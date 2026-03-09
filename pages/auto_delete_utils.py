"""
Utility functions for automatic order and sales data deletion.
These functions check the current date and trigger deletions based on scheduled dates.
"""

from django.utils import timezone
from datetime import datetime, date, timedelta
from decimal import Decimal
import json

from pages.models import Order, Payment, Customer, Product, StockAlert, MonthlySalesArchive, YearlySalesSnapshot
from django.db.models import Sum, Count, Q


def check_and_delete_completed_orders():
    """
    Check if today is the first day of the month.
    If so, delete all completed orders from the previous month and archive their sales data.
    """
    today = timezone.now().date()
    
    # Only run on the first day of the month
    if today.day != 1:
        return False
    
    # Get previous month's range
    first_day_current = today.replace(day=1)
    last_day_prev_month = first_day_current - timedelta(days=1)
    first_day_prev_month = last_day_prev_month.replace(day=1)
    
    prev_month_name = last_day_prev_month.strftime('%B')
    prev_month_year = last_day_prev_month.year
    
    # Get completed orders from previous month
    completed_orders = Order.objects.filter(
        status='completed',
        created_at__date__gte=first_day_prev_month,
        created_at__date__lte=last_day_prev_month
    ).select_related('customer')
    
    if not completed_orders.exists():
        return False
    
    # Archive the sales data
    archive_monthly_sales_data(completed_orders, prev_month_name, prev_month_year)
    
    # Delete the orders
    deleted_count = completed_orders.count()
    completed_orders.delete()
    
    return True, deleted_count, prev_month_name


def check_and_delete_yearly_sales_data():
    """
    Check if today is January 1st.
    If so, delete all yearly sales snapshots and monthly archives from the previous year.
    """
    today = timezone.now().date()
    
    # Only run on January 1st
    if not (today.month == 1 and today.day == 1):
        return False
    
    prev_year = today.year - 1
    
    # Create a yearly snapshot before deletion
    create_yearly_snapshot(prev_year)
    
    # Delete monthly archives from previous year
    deleted_count, _ = MonthlySalesArchive.objects.filter(year=prev_year).delete()
    
    return True, deleted_count, prev_year


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
        order_date = timezone.localtime(order.created_at).date() if hasattr(order.created_at, 'date') else order.created_at
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
    
    # Calculate total sales for the month
    total_sales = sum(daily_totals.values())
    
    # Save to MonthlySalesArchive (or update if already exists)
    MonthlySalesArchive.objects.update_or_create(
        month_name=month_name,
        year=year,
        defaults={
            'sales_by_day': daily_totals,
            'orders_by_day': daily_orders_list,
            'total_sales': Decimal(str(total_sales))
        }
    )


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
    today = timezone.now().date()

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
    Get the date when completed orders will be deleted (first day of next month).
    """
    today = timezone.now().date()
    
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
