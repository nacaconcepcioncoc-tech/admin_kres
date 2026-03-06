from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import datetime, date, timedelta
from decimal import Decimal
import json
from pages.models import Order, Customer, Payment, Product, StockAlert, MonthlySalesArchive, YearlySalesSnapshot
from django.db.models import Sum, Count, F, DecimalField


class Command(BaseCommand):
    help = 'Automatically delete completed orders, orphaned customers, and related payments on the first day of each month and yearly sales data on January 1'

    def handle(self, *args, **options):
        today = timezone.now().date()
        
        # Check if today is the first day of the month
        if today.day == 1:
            self.delete_previous_month_completed_orders()
            self.delete_orphaned_customers()
            self.delete_completed_payments()
            self.reset_monthly_stock()
        
        # Check if today is January 1st
        if today.month == 1 and today.day == 1:
            self.delete_yearly_sales_data()
        
        self.stdout.write(self.style.SUCCESS('Auto-deletion check completed'))

    def delete_orphaned_customers(self):
        """Delete customers who have no remaining orders."""
        orphaned = Customer.objects.annotate(
            order_count=Count('orders')
        ).filter(order_count=0)

        if not orphaned.exists():
            self.stdout.write(self.style.SUCCESS('No orphaned customers to delete'))
            return

        count = orphaned.count()
        orphaned.delete()
        self.stdout.write(self.style.SUCCESS(f'Deleted {count} orphaned customers'))

    def delete_completed_payments(self):
        """Delete payments linked to completed orders from the previous month."""
        today = timezone.now().date()
        first_day_current = today.replace(day=1)
        last_day_prev = first_day_current - timedelta(days=1)
        first_day_prev = last_day_prev.replace(day=1)

        completed_payments = Payment.objects.filter(
            order__status='completed',
            order__created_at__date__gte=first_day_prev,
            order__created_at__date__lte=last_day_prev
        )

        if not completed_payments.exists():
            self.stdout.write(self.style.SUCCESS('No completed payments to delete'))
            return

        count = completed_payments.count()
        completed_payments.delete()
        self.stdout.write(self.style.SUCCESS(f'Deleted {count} completed payments'))

    def reset_monthly_stock(self):
        """Reset all product stock quantities to 0 for the new month."""
        count = Product.objects.filter(is_active=True).update(stock_quantity=0)
        StockAlert.objects.filter(alert_status='active').update(
            alert_status='resolved',
            resolved_at=timezone.now()
        )
        self.stdout.write(self.style.SUCCESS(f'Reset stock quantities for {count} products'))

    def delete_previous_month_completed_orders(self):
        """
        Delete all completed orders from the previous month.
        Archive sales data before deletion.
        """
        today = timezone.now().date()
        
        # Get previous month
        first_day_current_month = today.replace(day=1)
        last_day_prev_month = first_day_current_month - timedelta(days=1)
        
        prev_month_name = last_day_prev_month.strftime('%B')  # 'January', 'February', etc.
        prev_month_num = last_day_prev_month.month
        prev_month_year = last_day_prev_month.year
        
        # Get first day of previous month
        first_day_prev_month = last_day_prev_month.replace(day=1)
        
        # Get all completed orders from previous month
        completed_orders = Order.objects.filter(
            status='completed',
            created_at__date__gte=first_day_prev_month,
            created_at__date__lte=last_day_prev_month
        ).select_related('customer')

        if not completed_orders.exists():
            self.stdout.write(self.style.SUCCESS(f'No completed orders found for {prev_month_name}'))
            return

        # Archive the sales data before deletion
        try:
            self.archive_monthly_sales_data(completed_orders, prev_month_name, prev_month_year)
            
            # Delete the completed orders
            order_count = completed_orders.count()
            completed_orders.delete()
            
            self.stdout.write(
                self.style.SUCCESS(
                    f'Successfully deleted {order_count} completed orders from {prev_month_name} {prev_month_year}'
                )
            )
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'Error archiving/deleting orders: {str(e)}')
            )

    def archive_monthly_sales_data(self, orders, month_name, year):
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
        
        self.stdout.write(
            self.style.SUCCESS(f'Archived sales data for {month_name} {year}: ${total_sales:.2f}')
        )

    def delete_yearly_sales_data(self):
        """
        Delete all sales data from the previous year.
        First, create a yearly snapshot to preserve history.
        """
        today = timezone.now().date()
        prev_year = today.year - 1
        
        try:
            # Create a yearly snapshot with all the archived monthly data from the previous year
            self.create_yearly_snapshot(prev_year)
            
            # Delete all MonthlySalesArchive records from previous year
            deleted_count, _ = MonthlySalesArchive.objects.filter(year=prev_year).delete()
            
            self.stdout.write(
                self.style.SUCCESS(
                    f'Successfully deleted {deleted_count} monthly archives from year {prev_year}'
                )
            )
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'Error deleting yearly sales data: {str(e)}')
            )

    def create_yearly_snapshot(self, year):
        """
        Create a yearly snapshot from all MonthlySalesArchive records.
        This preserves the full year's data before annual reset.
        """
        archives = MonthlySalesArchive.objects.filter(year=year)
        
        if not archives.exists():
            self.stdout.write(self.style.WARNING(f'No archive data found for year {year}'))
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
        
        self.stdout.write(
            self.style.SUCCESS(f'Created yearly snapshot for {year}: ${total_yearly_sales:.2f}')
        )
