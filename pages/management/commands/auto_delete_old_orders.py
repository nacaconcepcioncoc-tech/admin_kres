from django.core.management.base import BaseCommand

from pages.auto_delete_utils import (
    check_and_delete_completed_orders,
    check_and_delete_employee_standing_yearly_data,
    check_and_delete_yearly_sales_data,
)
class Command(BaseCommand):
    help = (
        'Run the Manila-time monthly operational reset and January 1 Employee '
        'Standing reset. Safe to schedule daily.'
    )

    def handle(self, *args, **options):
        monthly = check_and_delete_completed_orders()
        yearly_sales = check_and_delete_yearly_sales_data()
        yearly = check_and_delete_employee_standing_yearly_data()

        if monthly:
            _, orders_deleted, customers_deleted, period = monthly
            self.stdout.write(self.style.SUCCESS(
                f'Monthly cleanup completed for {period}: {orders_deleted} completed '
                f'orders and {customers_deleted} orphaned customers deleted. Related '
                'items/payments were cascaded; non-completed orders were preserved.'
            ))
        else:
            self.stdout.write('No monthly reset is due, or this period already ran.')

        if yearly_sales:
            _, year, archives_deleted, total = yearly_sales
            self.stdout.write(self.style.SUCCESS(
                f'Yearly Reports archive reset completed for {year}: '
                f'{archives_deleted} monthly archives removed after preserving '
                f'the minimal yearly total ({total}).'
            ))
        else:
            self.stdout.write('No yearly Reports archive reset is due, or it already ran.')

        if yearly:
            _, evaluations, legacy, summaries, year = yearly
            self.stdout.write(self.style.SUCCESS(
                f'Employee Standing reset completed for {year}: {evaluations} evaluations, '
                f'{legacy} legacy records, and {summaries} summaries deleted. Profiles preserved.'
            ))
        else:
            self.stdout.write('No yearly Employee Standing reset is due.')
