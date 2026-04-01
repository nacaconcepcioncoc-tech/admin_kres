"""
Manila timezone utilities for order management.
Ensures consistent timezone handling across the application.
"""

from django.utils import timezone
from datetime import timedelta, date
import pytz


def get_manila_timezone():
    """Get Manila timezone object (UTC+8)"""
    return pytz.timezone('Asia/Manila')


def get_manila_today():
    """
    Get today's date in Manila timezone.
    
    Returns:
        datetime.date: Today's date according to Manila timezone
    """
    manila_tz = get_manila_timezone()
    return timezone.now().astimezone(manila_tz).date()


def get_manila_now():
    """
    Get current datetime in Manila timezone.
    
    Returns:
        datetime.datetime: Current datetime according to Manila timezone
    """
    manila_tz = get_manila_timezone()
    return timezone.now().astimezone(manila_tz)


def is_delivery_tomorrow(delivery_date):
    """
    Check if a delivery_date is tomorrow based on Manila timezone.
    
    Args:
        delivery_date: A date object (DateField) to check
    
    Returns:
        bool: True if the delivery_date equals tomorrow in Manila timezone
    """
    if not delivery_date or not isinstance(delivery_date, date):
        return False
    
    manila_today = get_manila_today()
    tomorrow = manila_today + timedelta(days=1)
    return delivery_date == tomorrow


def is_delivery_today(delivery_date):
    """
    Check if a delivery_date is today based on Manila timezone.
    
    Args:
        delivery_date: A date object (DateField) to check
    
    Returns:
        bool: True if the delivery_date equals today in Manila timezone
    """
    if not delivery_date or not isinstance(delivery_date, date):
        return False
    
    manila_today = get_manila_today()
    return delivery_date == manila_today


def get_delivery_date_note(delivery_date):
    """
    Get a human-readable note about when a delivery is scheduled.
    
    Args:
        delivery_date: A date object (DateField)
    
    Returns:
        str: Description like "Today", "Tomorrow", or the date
    """
    if not delivery_date or not isinstance(delivery_date, date):
        return "Not scheduled"
    
    manila_today = get_manila_today()
    
    if delivery_date == manila_today:
        return "Today"
    elif delivery_date == manila_today + timedelta(days=1):
        return "Tomorrow"
    else:
        return delivery_date.strftime('%B %d, %Y')
