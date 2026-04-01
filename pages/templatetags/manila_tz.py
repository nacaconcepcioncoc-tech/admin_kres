from django import template
from django.utils import timezone
from datetime import date, timedelta
from ..manila_tz_utils import (
    get_manila_timezone, 
    get_manila_today, 
    is_delivery_tomorrow, 
    is_delivery_today
)

register = template.Library()


@register.filter
def is_delivery_tomorrow_filter(delivery_date):
    """
    Check if a delivery date is tomorrow in Manila timezone
    Usage: {% if order.delivery_date|is_delivery_tomorrow_filter %}...{% endif %}
    """
    return is_delivery_tomorrow(delivery_date)


@register.filter
def is_delivery_today_filter(delivery_date):
    """
    Check if a delivery date is today in Manila timezone
    Usage: {% if order.delivery_date|is_delivery_today_filter %}...{% endif %}
    """
    return is_delivery_today(delivery_date)


@register.simple_tag
def manila_current_date():
    """
    Get current date in Manila timezone
    Usage: {% manila_current_date as manila_today %}
    """
    return get_manila_today()


@register.simple_tag
def manila_tomorrow_date():
    """
    Get tomorrow's date in Manila timezone
    Usage: {% manila_tomorrow_date as manila_tomorrow %}
    """
    manila_today = get_manila_today()
    return manila_today + timedelta(days=1)
