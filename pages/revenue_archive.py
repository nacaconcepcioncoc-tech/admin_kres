"""Lightweight, payment-ledger-based aggregation for Reports archives."""

from collections import defaultdict
from decimal import Decimal

from django.utils import timezone

from .manila_tz_utils import get_manila_timezone


MONEY_PLACES = Decimal('0.01')


def aggregate_payments_by_manila_month(payments):
    """Group payments into month/day/customer totals without storing identifiers."""
    manila_tz = get_manila_timezone()
    grouped = defaultdict(lambda: defaultdict(lambda: defaultdict(Decimal)))
    names = {}

    for payment in payments:
        received_at = timezone.localtime(payment.payment_date, manila_tz)
        month_key = (received_at.year, received_at.month)
        day_key = str(received_at.day)
        customer_id = payment.order.customer_id
        customer_key = str(customer_id) if customer_id is not None else payment.order.customer_name
        names[(month_key, day_key, customer_key)] = payment.order.customer_name
        grouped[month_key][day_key][customer_key] += Decimal(
            payment.amount or 0
        ).quantize(MONEY_PLACES)

    result = {}
    for month_key, days in grouped.items():
        customer_rows = {}
        daily_totals = {}
        for day_key, customers in days.items():
            rows = [
                {
                    'customer_name': names[(month_key, day_key, customer_key)],
                    'total': float(amount.quantize(MONEY_PLACES)),
                }
                for customer_key, amount in sorted(
                    customers.items(),
                    key=lambda item: names[(month_key, day_key, item[0])].casefold(),
                )
            ]
            customer_rows[day_key] = rows
            daily_totals[day_key] = float(sum(customers.values(), Decimal('0.00')))
        result[month_key] = {
            'sales_by_day': daily_totals,
            'customers_by_day': customer_rows,
        }
    return result


def merge_lightweight_archive(existing_rows, new_rows):
    """Merge day/customer totals and normalize any earlier detailed row shape."""
    merged = defaultdict(lambda: defaultdict(Decimal))

    for source in (existing_rows or {}, new_rows or {}):
        for day_key, rows in source.items():
            for row in rows or []:
                customer_name = str(row.get('customer_name') or 'Unnamed Customer').strip()
                amount = Decimal(str(row.get('total', row.get('amount', 0)) or 0))
                merged[str(day_key)][customer_name] += amount.quantize(MONEY_PLACES)

    normalized_rows = {}
    daily_totals = {}
    for day_key, customers in merged.items():
        normalized_rows[day_key] = [
            {'customer_name': name, 'total': float(amount.quantize(MONEY_PLACES))}
            for name, amount in sorted(customers.items(), key=lambda item: item[0].casefold())
        ]
        daily_totals[day_key] = float(sum(customers.values(), Decimal('0.00')))

    return normalized_rows, daily_totals
