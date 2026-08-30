"""
Utility functions for automatic order and sales data deletion.
These functions check the current date and trigger deletions based on scheduled dates.
"""

from django.utils import timezone
from datetime import datetime, date, timedelta
from decimal import Decimal
import calendar
import json
import logging

from pages.models import (
    Customer,
    Employee,
    EmployeeMonthlyPerformance,
    MonthlyCleanupRun,
    MonthlyPerformanceSummary,
    MonthlySalesArchive,
    Order,
    Payment,
    PerformanceRecord,
    YearlySalesSnapshot,
)
from pages.manila_tz_utils import get_manila_today, get_manila_timezone
from pages.revenue_archive import (
    aggregate_payments_by_manila_month,
    merge_lightweight_archive,
)
from django.db.models import Sum, Count, Q, Prefetch
from django.db import transaction
from django.db import connection


logger = logging.getLogger(__name__)



def get_manila_month_bounds(reference_date=None):
    """Return Manila calendar-month date and aware-datetime boundaries."""
    reference_date = reference_date or get_manila_today()
    month_start = reference_date.replace(day=1)
    if month_start.month == 12:
        next_month_start = month_start.replace(
            year=month_start.year + 1,
            month=1,
        )
    else:
        next_month_start = month_start.replace(month=month_start.month + 1)

    manila_tz = get_manila_timezone()
    month_start_datetime = timezone.make_aware(
        datetime.combine(month_start, datetime.min.time()),
        manila_tz,
    )
    next_month_start_datetime = timezone.make_aware(
        datetime.combine(next_month_start, datetime.min.time()),
        manila_tz,
    )
    return (
        month_start,
        next_month_start,
        month_start_datetime,
        next_month_start_datetime,
    )


def get_completed_orders_for_month(reference_date=None):
    """
    Return completed orders assigned to one Manila calendar month.

    Delivery date is the primary month basis. Orders without a delivery date
    use their latest update timestamp, matching the monthly reset and every
    current-month summary across Dashboard, Orders, Payments, and Reports.
    """
    (
        month_start,
        next_month_start,
        month_start_datetime,
        next_month_start_datetime,
    ) = get_manila_month_bounds(reference_date)

    return Order.objects.filter(status='completed').filter(
        Q(
            delivery_date__gte=month_start,
            delivery_date__lt=next_month_start,
        )
        | Q(
            delivery_date__isnull=True,
            updated_at__gte=month_start_datetime,
            updated_at__lt=next_month_start_datetime,
        )
    )


def get_completed_order_month_date(order):
    """Return the Manila calendar date used to group a completed order."""
    if order.delivery_date:
        return order.delivery_date
    return timezone.localtime(order.updated_at).date()


def has_valid_fully_paid_cleanup_structure(order):
    """Return True only for a ledger-settled order supported by the two-payment flow."""
    payments = list(getattr(order, 'cleanup_payments', []))
    if not payments or any(payment.payment_type is None for payment in payments):
        return False

    order_total = Decimal(order.total or 0).quantize(Decimal('0.01'))
    if order_total <= 0 or any(Decimal(payment.amount or 0) <= 0 for payment in payments):
        return False

    received_total = sum(
        (Decimal(payment.amount or 0) for payment in payments),
        Decimal('0.00'),
    )
    calculated_remaining = max(order_total - received_total, Decimal('0.00'))
    if calculated_remaining != Decimal('0.00'):
        return False

    if len(payments) == 1:
        payment = payments[0]
        return (
            payment.payment_type == Payment.TYPE_FULL_PAYMENT
            and payment.payment_status == Payment.STATUS_FULLY_PAID
            and Decimal(payment.amount) == order_total
        )

    if len(payments) == 2:
        payments_by_type = {payment.payment_type: payment for payment in payments}
        if set(payments_by_type) != {
            Payment.TYPE_DOWN_PAYMENT,
            Payment.TYPE_BALANCE_PAYMENT,
        }:
            return False
        down_payment = payments_by_type[Payment.TYPE_DOWN_PAYMENT]
        balance_payment = payments_by_type[Payment.TYPE_BALANCE_PAYMENT]
        return (
            Decimal(down_payment.amount) < order_total
            and Decimal(balance_payment.amount) >= order_total - Decimal(down_payment.amount)
            and down_payment.payment_status
            == Payment.STATUS_DOWN_PAYMENT
            and balance_payment.payment_status == Payment.STATUS_FULLY_PAID
        )

    return False


def check_and_delete_employee_standing_yearly_data():
    """Delete prior-year Employee Standing records on January 1 in Manila.

    The operation is naturally idempotent: once the previous year's rows are
    deleted, later calls on the same day find nothing. Employee profiles and
    all unrelated operational records are preserved.
    """
    today = get_manila_today()
    if today.month != 1 or today.day != 1:
        return False

    previous_year = today.year - 1
    with transaction.atomic():
        monthly_evaluations = EmployeeMonthlyPerformance.objects.filter(year=previous_year)
        legacy_records = PerformanceRecord.objects.filter(record_date__year=previous_year)
        monthly_summaries = MonthlyPerformanceSummary.objects.filter(month__year=previous_year)
        deleted_count = monthly_evaluations.count()
        legacy_deleted_count = legacy_records.count()
        summary_deleted_count = monthly_summaries.count()
        monthly_evaluations.delete()
        legacy_records.delete()
        monthly_summaries.delete()

        # Recalculate stored totals without deleting or replacing profiles.
        for employee in Employee.objects.select_for_update().all():
            employee.recalculate_yearly_evaluation_summary(year=today.year)

    return True, deleted_count, legacy_deleted_count, summary_deleted_count, previous_year

def get_previous_cleanup_month(reference_date=None):
    """Return the first day of the last completed Manila calendar month."""
    today = reference_date or get_manila_today()
    return (today.replace(day=1) - timedelta(days=1)).replace(day=1)


def _lock_cleanup_month(month):
    """Serialize one cleanup month on PostgreSQL before its unique row exists."""
    if connection.vendor == 'postgresql':
        lock_key = (month.year * 100) + month.month
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_advisory_xact_lock(%s)', [lock_key])


def check_and_delete_completed_orders():
    """Archive and delete only the previous month's completed operations.

    The scheduler may invoke this command daily. It processes the last completed
    Manila month once, including a later catch-up when the first-day trigger was
    missed. Pending, processing, cancelled, and current-month orders are never
    selected.
    """
    previous_month_start = get_previous_cleanup_month()

    with transaction.atomic():
        _lock_cleanup_month(previous_month_start)
        if MonthlyCleanupRun.objects.select_for_update().filter(
            month=previous_month_start
        ).exists():
            logger.info(
                'Monthly cleanup already processed for %s',
                previous_month_start.strftime('%B %Y'),
            )
            return False

        # Creating this before selection makes an empty run durable/idempotent.
        cleanup_run = MonthlyCleanupRun.objects.create(month=previous_month_start)
        candidate_orders = list(
            get_completed_orders_for_month(previous_month_start)
            .select_for_update()
            .select_related('customer')
            .prefetch_related(
                Prefetch(
                    'payments',
                    queryset=Payment.objects.select_for_update().order_by(
                        'payment_date', 'payment_id'
                    ),
                    to_attr='cleanup_payments',
                )
            )
            .order_by('delivery_date', 'updated_at', 'order_id')
        )
        completed_orders = [
            order for order in candidate_orders
            if has_valid_fully_paid_cleanup_structure(order)
        ]
        order_count = len(completed_orders)
        customer_ids = {order.customer_id for order in completed_orders}

        if completed_orders:
            archive_monthly_sales_data(
                completed_orders,
                previous_month_start.strftime('%B'),
                previous_month_start.year,
            )
            order_ids = [order.order_id for order in completed_orders]
            Order.objects.filter(
                order_id__in=order_ids,
                status='completed',
            ).delete()  # Django's collector deletes items/payments before orders.

        orphaned_customers = Customer.objects.filter(
            customer_id__in=customer_ids,
            orders__isnull=True,
        )
        customer_count = orphaned_customers.count()
        orphaned_customers.delete()

        cleanup_run.orders_deleted = order_count
        cleanup_run.performance_records_deleted = 0
        cleanup_run.save(update_fields=['orders_deleted', 'performance_records_deleted'])

    period = previous_month_start.strftime('%B %Y')
    logger.info(
        'Monthly cleanup processed for %s: orders_deleted=%s customers_deleted=%s',
        period,
        order_count,
        customer_count,
    )
    return True, order_count, customer_count, period


def check_and_delete_yearly_sales_data():
    """Prune the prior year's detailed Reports archive once during January."""
    today = get_manila_today()
    if today.month != 1:
        return False

    previous_year = today.year - 1
    with transaction.atomic():
        _lock_cleanup_month(date(previous_year, 12, 31))
        archives = MonthlySalesArchive.objects.select_for_update().filter(
            year=previous_year
        )
        snapshot = YearlySalesSnapshot.objects.select_for_update().filter(
            year=previous_year
        ).first()
        if snapshot and not archives.exists():
            return False

        manila_tz = get_manila_timezone()
        year_start = timezone.make_aware(datetime(previous_year, 1, 1), manila_tz)
        next_year_start = timezone.make_aware(datetime(today.year, 1, 1), manila_tz)
        archived_total = archives.aggregate(total=Sum('total_sales'))['total'] or Decimal('0.00')
        retained_payment_total = (
            Payment.objects.filter(
                payment_date__gte=year_start,
                payment_date__lt=next_year_start,
            )
            .exclude(payment_status__in=('failed', 'refunded'))
            .aggregate(total=Sum('amount'))['total']
            or Decimal('0.00')
        )
        total_yearly_sales = archived_total + retained_payment_total
        YearlySalesSnapshot.objects.update_or_create(
            year=previous_year,
            defaults={
                'calendar_data': {},
                'all_months_archive': {},
                'total_yearly_sales': total_yearly_sales,
            },
        )
        archive_count = archives.count()
        archives.delete()

    logger.info(
        'Yearly Reports archive pruned for %s: months_deleted=%s',
        previous_year,
        archive_count,
    )
    return True, previous_year, archive_count, total_yearly_sales


def archive_monthly_sales_data(orders, month_name, year):
    """
    Archive received-payment ledger rows before their completed orders are deleted.

    Revenue remains in the month/day in which each payment was received, even
    when the related order is completed and cleaned up in a later month.
    """
    order_ids = [order.order_id for order in orders]
    received_payments = (
        Payment.objects.filter(order_id__in=order_ids)
        .exclude(payment_status__in=('failed', 'refunded'))
        .select_related('order', 'order__customer')
        .order_by('payment_date', 'payment_id')
    )
    payments_by_month = aggregate_payments_by_manila_month(received_payments)

    # Preserve the prior behavior of creating the cleanup month's archive even
    # when no received rows are present.
    fallback_month_number = datetime.strptime(month_name, '%B').month
    payments_by_month.setdefault(
        (year, fallback_month_number),
        {'sales_by_day': {}, 'customers_by_day': {}},
    )

    for (archive_year, archive_month_number), monthly_data in payments_by_month.items():
        archive_month = calendar.month_name[archive_month_number]
        archive, _ = MonthlySalesArchive.objects.select_for_update().get_or_create(
            month_name=archive_month,
            year=archive_year,
            defaults={'sales_by_day': {}, 'orders_by_day': {}, 'total_sales': 0},
        )
        merged_rows, merged_sales = merge_lightweight_archive(
            archive.orders_by_day,
            monthly_data['customers_by_day'],
        )
        archive.orders_by_day = merged_rows
        archive.sales_by_day = merged_sales
        archive.total_sales = Decimal(str(sum(merged_sales.values())))
        archive.save(update_fields=['orders_by_day', 'sales_by_day', 'total_sales'])


def create_yearly_snapshot(year):
    """
    Preserve only the minimal yearly total; detailed daily rows are not copied.
    """
    archives = MonthlySalesArchive.objects.filter(year=year)
    
    if not archives.exists():
        return
    
    total_yearly_sales = archives.aggregate(total=Sum('total_sales'))['total'] or Decimal('0.00')
    
    # Save yearly snapshot
    YearlySalesSnapshot.objects.update_or_create(
        year=year,
        defaults={
            'calendar_data': {},
            'all_months_archive': {},
            'total_yearly_sales': total_yearly_sales
        }
    )


def check_and_delete_orphaned_customers():
    """
    Check if today is the first day of the month.
    If so, delete customers who have NO active/pending orders.
    Only deletes customers whose orders have ALL been completed (and deleted by order auto-delete)
    or who have zero orders remaining.
    Sales data is already preserved in MonthlySalesArchive from order archiving.
    """
    today = timezone.now().date()

    # Only run on the first day of the month
    if today.day != 1:
        return False

    # Find customers with no remaining orders at all
    # (their orders were already archived+deleted by check_and_delete_completed_orders)
    orphaned_customers = Customer.objects.annotate(
        active_order_count=Count('orders')
    ).filter(active_order_count=0)

    if not orphaned_customers.exists():
        return False

    deleted_count = orphaned_customers.count()
    orphaned_customers.delete()

    return True, deleted_count


def check_and_delete_completed_payments():
    """
    Check if today is the first day of the month.
    If so, delete payments whose associated orders have been completed and deleted.
    This runs AFTER check_and_delete_completed_orders so orphaned payments
    (whose orders were already deleted via CASCADE) are already gone.
    Additionally, delete any payments linked to completed orders from previous month.
    Sales data is already preserved in MonthlySalesArchive from order archiving.
    """
    today = get_manila_today()

    # Only run on the first day of the month
    if today.day != 1:
        return False

    # Get previous month's range
    first_day_current = today.replace(day=1)
    last_day_prev_month = first_day_current - timedelta(days=1)
    first_day_prev_month = last_day_prev_month.replace(day=1)

    # Delete payments linked to completed orders from the previous month
    completed_payments = Payment.objects.filter(
        order__status='completed',
        order__created_at__date__gte=first_day_prev_month,
        order__created_at__date__lte=last_day_prev_month
    )

    if not completed_payments.exists():
        return False

    deleted_count = completed_payments.count()
    completed_payments.delete()

    return True, deleted_count


def check_and_reset_monthly_stock():
    """
    Check if today is the first day of the month.
    If so, delete all active products and clear stock alerts
    to start a new inventory cycle. This allows admins to input fresh products every month.
    """
    today = timezone.now().date()

    # Only run on the first day of the month
    if today.day != 1:
        return False

    # Delete all active products
    deleted_count, _ = Product.objects.filter(is_active=True).delete()

    # Resolve all active stock alerts since products have been deleted
    StockAlert.objects.filter(alert_status='active').update(
        alert_status='resolved',
        resolved_at=timezone.now()
    )

    if deleted_count == 0:
        return False

    return True, deleted_count


def get_next_month_deletion_date():
    """
    Get the next Manila month boundary for the completed-order reset notice.
    """
    today = get_manila_today()
    
    # First day of next month
    if today.month == 12:
        next_month_first = date(today.year + 1, 1, 1)
    else:
        next_month_first = date(today.year, today.month + 1, 1)
    
    return next_month_first


def get_next_year_deletion_date():
    """
    Get the date when yearly sales data will be deleted (January 1 of next year).
    """
    today = timezone.now().date()
    next_year_jan_1 = date(today.year + 1, 1, 1)
    return next_year_jan_1
