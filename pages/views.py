from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.db.models import Sum, Count, Q, F, OuterRef, Subquery, Case, When, Value, IntegerField, Prefetch
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.http import JsonResponse, HttpResponseForbidden
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.cache import never_cache
from django.urls import reverse
from django.conf import settings
from django.db import transaction, connection
from django.db.utils import OperationalError, ProgrammingError
from datetime import timedelta, datetime
from functools import wraps
import calendar
import hmac
from decimal import Decimal, InvalidOperation
import json
import logging
import re
from urllib.parse import urlencode
from .models import Customer, Product, Order, OrderItem, Payment, StockAlert, MonthlySalesArchive, YearlySalesSnapshot
from .models import Employee, PerformanceRecord, EmployeeMonthlyPerformance, EmployeeStandingPin, MonthlyCleanupRun
from .payment_state import (
    STATE_DOWN_PAYMENT,
    STATE_FULLY_PAID,
    calculate_order_payment_display_state,
    calculate_order_payment_state,
)
from .manila_tz_utils import get_manila_timezone, get_manila_today, get_manila_now, is_delivery_tomorrow
from .auto_delete_utils import (
    check_and_delete_completed_orders,
    check_and_delete_employee_standing_yearly_data,
    check_and_delete_yearly_sales_data,
    get_previous_cleanup_month,
    get_next_month_deletion_date,
    get_next_year_deletion_date
)
from .revenue_archive import (
    aggregate_payments_by_manila_month,
    merge_lightweight_archive,
)


logger = logging.getLogger(__name__)


MONEY_PATTERN = re.compile(r'^\d{1,8}(?:\.\d{1,2})?$')
MONEY_PLACES = Decimal('0.01')


@csrf_exempt
@require_http_methods(["POST"])
def scheduled_monthly_cleanup(request):
    """Secret-authenticated scheduler hook for the existing cleanup helper."""
    logger.info('Scheduled monthly cleanup trigger received')

    if not settings.DEBUG and not request.is_secure():
        logger.warning('Scheduled monthly cleanup rejected: insecure request')
        return JsonResponse({'success': False, 'message': 'HTTPS is required.'}, status=400)

    expected_secret = settings.MONTHLY_CLEANUP_SECRET
    if len(expected_secret) < 32:
        logger.error('Scheduled monthly cleanup failed: strong secret is not configured')
        return JsonResponse(
            {'success': False, 'message': 'Cleanup trigger is not configured.'},
            status=503,
        )

    authorization = request.headers.get('Authorization', '')
    scheme, separator, supplied_secret = authorization.partition(' ')
    valid_secret = (
        separator == ' '
        and scheme.lower() == 'bearer'
        and hmac.compare_digest(supplied_secret, expected_secret)
    )
    if not valid_secret:
        logger.warning('Scheduled monthly cleanup rejected: invalid credentials')
        return JsonResponse({'success': False, 'message': 'Forbidden.'}, status=403)

    cleanup_month = get_previous_cleanup_month()
    try:
        result = check_and_delete_completed_orders()
        yearly_reports = check_and_delete_yearly_sales_data()
        yearly_employee = check_and_delete_employee_standing_yearly_data()
    except Exception:
        logger.exception('Scheduled monthly cleanup failed for %s', cleanup_month)
        return JsonResponse({'success': False, 'message': 'Cleanup failed.'}, status=500)

    if not result:
        run = MonthlyCleanupRun.objects.filter(month=cleanup_month).first()
        logger.info('Scheduled monthly cleanup already processed for %s', cleanup_month)
        return JsonResponse({
            'success': True,
            'status': 'already_processed',
            'month': cleanup_month.isoformat(),
            'orders_deleted': run.orders_deleted if run else 0,
            'yearly_reports': 'processed' if yearly_reports else 'not_due_or_already_processed',
            'yearly_employee': 'processed' if yearly_employee else 'not_due_or_already_processed',
        })

    _, orders_deleted, customers_deleted, period = result
    return JsonResponse({
        'success': True,
        'status': 'processed',
        'month': cleanup_month.isoformat(),
        'period': period,
        'orders_deleted': orders_deleted,
        'customers_deleted': customers_deleted,
        'yearly_reports': 'processed' if yearly_reports else 'not_due_or_already_processed',
        'yearly_employee': 'processed' if yearly_employee else 'not_due_or_already_processed',
    })


def _parse_money(value, field_name, *, allow_zero=True):
    """Parse a plain decimal currency string without accepting floats/exponents."""
    raw_value = str(value if value is not None else '').strip()
    if not MONEY_PATTERN.fullmatch(raw_value):
        raise ValueError(f'{field_name} must be a valid amount with at most two decimal places.')
    amount = Decimal(raw_value).quantize(MONEY_PLACES)
    if amount < 0 or (not allow_zero and amount == 0):
        qualifier = 'greater than zero' if not allow_zero else 'zero or greater'
        raise ValueError(f'{field_name} must be {qualifier}.')
    return amount


def _requested_initial_payment_type(data):
    """Map the current UI values and the new API values to one payment type."""
    requested = str(
        data.get('payment_type')
        or data.get('payment_mode')
        or data.get('payment_status')
        or ''
    ).strip().lower()
    if requested in {'full_payment', 'fully_paid', 'completed'}:
        return Payment.TYPE_FULL_PAYMENT
    if requested in {'down_payment', 'pending'}:
        return Payment.TYPE_DOWN_PAYMENT
    raise ValueError('Payment mode must be Full Payment or Down Payment.')


def _money(value):
    """Normalize a database Decimal to the system's two currency places."""
    return Decimal(value or 0).quantize(MONEY_PLACES)


def _is_pickup_order(order):
    """Delivery type is stored by the existing order-note prefix."""
    return str(order.notes or '').lstrip().upper().startswith('[PICK UP]')


def _order_by_fifo_schedule(queryset):
    """Order scheduled work FIFO, with pickup winning exact schedule ties."""
    return (
        queryset
        .annotate(
            delivery_type_priority=Case(
                When(notes__istartswith='[PICK UP]', then=Value(0)),
                default=Value(1),
                output_field=IntegerField(),
            )
        )
        .order_by(
            F('delivery_date').asc(nulls_last=True),
            F('delivery_time').asc(nulls_last=True),
            'delivery_type_priority',
            'order_id',
        )
    )


def _received_payment_transactions():
    """Ledger rows that represent money received, including readable legacy rows."""
    return Payment.objects.exclude(payment_status__in=('failed', 'refunded'))


def _pending_payment_count():
    orders = Order.objects.prefetch_related('payments').only(
        'order_id', 'total', 'balance_payment'
    )
    return sum(
        1 for order in orders
        if calculate_order_payment_display_state(
            order, order.payments.all()
        )['code'] == STATE_DOWN_PAYMENT
    )


def _manila_month_datetime_bounds(month_start):
    """Return aware Manila boundaries for the calendar month containing month_start."""
    if month_start.month == 12:
        next_month_start = month_start.replace(year=month_start.year + 1, month=1, day=1)
    else:
        next_month_start = month_start.replace(month=month_start.month + 1, day=1)
    manila_tz = get_manila_timezone()
    return (
        timezone.make_aware(datetime.combine(month_start, datetime.min.time()), manila_tz),
        timezone.make_aware(datetime.combine(next_month_start, datetime.min.time()), manila_tz),
    )



# ============================================================================
# EMPLOYEE STANDING PIN ACCESS CONTROL
# ============================================================================
EMPLOYEE_STANDING_PIN_SESSION_KEY = "employee_standing_pin_verified"
EMPLOYEE_STANDING_PIN_USER_SESSION_KEY = "employee_standing_pin_user_id"
EMPLOYEE_STANDING_PIN_ID_SESSION_KEY = "employee_standing_pin_id"
EMPLOYEE_STANDING_PIN_VERSION_SESSION_KEY = "employee_standing_pin_version"
EMPLOYEE_STANDING_PIN_FAILURES_SESSION_KEY = "employee_standing_pin_failures"
EMPLOYEE_STANDING_PIN_LOCKED_UNTIL_SESSION_KEY = "employee_standing_pin_locked_until"


def _active_employee_standing_pin():
    return EmployeeStandingPin.objects.filter(is_active=True).first()


def _employee_standing_pin_is_verified(request, configured_pin):
    """Return True only when the current authenticated admin verified this session."""
    return bool(
        configured_pin
        and request.user.is_authenticated
        and request.session.get(EMPLOYEE_STANDING_PIN_SESSION_KEY) is True
        and request.session.get(EMPLOYEE_STANDING_PIN_USER_SESSION_KEY) == request.user.pk
        and request.session.get(EMPLOYEE_STANDING_PIN_ID_SESSION_KEY) == configured_pin.pk
        and request.session.get(EMPLOYEE_STANDING_PIN_VERSION_SESSION_KEY) == configured_pin.updated_at.isoformat()
    )


def _clear_employee_standing_pin_verification(request):
    """Remove only the successful Employee Standing verification state."""
    for session_key in (
        EMPLOYEE_STANDING_PIN_SESSION_KEY,
        EMPLOYEE_STANDING_PIN_USER_SESSION_KEY,
        EMPLOYEE_STANDING_PIN_ID_SESSION_KEY,
        EMPLOYEE_STANDING_PIN_VERSION_SESSION_KEY,
    ):
        request.session.pop(session_key, None)
    request.session.modified = True


def employee_standing_pin_required(view_func):
    """Protect Employee Standing HTML and API routes from direct URL access."""
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"{reverse('pages:login')}?next={request.get_full_path()}")
        if not (request.user.is_staff or request.user.is_superuser):
            if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.path.startswith('/ajax/'):
                return JsonResponse({'success': False, 'message': 'Administrator access is required.'}, status=403)
            return HttpResponseForbidden('Administrator access is required.')
        try:
            configured_pin = _active_employee_standing_pin()
        except (OperationalError, ProgrammingError):
            configured_pin = None
        if not _employee_standing_pin_is_verified(request, configured_pin):
            verify_url = reverse('pages:employee_standing_pin')
            next_url = request.get_full_path()
            target = f"{verify_url}?{urlencode({'next': next_url})}"
            if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.path.startswith('/ajax/'):
                return JsonResponse({
                    'success': False,
                    'message': 'Employee Standing PIN verification is required.',
                    'verify_url': target,
                }, status=403)
            return redirect(target)
        return view_func(request, *args, **kwargs)
    return wrapped


@login_required(login_url='pages:login')
@require_http_methods(["POST"])
@csrf_protect
@never_cache
def employee_standing_lock(request):
    """Immediately lock Employee Standing without ending the Django login."""
    if not (request.user.is_staff or request.user.is_superuser):
        return HttpResponseForbidden('Administrator access is required.')

    _clear_employee_standing_pin_verification(request)
    return redirect('pages:dashboard')


@login_required(login_url='pages:login')
@require_http_methods(["GET", "POST"])
@csrf_protect
@never_cache
def employee_standing_pin(request):
    """Verify the server-side hashed PIN once for the current login session."""
    logger = logging.getLogger(__name__)

    if not (request.user.is_staff or request.user.is_superuser):
        return HttpResponseForbidden('Administrator access is required.')

    next_url = request.GET.get('next') or request.POST.get('next') or reverse('pages:employee_standing')
    if not url_has_allowed_host_and_scheme(
        url=next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        next_url = reverse('pages:employee_standing')

    try:
        configured_pin = _active_employee_standing_pin()
    except (OperationalError, ProgrammingError):
        configured_pin = None

    if not configured_pin:
        logger.error('No active Employee Standing PIN exists or its migration is not applied.')
        return render(request, 'employee_standing_pin.html', {
            'next': next_url,
            'error': 'Employee Standing PIN is not configured. Contact the system administrator.',
        }, status=503)

    if _employee_standing_pin_is_verified(request, configured_pin):
        return redirect(next_url)

    now = timezone.now()
    locked_until_raw = request.session.get(EMPLOYEE_STANDING_PIN_LOCKED_UNTIL_SESSION_KEY)
    locked_until = None
    if locked_until_raw:
        try:
            locked_until = datetime.fromisoformat(locked_until_raw)
            if timezone.is_naive(locked_until):
                locked_until = timezone.make_aware(locked_until, timezone.get_current_timezone())
        except (TypeError, ValueError):
            request.session.pop(EMPLOYEE_STANDING_PIN_LOCKED_UNTIL_SESSION_KEY, None)

    if locked_until and locked_until > now:
        remaining_seconds = max(1, int((locked_until - now).total_seconds()))
        return render(request, 'employee_standing_pin.html', {
            'next': next_url,
            'error': f'Too many incorrect attempts. Try again in {remaining_seconds // 60 + 1} minute(s).',
            'is_locked': True,
        }, status=429)

    if request.method == 'POST':
        submitted_pin = request.POST.get('pin', '').strip()

        if submitted_pin and configured_pin.check_pin(submitted_pin):
            request.session[EMPLOYEE_STANDING_PIN_SESSION_KEY] = True
            request.session[EMPLOYEE_STANDING_PIN_USER_SESSION_KEY] = request.user.pk
            request.session[EMPLOYEE_STANDING_PIN_ID_SESSION_KEY] = configured_pin.pk
            request.session[EMPLOYEE_STANDING_PIN_VERSION_SESSION_KEY] = configured_pin.updated_at.isoformat()
            request.session.pop(EMPLOYEE_STANDING_PIN_FAILURES_SESSION_KEY, None)
            request.session.pop(EMPLOYEE_STANDING_PIN_LOCKED_UNTIL_SESSION_KEY, None)
            request.session.modified = True
            return redirect(next_url)

        failures = int(request.session.get(EMPLOYEE_STANDING_PIN_FAILURES_SESSION_KEY, 0)) + 1
        request.session[EMPLOYEE_STANDING_PIN_FAILURES_SESSION_KEY] = failures
        logger.warning(
            'Failed Employee Standing PIN attempt: user_id=%s username=%s ip=%s attempt=%s',
            request.user.pk,
            request.user.get_username(),
            request.META.get('REMOTE_ADDR', 'unknown'),
            failures,
        )

        error = 'Incorrect PIN. Access denied.'
        status_code = 403
        if failures >= 5:
            locked_until = now + timedelta(minutes=5)
            request.session[EMPLOYEE_STANDING_PIN_LOCKED_UNTIL_SESSION_KEY] = locked_until.isoformat()
            request.session[EMPLOYEE_STANDING_PIN_FAILURES_SESSION_KEY] = 0
            error = 'Too many incorrect attempts. Try again in 5 minutes.'
            status_code = 429

        return render(request, 'employee_standing_pin.html', {
            'next': next_url,
            'error': error,
        }, status=status_code)

    return render(request, 'employee_standing_pin.html', {'next': next_url})



# ============================================================================
# ADMIN HELPER: CLEAR ALL TEST DATA
# ============================================================================
@login_required(login_url='login')
@require_http_methods(["POST"])
@csrf_protect
def clear_all_data(request):
    """Clear all customers, products, orders, payments for testing"""
    # Only allow this in development or for superusers
    if not request.user.is_superuser:
        return JsonResponse({'success': False, 'message': 'Unauthorized'}, status=403)
   
    # Delete all data
    Payment.objects.all().delete()
    OrderItem.objects.all().delete()
    Order.objects.all().delete()
    Customer.objects.all().delete()
    Product.objects.all().delete()
    StockAlert.objects.all().delete()
   
    messages.success(request, 'All data has been cleared successfully!')
    return redirect('pages:dashboard')




# ============================================================================
# LOGIN VIEW
# ============================================================================
@require_http_methods(["GET", "POST"])
@csrf_protect
def login_view(request):
    """Admin login view"""
    logger = logging.getLogger(__name__)
    wants_json = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    if request.user.is_authenticated:
        if wants_json:
            return JsonResponse({
                'success': True,
                'redirect_url': reverse('pages:dashboard'),
            })
        return redirect('pages:dashboard')
    if request.method == 'POST':
        username_or_email = request.POST.get('email')
        password = request.POST.get('password')
       
        # Try to authenticate with the provided username/email
        user = authenticate(request, username=username_or_email, password=password)
       
        # If authentication fails, try to find user by email and authenticate
        if user is None:
            from django.contrib.auth.models import User
            try:
                user_obj = User.objects.get(email=username_or_email)
                user = authenticate(request, username=user_obj.username, password=password)
            except User.DoesNotExist:
                logger.warning(f"User not found with email: {username_or_email}")
            except Exception as e:
                logger.error(f"Database error during login: {str(e)}", exc_info=True)
       
        if user is not None:
            login(request, user)
            remember_me = request.POST.get('remember') in {'on', 'true', '1', 'yes'}
            if remember_me:
                request.session['remember_me_auth'] = True
                request.session.set_expiry(settings.REMEMBER_ME_SESSION_AGE)
            else:
                # Expire at browser close when "Remember Me" is not selected.
                request.session.pop('remember_me_auth', None)
                request.session.set_expiry(0)

            next_page = request.GET.get('next', '')
            if next_page and url_has_allowed_host_and_scheme(
                url=next_page,
                allowed_hosts={request.get_host()},
                require_https=request.is_secure(),
            ):
                redirect_url = next_page
            else:
                redirect_url = reverse('pages:dashboard')
            if wants_json:
                return JsonResponse({
                    'success': True,
                    'redirect_url': redirect_url,
                })
            return redirect(redirect_url)
        else:
            error_message = 'Incorrect username or password'
            if wants_json:
                return JsonResponse({
                    'success': False,
                    'message': error_message,
                }, status=401)
            messages.error(request, error_message)
   
    return render(request, 'login.html')




# ============================================================================
# LOGOUT VIEW
# ============================================================================
@require_http_methods(["POST"])
@csrf_protect
def logout_view(request):
    """Admin logout view"""
    logout(request)
    return redirect('pages:login')




# ============================================================================
# DASHBOARD VIEW
# ============================================================================
@login_required(login_url='login')
def dashboard(request):
    """Dashboard statistics and tomorrow deliveries using Manila time."""

    # Catch up missed monthly resets before calculating dashboard totals.

    today = get_manila_today()
    tomorrow = today + timedelta(days=1)
    current_month_start = today.replace(day=1)

    if today.month == 12:
        next_month_start = today.replace(year=today.year + 1, month=1, day=1)
    else:
        next_month_start = today.replace(month=today.month + 1, day=1)

    manila_tz = get_manila_timezone()
    month_start_datetime = timezone.make_aware(
        datetime.combine(current_month_start, datetime.min.time()),
        manila_tz,
    )
    next_month_start_datetime = timezone.make_aware(
        datetime.combine(next_month_start, datetime.min.time()),
        manila_tz,
    )

    # Use the order's delivery month when available. Orders without a delivery
    # date use their creation month so every valid order is counted once.
    current_month_orders = Order.objects.filter(
        Q(
            delivery_date__gte=current_month_start,
            delivery_date__lt=next_month_start,
        )
        | Q(
            delivery_date__isnull=True,
            created_at__gte=month_start_datetime,
            created_at__lt=next_month_start_datetime,
        )
    )

    pending_orders = Order.objects.filter(status='pending').count()

    current_month_payments = _received_payment_transactions().filter(
        payment_date__gte=month_start_datetime,
        payment_date__lt=next_month_start_datetime,
    )
    total_revenue = current_month_payments.aggregate(
        total=Sum('amount')
    )['total'] or Decimal('0.00')

    completed_orders = current_month_orders.filter(
        status='completed'
    ).count()

    customers_transacted_this_month = current_month_payments.values(
        'order__customer_id'
    ).distinct().count()

    tomorrow_deliveries = (
        Order.objects.filter(delivery_date=tomorrow)
        .exclude(status='cancelled')
        .annotate(
            delivery_type_priority=Case(
                When(notes__startswith='[PICK UP]', then=Value(1)),
                default=Value(0),
                output_field=IntegerField(),
            )
        )
        .select_related('customer')
        .prefetch_related('items')
        .order_by(
            F('delivery_time').asc(nulls_last=True),
            'delivery_type_priority',
            'order_id',
        )
    )

    context = {
        'total_revenue': total_revenue,
        'pending_orders': pending_orders,
        'completed_orders': completed_orders,
        'customers_transacted_this_month': customers_transacted_this_month,
        'tomorrow_deliveries': tomorrow_deliveries,
        'new_customers_count': Customer.objects.filter(
            created_at__date=today
        ).count(),
        'pending_payments': _pending_payment_count(),
    }

    return render(request, 'dashboard.html', context)




# ============================================================================
# MONTHLY ORDER NUMBER HELPER
# ============================================================================
def _get_next_monthly_order_number():
    """
    Generate the next order number for the current Manila month.

    Stored format: ORD-0001-YYYYMM
    The visible sequence restarts from 1 each Manila calendar month, while
    YYYYMM keeps each stored order number unique across different months.
    """
    manila_now = get_manila_now()
    month_key = manila_now.strftime('%Y%m')

    # Prevent two simultaneous PostgreSQL requests from receiving the same
    # sequence number. The surrounding order-create view is transaction.atomic.
    if connection.vendor == 'postgresql':
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(%s)",
                [int(month_key)],
            )

    existing_numbers = Order.objects.filter(
        created_at__year=manila_now.year,
        created_at__month=manila_now.month,
    ).values_list('order_number', flat=True)

    highest_sequence = 0
    for existing_number in existing_numbers:
        try:
            sequence = int(str(existing_number).split('-')[1])
        except (IndexError, TypeError, ValueError):
            continue
        highest_sequence = max(highest_sequence, sequence)

    return f'ORD-{highest_sequence + 1:04d}-{month_key}'


# ============================================================================
# CUSTOMERS VIEWS - WITH AJAX SUPPORT
# ============================================================================
@login_required(login_url='login')
def customers(request):
    """List all customers from database with search and status filter"""
   

    # Keep Customers synchronized with Orders and Payments using the same
    # Manila-time monthly reset. Only completed orders from previous months
    # are affected; customer records and all non-completed orders remain.
    search_query = request.GET.get('search', '')
    status_filter = request.GET.get('status', '')
    profile_orders_queryset = (
        Order.objects
        .select_related('customer')
        .prefetch_related('items', 'payments')
        .order_by('-created_at', '-order_id')
    )

    customers_list = (
        Customer.objects.filter(orders__isnull=False)
        .prefetch_related(
            Prefetch(
                'orders',
                queryset=profile_orders_queryset,
                to_attr='profile_orders',
            )
        )
        .distinct()
    )

    latest_order_status_subquery = Order.objects.filter(
        customer=OuterRef('pk')
    ).order_by('-created_at', '-order_id').values('status')[:1]
    customers_list = customers_list.annotate(
        latest_order_status=Subquery(latest_order_status_subquery)
    )
   
    if search_query:
        customers_list = customers_list.filter(
            Q(first_name__icontains=search_query) |
            Q(last_name__icontains=search_query) |
            Q(email__icontains=search_query) |
            Q(phone__icontains=search_query)
        )
   
    # Filter by order status if specified
    if status_filter == 'pending':
        customers_list = customers_list.filter(latest_order_status='pending')
    elif status_filter == 'completed':
        customers_list = customers_list.filter(latest_order_status='completed')
   
    # Annotate with order count
    customers_list = customers_list.annotate(
        order_count=Count('orders')
    ).order_by('-created_at')

    # The sender is the actual customer/buyer. Keep the existing Customer
    # records and relationships intact, but expose sender identity consistently
    # on the Customers page using each record's latest order.
    for customer in customers_list:
        latest_order = customer.orders.order_by('-created_at').first()
        customer.display_customer_name = (
            latest_order.customer_name if latest_order else
            f"{customer.first_name} {customer.last_name}".strip()
        )
        customer.display_customer_phone = (
            latest_order.customer_contact if latest_order else customer.phone
        )
   
    # Notification data
    recent_orders = Order.objects.select_related('customer').order_by('-created_at')[:3]
    pending_payments = _pending_payment_count()
   
    # Get next month's deletion date for warning message
    next_deletion_date = get_next_month_deletion_date()

    context = {
        'customers': customers_list,
        'search_query': search_query,
        'status_filter': status_filter,
        'recent_orders': recent_orders,
        'pending_payments': pending_payments,
        'new_customers_count': Customer.objects.filter(created_at__date=get_manila_today()).count(),
        'next_deletion_date': next_deletion_date,
    }
   
    return render(request, 'customers.html', context)




@login_required(login_url='login')
@require_http_methods(["POST"])
@csrf_protect
def customer_create_ajax(request):
    """AJAX endpoint to create customer and return JSON"""
    try:
        # Parse JSON data
        data = json.loads(request.body)

        sender_is_receiver = data.get('sender_is_receiver') in (
            True, 1, '1', 'true', 'True', 'on', 'yes'
        )
        if sender_is_receiver:
            sender_name = str(data.get('sender_name', '')).strip()
            sender_parts = sender_name.split()
            data['customer_first_name'] = sender_parts[0] if sender_parts else ''
            data['customer_last_name'] = ' '.join(sender_parts[1:]) if len(sender_parts) > 1 else ''
            data['customer_phone'] = str(data.get('sender_phone', '')).strip()
            data['customer_address'] = str(
                data.get('delivery_address') or data.get('customer_address') or ''
            ).strip()
       
        # Create customer in database
        customer = Customer.objects.create(
            first_name=data.get('first_name'),
            last_name=data.get('last_name'),
            email=data.get('email'),
            phone=data.get('phone'),
            address=data.get('address', ''),
            city=data.get('city', ''),
            state=data.get('state', ''),
            zip_code=data.get('zip_code', ''),
        )
       
        return JsonResponse({
            'success': True,
            'message': f'Customer {customer.first_name} {customer.last_name} created successfully!',
            'customer': {
                'id': customer.customer_id,
                'name': f'{customer.first_name} {customer.last_name}',
                'email': customer.email,
                'phone': customer.phone,
            }
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'message': f'Error creating customer: {str(e)}'
        }, status=400)




@login_required(login_url='login')
def orders(request):
    """List all orders from database"""
    
    # Check if we need to delete completed orders from the previous month
   
    search_query = request.GET.get('search', '')
    status_filter = request.GET.get('status', '')
   
    # Get orders from database
    orders_list = Order.objects.select_related('customer').prefetch_related(
        'items__product', 'payments'
    )
   
    if search_query:
        orders_list = orders_list.filter(
            Q(order_number__icontains=search_query) |
            Q(sender_name__icontains=search_query) |
            Q(sender_phone__icontains=search_query) |
            Q(customer__email__icontains=search_query)
        )
   
    if status_filter:
        orders_list = orders_list.filter(status=status_filter)

    delivery_filter = request.GET.get('delivery', '').strip().lower()
    if delivery_filter == 'tomorrow':
        orders_list = orders_list.filter(
            delivery_date=get_manila_today() + timedelta(days=1)
        ).exclude(status='cancelled')

    orders_list = _order_by_fifo_schedule(orders_list)
   
    # Completed Orders modal: current Manila month only.
    manila_today = get_manila_today()
    current_month_start = manila_today.replace(day=1)
    if manila_today.month == 12:
        next_month_start = current_month_start.replace(
            year=manila_today.year + 1,
            month=1,
        )
    else:
        next_month_start = current_month_start.replace(
            month=manila_today.month + 1,
        )

    completed_orders = (
        Order.objects.filter(status='completed')
        .filter(
            Q(
                delivery_date__gte=current_month_start,
                delivery_date__lt=next_month_start,
            )
            | Q(
                delivery_date__isnull=True,
                updated_at__date__gte=current_month_start,
                updated_at__date__lt=next_month_start,
            )
        )
        .select_related('customer')
        .prefetch_related('items__product')
        .order_by('-updated_at')
    )
   
    # Get all products for order creation
    products = Product.objects.filter(is_active=True).order_by('name')
   
    # Notification data
    new_customers = Customer.objects.filter(created_at__date=get_manila_today()).count()
    pending_payments = _pending_payment_count()
    
    # Get next month's deletion date for warning message
    next_deletion_date = get_next_month_deletion_date()
    
    # Get Manila timezone info for delivery date filtering
    manila_tomorrow = manila_today + timedelta(days=1)
    
    # Add Manila timezone delivery info to context
    orders_with_delivery_info = []
    tomorrow_count = 0
    for order in orders_list:
        payment_state = calculate_order_payment_display_state(
            order, order.payments.all()
        )
        order.display_payment_state = payment_state['code']
        order.display_payment_state_label = payment_state['label']
        order.display_remaining_balance = payment_state['remaining_balance']
        if order.delivery_date == manila_tomorrow:
            tomorrow_count += 1
        orders_with_delivery_info.append(order)
   
    context = {
        'orders': orders_with_delivery_info,
        'completed_orders': completed_orders,
        'products': products,
        'search_query': search_query,
        'status_filter': status_filter,
        'new_customers_count': new_customers,
        'pending_payments': pending_payments,
        'next_deletion_date': next_deletion_date,
        'manila_today': manila_today,
        'manila_tomorrow': manila_tomorrow,
        'tomorrow_deliveries_count': tomorrow_count,
    }
   
    return render(request, 'orders.html', context)




@login_required(login_url='login')
@require_http_methods(["POST"])
@csrf_protect
@transaction.atomic
def order_create_ajax(request):
    """
    AJAX endpoint to create order with AUTOMATED WORKFLOW:
    1. Create/Get Customer
    2. Create Order
    3. Add Order Items
    4. Calculate Totals
    5. Auto-Create Payment
    """
    try:
        data = json.loads(request.body)
       
        # Normalize sender/receiver values before validation. This accepts the
        # current payload and safely supports older cached frontend field names.
        delivery_type = str(data.get('notes', '')).lstrip().upper().startswith('[PICK UP]')
        sender_name_input = str(data.get('sender_name', '')).strip()
        sender_phone_input = str(data.get('sender_phone', '')).strip()

        receiver_name_input = str(
            data.get('receiver_name')
            or data.get('receiver_full_name')
            or data.get('customer_name')
            or ''
        ).strip()
        receiver_phone_input = str(
            data.get('receiver_phone')
            or data.get('contact_number')
            or ''
        ).strip()
        receiver_address_input = str(
            data.get('receiver_address')
            or data.get('delivery_address')
            or ''
        ).strip()

        # Compatibility for an older Create Order payload where receiver names
        # were sent through customer_first_name/customer_last_name. Only use it
        # when it is clearly different from the sender/customer name.
        if not receiver_name_input:
            legacy_receiver_name = ' '.join(filter(None, [
                str(data.get('customer_first_name', '')).strip(),
                str(data.get('customer_last_name', '')).strip(),
            ])).strip()
            if legacy_receiver_name and legacy_receiver_name.casefold() != sender_name_input.casefold():
                receiver_name_input = legacy_receiver_name

        required_fields = ['customer_email', 'customer_first_name', 'items']
        if not delivery_type:
            required_fields.append('customer_phone')
        for field in required_fields:
            if not data.get(field):
                return JsonResponse({
                    'success': False,
                    'message': f'Missing required field: {field}'
                }, status=400)

        if not sender_name_input:
            return JsonResponse({'success': False, 'message': 'Sender name is required.'}, status=400)
        if not delivery_type and not sender_phone_input:
            return JsonResponse({'success': False, 'message': 'Sender phone is required for Local Drop-off.'}, status=400)

        if not delivery_type:
            if not receiver_name_input:
                return JsonResponse({'success': False, 'message': 'Please enter the receiver name.'}, status=400)
            if not receiver_phone_input:
                return JsonResponse({'success': False, 'message': 'Please enter the receiver contact number.'}, status=400)
            if not receiver_address_input:
                return JsonResponse({'success': False, 'message': 'Please enter the delivery address.'}, status=400)
       
        # ======== STEP 1: CREATE OR GET CUSTOMER ========
        customer_email = data.get('customer_email')
       
        # Check if customer exists in database
        customer, created = Customer.objects.get_or_create(
            email=customer_email,
            defaults={
                'first_name': data.get('customer_first_name', ''),
                'last_name': data.get('customer_last_name', ''),
                'phone': data.get('customer_phone', ''),
                'address': data.get('customer_address', ''),
            }
        )
       
        customer_created = created
        if not created:
            customer.first_name = data.get('customer_first_name', customer.first_name)
            customer.last_name = data.get('customer_last_name', customer.last_name)
            customer.phone = data.get('customer_phone', customer.phone)
            if data.get('customer_address'):
                customer.address = data.get('customer_address', '')
            customer.save()
       
        # ======== STEP 2: CREATE ORDER IN DATABASE ========

        # Parse delivery date
        delivery_date_val = None
        raw_delivery_date = data.get('delivery_date', '')
        if raw_delivery_date:
            try:
                delivery_date_val = datetime.strptime(raw_delivery_date, '%Y-%m-%d').date()
            except ValueError:
                return JsonResponse({'success': False, 'message': 'Invalid delivery date.'}, status=400)
        
        # Parse delivery time
        delivery_time_val = None
        raw_delivery_time = data.get('delivery_time', '')
        if raw_delivery_time:
            try:
                delivery_time_val = datetime.strptime(raw_delivery_time, '%H:%M').time()
            except ValueError:
                return JsonResponse({'success': False, 'message': 'Invalid delivery time.'}, status=400)

        if delivery_date_val and delivery_date_val < get_manila_today():
            return JsonResponse({'success': False, 'message': 'Delivery date cannot be in the past.'}, status=400)

        sender_name = sender_name_input
        sender_phone = sender_phone_input
        receiver_name = receiver_name_input
        receiver_phone = receiver_phone_input
        receiver_address = receiver_address_input
        if delivery_type:
            receiver_name = receiver_name or sender_name
            receiver_phone = receiver_phone or sender_phone
            receiver_address = ''

        if not sender_name:
            return JsonResponse({'success': False, 'message': 'Sender name is required.'}, status=400)
        if not delivery_type and not sender_phone:
            return JsonResponse({'success': False, 'message': 'Sender phone is required for Local Drop-off.'}, status=400)
        if not delivery_type and (not receiver_name or not receiver_phone):
            return JsonResponse({'success': False, 'message': 'Receiver name and phone are required.'}, status=400)

        monthly_order_number = _get_next_monthly_order_number()

        order = Order.objects.create(
            order_number=monthly_order_number,
            customer=customer,
            status='pending',
            notes=data.get('notes', ''),
            delivery_date=delivery_date_val,
            delivery_time=delivery_time_val,
            receiver_name=receiver_name,
            customer_phone=receiver_phone,
            customer_address=receiver_address,
            delivery_address=data.get('delivery_address', receiver_address),
            fulfilled_by=data.get('fulfilled_by', ''),
            sender_name=sender_name,
            sender_phone=sender_phone,
            sender_is_receiver=bool(data.get('sender_is_receiver', False)),
            rider_name=data.get('rider_name', ''),
            balance_payment=Decimal('0.00'),
            delivery_fee_charge=Decimal('0.00'),
        )
        # ======== STEP 3: ADD ORDER ITEMS TO DATABASE ========
        items_data = data.get('items', [])
       
        for item_data in items_data:
            product_name = item_data.get('product_name', 'Custom Product')
            try:
                unit_price = _parse_money(item_data.get('unit_price', 0), 'Order Amount')
            except ValueError as exc:
                return JsonResponse({'success': False, 'message': str(exc)}, status=400)
           
            # Find product by name first, then by ID, otherwise create it
            product = None
            product_id = item_data.get('product_id')
           
            # Try to find by name (case-insensitive)
            product = Product.objects.filter(name__iexact=product_name, is_active=True).first()
           
            # Try by ID as fallback
            if not product and product_id:
                try:
                    product = Product.objects.get(product_id=product_id)
                except Product.DoesNotExist:
                    pass
           
            # Create a new product record if still not found
            if not product:
                from uuid import uuid4
                product = Product.objects.create(
                    name=product_name,
                    sku=f'CUSTOM-{uuid4().hex[:12].upper()}',
                    price=unit_price if unit_price > 0 else Decimal('0.00'),
                    stock_quantity=9999,
                    low_stock_threshold=0,
                    is_active=True
                )
           
            if unit_price <= 0:
                unit_price = product.price
           
            OrderItem.objects.create(
                order=order,
                product=product,
                quantity=int(item_data.get('quantity', 1)),
                unit_price=unit_price
            )
       
        # ======== STEP 4: CALCULATE TOTALS ========
        order.calculate_totals()
       
        # ======== STEP 5: AUTO-CREATE PAYMENT IN DATABASE ========
        payment_method = data.get('payment_method', 'cash')
        payment_amount = data.get('payment_amount')

        valid_payment_methods = {choice[0] for choice in Payment.PAYMENT_METHOD_CHOICES}
        if payment_method not in valid_payment_methods:
            payment_method = 'cash'

        try:
            payment_type = _requested_initial_payment_type(data)
            order_total = _money(order.total)
            if order_total <= 0:
                raise ValueError('Order Amount must be greater than zero before recording payment.')
            if payment_amount in (None, '') and payment_type == Payment.TYPE_FULL_PAYMENT:
                payment_amount = order_total
            payment_amount = _parse_money(payment_amount, 'Payment Amount', allow_zero=False)
        except ValueError as exc:
            transaction.set_rollback(True)
            return JsonResponse({'success': False, 'message': str(exc)}, status=400)

        if payment_type == Payment.TYPE_FULL_PAYMENT:
            if payment_amount != order_total:
                transaction.set_rollback(True)
                return JsonResponse({
                    'success': False,
                    'message': f'Full Payment must equal the exact Order Amount of ₱{order_total:.2f}.',
                }, status=400)
            initial_balance = Decimal('0.00')
            payment_status = Payment.STATUS_FULLY_PAID
        else:
            if payment_amount >= order_total:
                transaction.set_rollback(True)
                return JsonResponse({
                    'success': False,
                    'message': 'Down Payment must be greater than zero and less than the Order Amount.',
                }, status=400)
            initial_balance = (order_total - payment_amount).quantize(MONEY_PLACES)
            payment_status = Payment.STATUS_DOWN_PAYMENT

        payment = Payment.objects.create(
            order=order,
            amount=payment_amount,
            payment_method=payment_method,
            payment_status=payment_status,
            payment_type=payment_type,
            notes=f'Auto-generated payment for order {order.order_number}'
        )
        order.balance_payment = initial_balance
        order.save(update_fields=['balance_payment', 'updated_at'])
       
        # Prepare response with all created data
        response_data = {
            'success': True,
            'message': f'Order {order.order_number} created successfully! Payment {payment.payment_number} generated.',
            'customer_created': customer_created,
            'order': {
                'id': order.order_id,
                'order_number': order.order_number,
                'customer_name': order.customer_name,
                'customer_email': customer.email,
                'status': order.status,
                'total': f'{order.total:.2f}',
                'created_at': order.created_at.strftime('%Y-%m-%d %H:%M:%S'),
            },
            'payment': {
                'id': payment.payment_id,
                'payment_number': payment.payment_number,
                'amount': f'{payment.amount:.2f}',
                'method': payment.payment_method,
                'status': payment.payment_status,
                'type': payment.payment_type,
            }
        }
       
        return JsonResponse(response_data)
       
    except Product.DoesNotExist:
        return JsonResponse({
            'success': False,
            'message': 'Product not found in database'
        }, status=404)
    except ValueError as e:
        return JsonResponse({
            'success': False,
            'message': str(e)
        }, status=400)
    except Exception as e:
        logging.getLogger(__name__).exception('Order creation failed')
        return JsonResponse({
            'success': False,
            'message': f'Error creating order: {str(e)}'
        }, status=400)




@login_required(login_url='login')
@require_http_methods(["POST"])
@csrf_protect
@transaction.atomic
def order_update_ajax(request):
    """Update an existing order and its related customer, item, and payment."""
    try:
        data = json.loads(request.body or b'{}')
        order_id = data.get('order_id')
        if not order_id:
            return JsonResponse({'success': False, 'message': 'order_id is required.'}, status=400)

        order = (
            Order.objects.select_for_update().select_related('customer')
            .prefetch_related('items', 'payments')
            .get(order_id=order_id)
        )

        requested_notes = str(data.get('notes', order.notes or '')).strip()
        requested_pickup = requested_notes.lstrip().upper().startswith('[PICK UP]')
        if requested_pickup and _money(order.delivery_fee_charge) > 0:
            return JsonResponse({
                'success': False,
                'message': 'This order has a permanent Delivery Fee and cannot be changed to Local Pick Up.',
            }, status=409)

        sender_name = str(data.get('sender_name', order.sender_name or '')).strip()
        sender_phone = str(data.get('sender_phone', order.sender_phone or '')).strip()
        raw_same = data.get('sender_is_receiver', order.sender_is_receiver)
        sender_is_receiver = raw_same in (True, 1, '1', 'true', 'True', 'on', 'yes')

        receiver_name = str(data.get('receiver_name', order.receiver_name or '')).strip()
        receiver_phone = str(data.get('receiver_phone', order.customer_phone or '')).strip()
        receiver_address = str(data.get('receiver_address', order.customer_address or '')).strip()

        if requested_pickup:
            sender_is_receiver = True
            receiver_name = sender_name
            receiver_phone = sender_phone
            receiver_address = ''
        elif sender_is_receiver:
            receiver_name = sender_name
            receiver_phone = sender_phone
            receiver_address = str(
                data.get('delivery_address')
                or data.get('customer_address')
                or order.delivery_address
                or order.customer_address
                or ''
            ).strip()

        if not sender_name:
            return JsonResponse({'success': False, 'message': 'Sender name is required.'}, status=400)
        if not requested_pickup and not sender_phone:
            return JsonResponse({'success': False, 'message': 'Sender phone is required for Local Drop-off.'}, status=400)
        if not requested_pickup and (not receiver_name or not receiver_phone):
            return JsonResponse({'success': False, 'message': 'Receiver name and phone are required.'}, status=400)

        raw_date = str(data.get('delivery_date', '')).strip()
        if raw_date:
            try:
                parsed_date = datetime.strptime(raw_date, '%Y-%m-%d').date()
            except ValueError:
                return JsonResponse({'success': False, 'message': 'Invalid delivery date.'}, status=400)
            if parsed_date < get_manila_today():
                return JsonResponse({'success': False, 'message': 'Delivery date cannot be in the past.'}, status=400)
            order.delivery_date = parsed_date

        raw_time = str(data.get('delivery_time', '')).strip()
        if raw_time:
            try:
                order.delivery_time = datetime.strptime(raw_time, '%H:%M').time()
            except ValueError:
                return JsonResponse({'success': False, 'message': 'Invalid delivery time.'}, status=400)

        # The related Customer row is the sender/buyer, never the receiver.
        customer = order.customer
        sender_parts = sender_name.split()
        customer.first_name = sender_parts[0] if sender_parts else ''
        customer.last_name = ' '.join(sender_parts[1:]) if len(sender_parts) > 1 else ''
        customer.phone = sender_phone
        if receiver_address:
            customer.address = receiver_address
        email = str(data.get('customer_email', customer.email or '')).strip()
        if email:
            customer.email = email
        customer.save()

        order.receiver_name = receiver_name
        order.customer_phone = receiver_phone
        order.customer_address = receiver_address
        order.delivery_address = '' if requested_pickup else str(
            data.get('delivery_address', order.delivery_address or '')
        ).strip()
        order.sender_name = sender_name
        order.sender_phone = sender_phone
        order.sender_is_receiver = sender_is_receiver
        order.notes = requested_notes
        order.fulfilled_by = str(data.get('fulfilled_by', order.fulfilled_by or '')).strip()
        order.rider_name = str(data.get('rider_name', order.rider_name or '')).strip()

        # Delivery Fee is managed only from View Details. Switching an order
        # to Local Pick Up always clears it; drop-off edits preserve it.
        if _is_pickup_order(order):
            order.delivery_fee_charge = Decimal('0.00')
        order.save()

        items = data.get('items')
        if isinstance(items, list) and items:
            item_data = items[0]
            product_name = str(item_data.get('product_name', '')).strip()
            if not product_name:
                return JsonResponse({'success': False, 'message': 'Order item is required.'}, status=400)
            try:
                quantity = int(item_data.get('quantity', 1))
                unit_price = _parse_money(item_data.get('unit_price', 0), 'Order Amount')
            except (TypeError, ValueError) as exc:
                return JsonResponse({'success': False, 'message': str(exc)}, status=400)
            if quantity < 1 or unit_price < 0:
                return JsonResponse({'success': False, 'message': 'Invalid quantity or price.'}, status=400)

            product = Product.objects.filter(name__iexact=product_name, is_active=True).first()
            if product is None:
                from uuid import uuid4
                product = Product.objects.create(
                    name=product_name,
                    sku=f'CUSTOM-{uuid4().hex[:12].upper()}',
                    price=unit_price,
                    stock_quantity=9999,
                    low_stock_threshold=0,
                    is_active=True,
                )

            item = order.items.first() or OrderItem(order=order)
            item.product = product
            item.product_name = product_name
            item.product_sku = product.sku
            item.quantity = quantity
            item.unit_price = unit_price if unit_price > 0 else product.price
            item.save()
            order.calculate_totals()

        return JsonResponse({
            'success': True,
            'message': f'Order {order.order_number} updated successfully.',
            'order': {
                'id': order.order_id,
                'order_number': order.order_number,
                'sender_is_receiver': order.sender_is_receiver,
                'total': f'{Decimal(order.total or 0):.2f}',
                'status': order.status,
            }
        })
    except Order.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'Order not found.'}, status=404)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'message': 'Invalid JSON data.'}, status=400)
    except Exception as exc:
        logging.getLogger(__name__).exception('Order update failed')
        return JsonResponse({'success': False, 'message': f'Error updating order: {exc}'}, status=400)


# ============================================================================
# PAYMENTS VIEWS - WITH AJAX SUPPORT
# ============================================================================
@login_required(login_url='login')
def payments(request):
    """List all payments from database with status filter"""
   
    # Run the same Manila-time completed-order reset used by Orders and
    # Customers before cleaning related completed payments.

    search_query = request.GET.get('search', '')
    status_filter = request.GET.get('status', '')
    method_filter = request.GET.get('method', '')
   
    # Get payments from database
    payments_list = Payment.objects.select_related(
        'order', 'order__customer'
    ).prefetch_related('order__payments')
   
    if search_query:
        payments_list = payments_list.filter(
            Q(payment_number__icontains=search_query) |
            Q(order__order_number__icontains=search_query) |
            Q(transaction_id__icontains=search_query)
        )
   
    if method_filter:
        payments_list = payments_list.filter(payment_method=method_filter)
   
    status_filter = {
        'pending': STATE_DOWN_PAYMENT,
        'completed': STATE_FULLY_PAID,
    }.get(status_filter, status_filter)
    payment_rows = list(payments_list.order_by('-payment_date'))
    state_by_order = {}
    for payment in payment_rows:
        if payment.order_id not in state_by_order:
            state_by_order[payment.order_id] = calculate_order_payment_display_state(
                payment.order, payment.order.payments.all()
            )
        payment_state = state_by_order[payment.order_id]
        payment.display_payment_state = payment_state['code']
        payment.display_payment_state_label = payment_state['label']
        payment.calculated_remaining_balance = payment_state['remaining_balance']

    if status_filter in (STATE_DOWN_PAYMENT, STATE_FULLY_PAID):
        payment_rows = [
            payment for payment in payment_rows
            if payment.display_payment_state == status_filter
        ]
   
    # Revenue is derived from immutable received-payment ledger rows.
    total_revenue = _received_payment_transactions().aggregate(
        total=Sum('amount')
    )['total'] or Decimal('0.00')

    pending_payments_count = _pending_payment_count()

    # Monthly summary cards use the current Manila calendar month only.
    manila_today = get_manila_today()
    current_month_start = manila_today.replace(day=1)

    if current_month_start.month == 12:
        next_month_start = current_month_start.replace(
            year=current_month_start.year + 1,
            month=1,
        )
    else:
        next_month_start = current_month_start.replace(
            month=current_month_start.month + 1,
        )

    manila_tz = get_manila_timezone()
    month_start_datetime = timezone.make_aware(
        datetime.combine(current_month_start, datetime.min.time()),
        manila_tz,
    )
    next_month_start_datetime = timezone.make_aware(
        datetime.combine(next_month_start, datetime.min.time()),
        manila_tz,
    )

    customers_transacted = _received_payment_transactions().filter(
        payment_date__gte=month_start_datetime,
        payment_date__lt=next_month_start_datetime,
    ).values('order__customer_id').distinct().count()

    # Match the same month basis used by the completed-order monthly reset:
    # delivery date when present, otherwise the order's latest update date.
    orders_completed = Order.objects.filter(status='completed').filter(
        Q(
            delivery_date__gte=current_month_start,
            delivery_date__lt=next_month_start,
        )
        | Q(
            delivery_date__isnull=True,
            updated_at__gte=month_start_datetime,
            updated_at__lt=next_month_start_datetime,
        )
    ).count()

    # Notification data
    recent_orders = Order.objects.select_related('customer').order_by('-created_at')[:3]
    new_customers = Customer.objects.filter(created_at__date=get_manila_today()).count()

    # Get next month's deletion date for warning message
    next_deletion_date = get_next_month_deletion_date()

    context = {
        'payments': payment_rows,
        'total_revenue': total_revenue,
        'pending_payments_count': pending_payments_count,
        'customers_transacted': customers_transacted,
        'orders_completed': orders_completed,
        'search_query': search_query,
        'status_filter': status_filter,
        'method_filter': method_filter,
        'recent_orders': recent_orders,
        'new_customers_count': new_customers,
        'next_deletion_date': next_deletion_date,
    }

    return render(request, 'payments.html', context)




@login_required(login_url='login')
@require_http_methods(["POST"])
@csrf_protect
def payment_update_ajax(request):
    """AJAX endpoint to update payment"""
    try:
        data = json.loads(request.body)
        payment_id = data.get('payment_id')
       
        # Update in database
        payment = Payment.objects.get(payment_id=payment_id)
        requested_status = data.get('payment_status')
        if payment.payment_type and requested_status not in (None, payment.payment_status):
            return JsonResponse({
                'success': False,
                'message': 'Payment status is calculated by the two-payment workflow and cannot be edited manually.',
            }, status=409)
        payment.payment_status = requested_status or payment.payment_status
        payment.payment_method = data.get('payment_method', payment.payment_method)
        payment.transaction_id = data.get('transaction_id', payment.transaction_id)
        payment.notes = data.get('notes', payment.notes)

        valid_payment_methods = {choice[0] for choice in Payment.PAYMENT_METHOD_CHOICES}
        valid_payment_statuses = {choice[0] for choice in Payment.PAYMENT_STATUS_CHOICES}
        if payment.payment_method not in valid_payment_methods:
            payment.payment_method = 'cash'
        if payment.payment_status not in valid_payment_statuses:
            payment.payment_status = 'pending'

        payment.save()
       
        return JsonResponse({
            'success': True,
            'message': f'Payment {payment.payment_number} updated successfully!',
            'payment': {
                'id': payment.payment_id,
                'payment_number': payment.payment_number,
                'status': payment.payment_status,
                'method': payment.payment_method,
            }
        })
    except Payment.DoesNotExist:
        return JsonResponse({
            'success': False,
            'message': 'Payment not found'
        }, status=404)
    except Exception as e:
        return JsonResponse({
            'success': False,
            'message': f'Error updating payment: {str(e)}'
        }, status=400)

@login_required(login_url='login')
@require_http_methods(["POST"])
@csrf_protect
@transaction.atomic
def payment_update_by_order_ajax(request):
    """Create the one permitted final balance transaction for a down payment."""
    try:
        data = json.loads(request.body or b'{}')
        order_id = data.get('order_id')
        submitted_amount = data.get('balance_payment_amount')

        if not order_id:
            return JsonResponse({
                'success': False,
                'message': 'order_id is required.'
            }, status=400)

        order = Order.objects.select_for_update().get(order_id=order_id)
        payments = list(
            order.payments.select_for_update().order_by('payment_date', 'payment_id')
        )

        if not payments:
            return JsonResponse({
                'success': False,
                'message': 'No payment found for this order.'
            }, status=404)

        if any(payment.payment_type is None for payment in payments):
            return JsonResponse({
                'success': False,
                'message': 'Legacy payment history must be reconciled before recording a balance payment.',
            }, status=409)
        if any(payment.payment_type == Payment.TYPE_FULL_PAYMENT for payment in payments):
            return JsonResponse({
                'success': False,
                'message': 'A Full Payment order cannot receive a second payment.',
            }, status=409)
        if any(payment.payment_type == Payment.TYPE_BALANCE_PAYMENT for payment in payments):
            return JsonResponse({
                'success': False,
                'message': 'The Remaining Balance Payment has already been recorded.',
            }, status=409)
        if len(payments) != 1 or payments[0].payment_type != Payment.TYPE_DOWN_PAYMENT:
            return JsonResponse({
                'success': False,
                'message': 'This order does not have a valid single Down Payment to settle.',
            }, status=409)

        order_total = _money(order.total)
        received_total = sum((_money(payment.amount) for payment in payments), Decimal('0.00'))
        calculated_balance = (order_total - received_total).quantize(MONEY_PLACES)
        if calculated_balance <= 0:
            return JsonResponse({
                'success': False,
                'message': 'This order has no remaining balance.',
            }, status=409)

        try:
            final_amount = _parse_money(submitted_amount, 'Remaining Balance Payment', allow_zero=False)
        except ValueError as exc:
            return JsonResponse({'success': False, 'message': str(exc)}, status=400)
        if final_amount < calculated_balance:
            return JsonResponse({
                'success': False,
                'message': f'Final Balance Payment must be at least ₱{calculated_balance:.2f}.',
            }, status=400)

        payment_method = str(data.get('payment_method') or payments[0].payment_method).strip()
        valid_methods = {choice[0] for choice in Payment.PAYMENT_METHOD_CHOICES}
        if payment_method not in valid_methods:
            return JsonResponse({'success': False, 'message': 'Invalid payment method.'}, status=400)

        balance_payment = Payment.objects.create(
            order=order,
            amount=final_amount,
            payment_method=payment_method,
            payment_status=Payment.STATUS_FULLY_PAID,
            payment_type=Payment.TYPE_BALANCE_PAYMENT,
            transaction_id=str(data.get('transaction_id', '') or '').strip()[:100],
            notes=str(data.get('notes', '') or f'Final balance payment for order {order.order_number}'),
        )
        order.balance_payment = Decimal('0.00')
        order.save(update_fields=['balance_payment', 'updated_at'])

        return JsonResponse({
            'success': True,
            'order_amount': f'{order_total:.2f}',
            'down_payment': f'{_money(payments[0].amount):.2f}',
            'settlement_amount': f'{final_amount:.2f}',
            'balance_payment': '0.00',
            'required_balance_payment': '0.00',
            'has_legacy_payment': False,
            'can_pay_balance': False,
            'payment': {
                'id': balance_payment.payment_id,
                'payment_number': balance_payment.payment_number,
                'status': balance_payment.payment_status,
                'type': balance_payment.payment_type,
                'amount': f'{balance_payment.amount:.2f}',
            }
        })

    except Order.DoesNotExist:
        return JsonResponse({
            'success': False,
            'message': 'Order not found.'
        }, status=404)
    except Exception as e:
        logging.getLogger(__name__).exception('Payment update by order failed')
        return JsonResponse({
            'success': False,
            'message': str(e)
        }, status=400)


@require_http_methods(["GET"])
@login_required(login_url='login')
def payment_get_by_order_ajax(request):
    """Read payment information without repairing or rewriting stored money."""
    try:
        order_id = request.GET.get('order_id')
        if not order_id:
            return JsonResponse({'success': False, 'message': 'order_id is required'}, status=400)
        
        order = Order.objects.get(order_id=order_id)
        payments = list(order.payments.order_by('payment_date', 'payment_id'))
        
        if not payments:
            return JsonResponse({'success': False, 'message': 'No payment found for this order'}, status=404)
        
        order_total = Decimal(order.total or 0).quantize(MONEY_PLACES)
        saved_additional_text = str(order.additional_payment or '').strip()
        try:
            saved_additional = Decimal(saved_additional_text or '0').quantize(MONEY_PLACES)
        except InvalidOperation:
            saved_additional = Decimal('0.00')

        initial_payment = payments[0]
        down_payment = _money(initial_payment.amount)
        cached_balance = Decimal(order.balance_payment or 0).quantize(MONEY_PLACES)
        payment_state = calculate_order_payment_state(order, payments)
        display_payment_state = calculate_order_payment_display_state(order, payments)
        status = payment_state['code']
        has_legacy_payment = payment_state['is_legacy']
        balance = cached_balance if has_legacy_payment else payment_state['remaining_balance']
        can_pay_balance = (
            status == STATE_DOWN_PAYMENT
            and len(payments) == 1
            and initial_payment.payment_type == Payment.TYPE_DOWN_PAYMENT
            and balance > 0
        )

        return JsonResponse({
            'success': True,
            'order_amount': f'{order_total:.2f}',
            'down_payment': f'{down_payment:.2f}',
            'additional_payment': f'{saved_additional:.2f}',
            'balance_payment': f'{balance:.2f}',
            'required_balance_payment': f'{balance:.2f}',
            'has_legacy_payment': has_legacy_payment,
            'can_pay_balance': can_pay_balance,
            'payment': {
                'id': initial_payment.payment_id,
                'payment_number': initial_payment.payment_number,
                'amount': f'{down_payment:.2f}',
                'payment_method': initial_payment.payment_method,
                'payment_status': status,
                'payment_status_label': display_payment_state['label'],
                'display_status': display_payment_state['code'],
                'display_status_label': display_payment_state['label'],
                'payment_type': initial_payment.payment_type,
            },
            'transactions': [
                {
                    'id': transaction_row.payment_id,
                    'payment_number': transaction_row.payment_number,
                    'amount': f'{_money(transaction_row.amount):.2f}',
                    'payment_method': transaction_row.payment_method,
                    'payment_status': transaction_row.payment_status,
                    'payment_type': transaction_row.payment_type,
                }
                for transaction_row in payments
            ],
        })
    except Order.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'Order not found'}, status=404)
    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)}, status=400)





# ============================================================================
# REPORTS VIEW - PULLING DATA FROM DATABASE
# ============================================================================
@login_required(login_url='login')
def reports(request):
    """Generate reports from received payment transactions in Manila time."""

    # Preserve the existing monthly/yearly archival workflow before building
    # the live current-month report. Archived historical report data is kept.

    today = get_manila_today()
    current_month_start = today.replace(day=1)
    if today.month == 12:
        next_month_start = today.replace(year=today.year + 1, month=1, day=1)
    else:
        next_month_start = today.replace(month=today.month + 1, day=1)

    month_start_datetime, next_month_start_datetime = _manila_month_datetime_bounds(
        current_month_start
    )
    current_month_payments = (
        _received_payment_transactions()
        .filter(
            payment_date__gte=month_start_datetime,
            payment_date__lt=next_month_start_datetime,
        )
        .select_related('order', 'order__customer')
        .order_by('payment_date', 'payment_id')
    )

    total_monthly_sales = current_month_payments.aggregate(
        total_transactions=Count('payment_id'),
        total_revenue=Sum('amount'),
    )
    total_monthly_sales['total_transactions'] = total_monthly_sales['total_transactions'] or 0
    total_monthly_sales['total_revenue'] = total_monthly_sales['total_revenue'] or Decimal('0.00')

    year_start_datetime = timezone.make_aware(
        datetime(today.year, 1, 1),
        get_manila_timezone(),
    )
    next_year_start_datetime = timezone.make_aware(
        datetime(today.year + 1, 1, 1),
        get_manila_timezone(),
    )
    current_year_payments = (
        _received_payment_transactions()
        .filter(
            payment_date__gte=year_start_datetime,
            payment_date__lt=next_year_start_datetime,
        )
        .select_related('order', 'order__customer')
        .order_by('payment_date', 'payment_id')
    )
    live_months = aggregate_payments_by_manila_month(current_year_payments)

    reports_year_data = {}
    for archive in MonthlySalesArchive.objects.filter(year=today.year):
        try:
            month_number = list(calendar.month_name).index(archive.month_name)
        except ValueError:
            continue
        reports_year_data[str(month_number)] = {
            'month_name': archive.month_name,
            'sales_by_day': archive.sales_by_day or {},
            'customers_by_day': archive.orders_by_day or {},
        }
    for (live_year, month_number), live_data in live_months.items():
        if live_year != today.year:
            continue
        month_key = str(month_number)
        archived_data = reports_year_data.get(month_key, {})
        merged_rows, merged_sales = merge_lightweight_archive(
            archived_data.get('customers_by_day', {}),
            live_data['customers_by_day'],
        )
        reports_year_data[month_key] = {
            'month_name': calendar.month_name[month_number],
            'sales_by_day': merged_sales,
            'customers_by_day': merged_rows,
        }

    reports_year_data.setdefault(str(today.month), {
        'month_name': today.strftime('%B'),
        'sales_by_day': {},
        'customers_by_day': {},
    })
    current_month_data = reports_year_data[str(today.month)]
    current_month_sales_by_day = current_month_data['sales_by_day']
    current_month_orders_by_day = current_month_data['customers_by_day']

    # Current-year Employee Standing overview. The monthly evaluations table
    # remains the single source of truth, so Reports always matches the
    # Employee Standing page and Django Admin after each page refresh.
    employees = list(
        Employee.objects.annotate(
            current_year_stars=Sum(
                'monthly_evaluations__stars',
                filter=Q(monthly_evaluations__year=today.year),
            ),
            current_year_demerits=Sum(
                'monthly_evaluations__demerits',
                filter=Q(monthly_evaluations__year=today.year),
            ),
        ).order_by('full_name')
    )
    for employee in employees:
        employee.yearly_total_stars = min(int(employee.current_year_stars or 0), 24)
        employee.yearly_total_demerits = int(employee.current_year_demerits or 0)

    pending_payments = _pending_payment_count()
    new_customers_count = Customer.objects.filter(created_at__date=today).count()
    next_year_deletion_date = get_next_year_deletion_date()
    next_deletion_date = get_next_month_deletion_date()

    context = {
        'total_monthly_sales': total_monthly_sales,
        'current_month_name': today.strftime('%B'),
        'current_month_year': today.year,
        'current_month_number': today.month,
        'current_month_day': today.day,
        'current_month_sales_by_day': current_month_sales_by_day,
        'current_month_orders_by_day': current_month_orders_by_day,
        'reports_year_data': reports_year_data,
        'employees': employees,
        'employee_standing_year': today.year,
        'pending_payments': pending_payments,
        'new_customers_count': new_customers_count,
        'next_year_deletion_date': next_year_deletion_date,
        'next_deletion_date': next_deletion_date,
    }

    return render(request, 'reports.html', context)




# ============================================================================
# EMPLOYEE STANDING VIEWS
# ============================================================================
@login_required(login_url='login')
@employee_standing_pin_required
@never_cache
def employee_standing(request):
    """Display retained monthly evaluations and current-year dynamic totals."""
    today = get_manila_today()
    selected_year = today.year

    evaluations = list(
        EmployeeMonthlyPerformance.objects
        .filter(year=selected_year)
        .select_related('employee')
        .order_by('employee_id', 'month')
    )
    evaluations_by_employee = {}
    for evaluation in evaluations:
        evaluations_by_employee.setdefault(evaluation.employee_id, {})[evaluation.month] = evaluation

    employees = list(Employee.objects.order_by('full_name'))
    for employee in employees:
        employee_evaluations = evaluations_by_employee.get(employee.pk, {})
        employee.yearly_total_stars = min(
            sum(item.stars for item in employee_evaluations.values()),
            24,
        )
        employee.yearly_total_demerits = sum(
            item.demerits for item in employee_evaluations.values()
        )
        employee.monthly_history = [
            {
                'month_number': month_number,
                'month_name': calendar.month_name[month_number],
                'evaluation': employee_evaluations.get(month_number),
            }
            for month_number in range(1, 13)
        ]

    return render(request, 'employee_standing.html', {
        'employees': employees,
        'today': today,
        'current_year': selected_year,
        'month_choices': [
            (month_number, calendar.month_name[month_number])
            for month_number in range(1, 13)
        ],
    })


@login_required(login_url='login')
@employee_standing_pin_required
def employee_standing_profile(request, employee_id):
    """Keep the existing route compatible by returning the redesigned module."""
    return redirect(f"{reverse('pages:employee_standing')}?employee={employee_id}")


@login_required(login_url='login')
@employee_standing_pin_required
@require_http_methods(["GET", "POST"])
@csrf_protect
@transaction.atomic
def employee_monthly_evaluation_ajax(request):
    """Fetch or upsert one monthly evaluation without allowing duplicates."""
    if request.method == 'GET':
        try:
            employee_id = int(request.GET.get('employee_id', ''))
            month = int(request.GET.get('month', ''))
            year = int(request.GET.get('year', ''))
        except (TypeError, ValueError):
            return JsonResponse({
                'success': False,
                'message': 'Employee, month, and year are required.',
            }, status=400)

        evaluation = (
            EmployeeMonthlyPerformance.objects
            .filter(employee_id=employee_id, month=month, year=year)
            .first()
        )
        if not evaluation:
            return JsonResponse({'success': True, 'exists': False})

        return JsonResponse({
            'success': True,
            'exists': True,
            'evaluation': {
                'id': evaluation.pk,
                'employee_id': evaluation.employee_id,
                'month': evaluation.month,
                'year': evaluation.year,
                'stars': evaluation.stars,
                'demerits': evaluation.demerits,
                'admin_remarks': evaluation.admin_remarks,
            },
        })

    payload = request.POST
    try:
        employee_id = int(payload.get('employee_id', ''))
        month = int(payload.get('month', ''))
        year = int(payload.get('year', ''))
        stars = int(payload.get('stars', ''))
        demerits = int(payload.get('demerits', ''))
    except (TypeError, ValueError):
        return JsonResponse({
            'success': False,
            'message': 'Employee, month, year, stars, and demerits must be valid values.',
        }, status=400)

    admin_remarks = str(payload.get('admin_remarks', '')).strip()

    if month not in range(1, 13):
        return JsonResponse({'success': False, 'message': 'Select a valid month.'}, status=400)
    if year < 2000 or year > 2100:
        return JsonResponse({'success': False, 'message': 'Select a valid year.'}, status=400)
    if stars not in (0, 1, 2):
        return JsonResponse({'success': False, 'message': 'Stars must be 0, 1, or 2.'}, status=400)
    if demerits < 0 or demerits > 999:
        return JsonResponse({'success': False, 'message': 'Demerits must be between 0 and 999.'}, status=400)
    if not admin_remarks:
        return JsonResponse({'success': False, 'message': 'Admin remarks are required.'}, status=400)

    employee = get_object_or_404(
        Employee.objects.select_for_update(),
        pk=employee_id,
    )
    evaluation, created = EmployeeMonthlyPerformance.objects.update_or_create(
        employee=employee,
        month=month,
        year=year,
        defaults={
            'stars': stars,
            'demerits': demerits,
            'admin_remarks': admin_remarks,
        },
    )

    totals = (
        EmployeeMonthlyPerformance.objects
        .filter(employee=employee, year=year)
        .aggregate(
            total_stars=Sum('stars'),
            total_demerits=Sum('demerits'),
        )
    )

    return JsonResponse({
        'success': True,
        'created': created,
        'mode': 'created' if created else 'updated',
        'message': (
            'Monthly evaluation saved successfully.'
            if created
            else 'Existing monthly evaluation updated successfully.'
        ),
        'evaluation': {
            'id': evaluation.pk,
            'employee_id': employee.pk,
            'month': evaluation.month,
            'month_name': calendar.month_name[evaluation.month],
            'year': evaluation.year,
            'stars': evaluation.stars,
            'demerits': evaluation.demerits,
            'admin_remarks': evaluation.admin_remarks,
        },
        'totals': {
            'stars': min(int(totals['total_stars'] or 0), 24),
            'demerits': int(totals['total_demerits'] or 0),
        },
    })


# ============================================================================
# API ENDPOINTS FOR GETTING DATA
# ============================================================================
@require_http_methods(["GET"])
@login_required(login_url='login')
def orders_export_data_ajax(request):
    """Get all orders (unfiltered) for Excel export."""
    orders = (
        Order.objects.select_related('customer')
        .prefetch_related('items', 'payments')
        .order_by('-created_at')
    )

    export_rows = []
    for order in orders:
        items_text = ', '.join(item.product_name for item in order.items.all()) or '—'

        payment_state = calculate_order_payment_display_state(
            order, order.payments.all()
        )
        status_text = payment_state['label']

        if order.delivery_date:
            delivery_text = order.delivery_date.strftime('%b %d, %Y')
            if order.delivery_time:
                delivery_text = f"{delivery_text} {order.delivery_time.strftime('%I:%M %p').lstrip('0')}"
        else:
            delivery_text = '—'

        export_rows.append(
            {
                'delivery_date': delivery_text,
                'customer': order.customer_name,
                'items': items_text,
                'amount': float(order.total or 0),
                'status': status_text,
            }
        )

    return JsonResponse({'success': True, 'orders': export_rows})


@require_http_methods(["GET"])
@login_required(login_url='login')
def get_customers_ajax(request):
    """Get all customers as JSON"""
    customers = Customer.objects.all().values(
        'customer_id', 'first_name', 'last_name', 'email', 'phone'
    )
    return JsonResponse(list(customers), safe=False)




# ============================================================================
# OTHER VIEWS
# ============================================================================
@login_required(login_url='login')
def features(request):
    """Features page"""
    return render(request, 'features.html')

# ── Helper: shared notification context ─────────────────────────────
def get_notification_context():
    from django.utils import timezone
    today = timezone.now().date()
    pending_payments   = _pending_payment_count()
    new_customers_count = Customer.objects.filter(created_at__date=today).count()
    return {
        'pending_payments':   pending_payments,
        'new_customers_count': new_customers_count,
    }


# ── AJAX: Update order status ────────────────────────────────────────
@login_required(login_url='login')
@require_http_methods(["POST"])
@csrf_protect
@transaction.atomic
def order_update_status_ajax(request):
    """Update only the order/delivery lifecycle status."""
    try:
        data       = json.loads(request.body)
        order_id   = data.get('order_id')
        new_status = data.get('status')

        if not order_id or not new_status:
            return JsonResponse({'success': False, 'message': 'Missing order_id or status'}, status=400)

        valid_statuses = ['pending', 'processing', 'completed', 'cancelled']
        if new_status not in valid_statuses:
            return JsonResponse({'success': False, 'message': f'Invalid status. Must be one of: {", ".join(valid_statuses)}'}, status=400)

        order = Order.objects.select_for_update().get(order_id=order_id)
        if order.status == new_status:
            return JsonResponse({
                'success': True,
                'message': f'Order is already {new_status}.',
                'status': new_status,
                'already_updated': True,
            })

        order.status = new_status
        order.save(update_fields=['status', 'updated_at'])

        return JsonResponse({
            'success': True,
            'message': f'Status updated to {new_status}',
            'status':  new_status
        })

    except Order.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'Order not found'}, status=404)
    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)}, status=500)
    

@login_required(login_url='login')
@require_http_methods(["POST"])
@csrf_protect
def order_update_fulfilled_ajax(request):
    """Save the fulfilled_by name for an order."""
    try:
        data = json.loads(request.body)
        order_id     = data.get('order_id')
        fulfilled_by = data.get('fulfilled_by', '').strip()

        order = Order.objects.get(order_id=order_id)
        order.fulfilled_by = fulfilled_by
        order.save()

        return JsonResponse({'success': True})
    except Order.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'Order not found'}, status=404)
    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)}, status=500)


@login_required(login_url='login')
@require_http_methods(["POST"])
@csrf_protect
@transaction.atomic
def order_update_rider_ajax(request):
    """Update View Details rider/fee fields without coupling them financially."""
    try:
        data = json.loads(request.body or b'{}')
        order_id = data.get('order_id')
        if not order_id:
            return JsonResponse({'success': False, 'message': 'order_id is required.'}, status=400)

        order = Order.objects.select_for_update().get(order_id=order_id)
        if order.status == 'completed':
            return JsonResponse({
                'success': False,
                'message': 'Completed orders can no longer be edited.',
            }, status=409)

        update_fields = []
        if 'rider_name' in data:
            rider_name = str(data.get('rider_name') or '').strip()
            if not rider_name:
                return JsonResponse({
                    'success': False,
                    'message': 'Rider Name is required.',
                }, status=400)
            order.rider_name = rider_name
            update_fields.append('rider_name')

        if 'delivery_fee_charge' in data:
            if _is_pickup_order(order):
                return JsonResponse({
                    'success': False,
                    'message': 'Local Pick Up orders cannot have a Delivery Fee.',
                }, status=400)
            if _money(order.delivery_fee_charge) > 0:
                return JsonResponse({
                    'success': False,
                    'message': 'Delivery Fee has already been saved and cannot be changed.',
                }, status=409)
            try:
                order.delivery_fee_charge = _parse_money(
                    data.get('delivery_fee_charge'), 'Delivery Fee', allow_zero=False
                )
            except ValueError as exc:
                return JsonResponse({'success': False, 'message': str(exc)}, status=400)
            update_fields.append('delivery_fee_charge')

        if not update_fields:
            return JsonResponse({
                'success': False,
                'message': 'No supported View Details field was provided.',
            }, status=400)

        order.save(update_fields=[*update_fields, 'updated_at'])

        return JsonResponse({
            'success': True,
            'rider_name': order.rider_name,
            'delivery_fee_charge': f'{order.delivery_fee_charge:.2f}',
        })
    except Order.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'Order not found'}, status=404)
    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)}, status=500)
