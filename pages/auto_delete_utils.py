"""
Utility functions for automatic order and sales data deletion.
These functions check the current date and trigger deletions based on scheduled dates.
"""

from django.utils import timezone
from datetime import datetime, date, timedelta
from decimal import Decimal
import json

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
from django.db.models import Sum, Count, Q
from django.db import transaction



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

def check_and_delete_completed_orders():
    """Archive and delete only the previous month's completed operations.

    The scheduler may invoke this command daily, but this function mutates data
    only on Manila day 1 and records the period even when no orders qualified.
    Pending, processing, cancelled, and current-month orders are never selected.
    """
    today = get_manila_today()
    if today.day != 1:
        return False

    current_month_start = today.replace(day=1)
    previous_month_reference = current_month_start - timedelta(days=1)
    previous_month_start = previous_month_reference.replace(day=1)

    with transaction.atomic():
        if MonthlyCleanupRun.objects.select_for_update().filter(
            month=previous_month_start
        ).exists():
            return False

        # Creating this before selection makes an empty run durable/idempotent.
        cleanup_run = MonthlyCleanupRun.objects.create(month=previous_month_start)
        completed_orders = list(
            get_completed_orders_for_month(previous_month_start)
            .select_for_update()
            .select_related('customer')
            .order_by('delivery_date', 'updated_at', 'order_id')
        )
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
    return True, order_count, customer_count, period


def check_and_delete_yearly_sales_data():
    """
    Check if today is January 1st.
    If so, delete all yearly sales snapshots and monthly archives from the previous year.
    """
    # Historical archives are permanent reporting data and must be preserved.
    return False


def archive_monthly_sales_data(orders, month_name, year):
    """
    Archive monthly sales data from orders before they are deleted.
    Preserves sales records for the Sales Calendar.
    """
    month_names = ['January', 'February', 'March', 'April', 'May', 'June',
                   'July', 'August', 'September', 'October', 'November', 'December']
    
    month_num = month_names.index(month_name) + 1 if month_name in month_names else 1
    
    # Get the date range for this month/year
    first_day = date(year, month_num, 1)
    if month_num == 12:
        last_day = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        last_day = date(year, month_num + 1, 1) - timedelta(days=1)
    
    # Group sales by day
    daily_totals = {}
    daily_orders_list = {}
    
    for order in orders:
        order_date = get_completed_order_month_date(order)
        day = order_date.day
        day_str = str(day)
        
        if day_str not in daily_totals:
            daily_totals[day_str] = 0
            daily_orders_list[day_str] = []
        
        daily_totals[day_str] += float(order.total or 0)
        daily_orders_list[day_str].append({
            'order_number': order.order_number,
            'customer_name': f"{order.customer.first_name} {order.customer.last_name}",
            'total': float(order.total or 0),
            'order_id': order.order_id,
            'order_date': order_date.isoformat()
        })
    
    # Merge into any earlier partial archive. Late-completed orders must not
    # overwrite orders that were already archived for the same month.
    archive, _ = MonthlySalesArchive.objects.select_for_update().get_or_create(
        month_name=month_name,
        year=year,
        defaults={'sales_by_day': {}, 'orders_by_day': {}, 'total_sales': 0},
    )
    merged_orders = dict(archive.orders_by_day or {})
    for day_str, new_orders in daily_orders_list.items():
        existing_orders = list(merged_orders.get(day_str, []))
        existing_ids = {
            str(item.get('order_id'))
            for item in existing_orders
            if item.get('order_id') is not None
        }
        existing_orders.extend(
            item for item in new_orders
            if str(item.get('order_id')) not in existing_ids
        )
        merged_orders[day_str] = existing_orders

    merged_sales = {
        day_str: sum(float(item.get('total') or 0) for item in day_orders)
        for day_str, day_orders in merged_orders.items()
    }
    archive.orders_by_day = merged_orders
    archive.sales_by_day = merged_sales
    archive.total_sales = Decimal(str(sum(merged_sales.values())))
    archive.save(update_fields=['orders_by_day', 'sales_by_day', 'total_sales'])


def create_yearly_snapshot(year):
    """
    Create a yearly snapshot from all MonthlySalesArchive records.
    This preserves the full year's data before annual reset.
    """
    archives = MonthlySalesArchive.objects.filter(year=year)
    
    if not archives.exists():
        return
    
    # Combine all monthly data
    all_months_data = {}
    calendar_data = {}
    total_yearly_sales = Decimal('0.00')
    
    for archive in archives:
        month_name = archive.month_name
        all_months_data[month_name] = {
            'sales_by_day': archive.sales_by_day,
            'orders_by_day': archive.orders_by_day,
            'total_sales': float(archive.total_sales)
        }
        calendar_data[month_name] = archive.sales_by_day
        total_yearly_sales += archive.total_sales
    
    # Save yearly snapshot
    YearlySalesSnapshot.objects.update_or_create(
        year=year,
        defaults={
            'calendar_data': calendar_data,
            'all_months_archive': all_months_data,
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
