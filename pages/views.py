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
from django.views.decorators.cache import never_cache
from django.urls import reverse
from django.conf import settings
from django.db import transaction, connection
from django.db.utils import OperationalError, ProgrammingError
from datetime import timedelta, datetime
from functools import wraps
import calendar
from decimal import Decimal
import json
import logging
from urllib.parse import urlencode
from .models import Customer, Product, Order, OrderItem, Payment, StockAlert, MonthlySalesArchive, YearlySalesSnapshot
from .models import Employee, PerformanceRecord, EmployeeMonthlyPerformance, EmployeeStandingPin
from .manila_tz_utils import get_manila_timezone, get_manila_today, get_manila_now, is_delivery_tomorrow
from .auto_delete_utils import (
    get_next_month_deletion_date,
    get_next_year_deletion_date
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

    total_revenue = current_month_orders.filter(
        status='completed'
    ).aggregate(total=Sum('total'))['total'] or Decimal('0.00')

    completed_orders = current_month_orders.filter(
        status='completed'
    ).count()

    customers_transacted_this_month = current_month_orders.values(
        'customer_id'
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
        'pending_payments': Payment.objects.filter(
            payment_status='pending'
        ).count(),
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
    pending_payments = Payment.objects.filter(payment_status='pending').count()
   
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
            data['sender_address'] = ''
       
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
    orders_list = Order.objects.select_related('customer').prefetch_related('items__product')
   
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
        orders_list = orders_list.order_by('delivery_time', 'order_id')
    else:
        orders_list = orders_list.order_by('-created_at')
   
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
    pending_payments = Payment.objects.filter(payment_status='pending').count()
    
    # Get next month's deletion date for warning message
    next_deletion_date = get_next_month_deletion_date()
    
    # Get Manila timezone info for delivery date filtering
    manila_tomorrow = manila_today + timedelta(days=1)
    
    # Add Manila timezone delivery info to context
    orders_with_delivery_info = []
    tomorrow_count = 0
    for order in orders_list:
        if order.delivery_date == manila_tomorrow:
            tomorrow_count += 1
        orders_with_delivery_info.append(order)
   
    context = {
        'orders': orders_list,
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
        delivery_type = data.get('notes', '').startswith('[PICK UP]')
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

        required_fields = ['customer_email', 'customer_first_name', 'customer_phone', 'items']
        for field in required_fields:
            if not data.get(field):
                return JsonResponse({
                    'success': False,
                    'message': f'Missing required field: {field}'
                }, status=400)

        if not sender_name_input or not sender_phone_input:
            return JsonResponse({'success': False, 'message': 'Sender name and phone are required.'}, status=400)

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
                'address': data.get('sender_address', data.get('customer_address', '')),
            }
        )
       
        customer_created = created
        if not created:
            customer.first_name = data.get('customer_first_name', customer.first_name)
            customer.last_name = data.get('customer_last_name', customer.last_name)
            customer.phone = data.get('customer_phone', customer.phone)
            if data.get('sender_address') or data.get('customer_address'):
                customer.address = data.get('sender_address', data.get('customer_address', ''))
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

        try:
            balance_payment = Decimal(str(data.get('balance_payment', 0) or 0))
            delivery_fee_charge = Decimal(str(data.get('delivery_fee_charge', 0) or 0))
        except Exception:
            return JsonResponse({
                'success': False,
                'message': 'Balance payment and delivery fee must be valid monetary amounts.'
            }, status=400)

        if balance_payment < 0 or delivery_fee_charge < 0:
            return JsonResponse({
                'success': False,
                'message': 'Balance payment and delivery fee cannot be negative.'
            }, status=400)

        sender_name = sender_name_input
        sender_phone = sender_phone_input
        sender_address = str(data.get('sender_address', '')).strip()
        receiver_name = receiver_name_input
        receiver_phone = receiver_phone_input
        receiver_address = receiver_address_input
        if delivery_type:
            receiver_name = receiver_name or sender_name
            receiver_phone = receiver_phone or sender_phone
            receiver_address = ''

        if not sender_name or not sender_phone:
            return JsonResponse({'success': False, 'message': 'Sender name and phone are required.'}, status=400)
        if not delivery_type and (not receiver_name or not receiver_phone):
            return JsonResponse({'success': False, 'message': 'Receiver name and phone are required.'}, status=400)

        monthly_order_number = _get_next_monthly_order_number()

        order = Order.objects.create(
            order_number=monthly_order_number,
            customer=customer,
            status='pending',
            notes=data.get('notes', ''),
            tax=Decimal(str(data.get('tax', 0))),
            discount=Decimal(str(data.get('discount', 0))),
            delivery_date=delivery_date_val,
            delivery_time=delivery_time_val,
            receiver_name=receiver_name,
            customer_phone=receiver_phone,
            customer_address=receiver_address,
            delivery_address=data.get('delivery_address', receiver_address),
            fulfilled_by=data.get('fulfilled_by', ''),
            sender_name=sender_name,
            sender_phone=sender_phone,
            sender_address=sender_address,
            sender_is_receiver=bool(data.get('sender_is_receiver', False)),
            rider_name=data.get('rider_name', ''),
            rider_phone=data.get('rider_phone', ''),
            rider_vehicle=data.get('rider_vehicle', ''),
            balance_payment=balance_payment,
            additional_payment=str(data.get('additional_payment', '') or '').strip()[:255],
            delivery_fee_charge=delivery_fee_charge,
        )
        # ======== STEP 3: ADD ORDER ITEMS TO DATABASE ========
        items_data = data.get('items', [])
       
        for item_data in items_data:
            product_name = item_data.get('product_name', 'Custom Product')
            unit_price = Decimal(str(item_data.get('unit_price', 0)))
           
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
        payment_status = data.get('payment_status', 'pending')
        payment_amount = data.get('payment_amount')

        valid_payment_methods = {choice[0] for choice in Payment.PAYMENT_METHOD_CHOICES}
        valid_payment_statuses = {choice[0] for choice in Payment.PAYMENT_STATUS_CHOICES}
        if payment_method not in valid_payment_methods:
            payment_method = 'cash'
        if payment_status not in valid_payment_statuses:
            payment_status = 'pending'
        
        # Use payment_amount if provided, otherwise use order total
        if payment_amount not in (None, ''):
            payment_amount = Decimal(str(payment_amount))
        else:
            payment_amount = order.total
        if payment_amount < 0 or payment_amount > order.total:
            return JsonResponse({'success': False, 'message': 'Payment amount must be between 0 and the order total.'}, status=400)
       
        payment = Payment.objects.create(
            order=order,
            amount=payment_amount,
            payment_method=payment_method,
            payment_status=payment_status,
            notes=f'Auto-generated payment for order {order.order_number}'
        )
       
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
                'total': float(order.total),
                'created_at': order.created_at.strftime('%Y-%m-%d %H:%M:%S'),
            },
            'payment': {
                'id': payment.payment_id,
                'payment_number': payment.payment_number,
                'amount': float(payment.amount),
                'method': payment.payment_method,
                'status': payment.payment_status,
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
            Order.objects.select_related('customer')
            .prefetch_related('items', 'payments')
            .get(order_id=order_id)
        )

        sender_name = str(data.get('sender_name', order.sender_name or '')).strip()
        sender_phone = str(data.get('sender_phone', order.sender_phone or '')).strip()
        sender_address = str(data.get('sender_address', order.sender_address or '')).strip()
        raw_same = data.get('sender_is_receiver', order.sender_is_receiver)
        sender_is_receiver = raw_same in (True, 1, '1', 'true', 'True', 'on', 'yes')

        receiver_name = str(data.get('receiver_name', order.receiver_name or '')).strip()
        receiver_phone = str(data.get('receiver_phone', order.customer_phone or '')).strip()
        receiver_address = str(data.get('receiver_address', order.customer_address or '')).strip()

        if sender_is_receiver:
            receiver_name = sender_name
            receiver_phone = sender_phone
            receiver_address = str(
                data.get('delivery_address')
                or data.get('customer_address')
                or order.delivery_address
                or order.customer_address
                or ''
            ).strip()
            sender_address = ''

        if not sender_name or not sender_phone:
            return JsonResponse({'success': False, 'message': 'Sender name and phone are required.'}, status=400)
        if not receiver_name or not receiver_phone:
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
        customer.address = sender_address
        email = str(data.get('customer_email', customer.email or '')).strip()
        if email:
            customer.email = email
        customer.save()

        order.receiver_name = receiver_name
        order.customer_phone = receiver_phone
        order.customer_address = receiver_address
        order.delivery_address = str(data.get('delivery_address', order.delivery_address or '')).strip()
        order.sender_name = sender_name
        order.sender_phone = sender_phone
        order.sender_address = sender_address
        order.sender_is_receiver = sender_is_receiver
        order.notes = str(data.get('notes', order.notes or '')).strip()
        order.fulfilled_by = str(data.get('fulfilled_by', order.fulfilled_by or '')).strip()
        order.rider_name = str(data.get('rider_name', order.rider_name or '')).strip()
        order.rider_phone = str(data.get('rider_phone', order.rider_phone or '')).strip()
        order.rider_vehicle = str(data.get('rider_vehicle', order.rider_vehicle or '')).strip()

        try:
            balance_payment = Decimal(str(data.get('balance_payment', order.balance_payment) or 0))
            delivery_fee_charge = Decimal(str(data.get('delivery_fee_charge', order.delivery_fee_charge) or 0))
        except Exception:
            return JsonResponse({
                'success': False,
                'message': 'Balance payment and delivery fee must be valid monetary amounts.'
            }, status=400)

        if balance_payment < 0 or delivery_fee_charge < 0:
            return JsonResponse({
                'success': False,
                'message': 'Balance payment and delivery fee cannot be negative.'
            }, status=400)

        order.balance_payment = balance_payment
        order.additional_payment = str(
            data.get('additional_payment', order.additional_payment or '') or ''
        ).strip()[:255]
        order.delivery_fee_charge = delivery_fee_charge
        order.save()

        items = data.get('items')
        if isinstance(items, list) and items:
            item_data = items[0]
            product_name = str(item_data.get('product_name', '')).strip()
            if not product_name:
                return JsonResponse({'success': False, 'message': 'Order item is required.'}, status=400)
            try:
                quantity = int(item_data.get('quantity', 1))
                unit_price = Decimal(str(item_data.get('unit_price', 0)))
            except (TypeError, ValueError):
                return JsonResponse({'success': False, 'message': 'Invalid quantity or price.'}, status=400)
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

        payment = order.payments.first()
        if payment:
            method = str(data.get('payment_method', payment.payment_method)).strip()
            status = str(data.get('payment_status', payment.payment_status)).strip()
            valid_methods = {choice[0] for choice in Payment.PAYMENT_METHOD_CHOICES}
            valid_statuses = {choice[0] for choice in Payment.PAYMENT_STATUS_CHOICES}
            if method not in valid_methods:
                return JsonResponse({'success': False, 'message': 'Invalid payment method.'}, status=400)
            if status not in valid_statuses:
                return JsonResponse({'success': False, 'message': 'Invalid payment status.'}, status=400)
            payment.payment_method = method
            payment.payment_status = status
            if data.get('payment_amount') not in (None, ''):
                amount = Decimal(str(data.get('payment_amount')))
                if amount < 0 or amount > order.total:
                    return JsonResponse({'success': False, 'message': 'Payment amount must be between 0 and the order total.'}, status=400)
                payment.amount = amount
            payment.save()

        return JsonResponse({
            'success': True,
            'message': f'Order {order.order_number} updated successfully.',
            'order': {
                'id': order.order_id,
                'order_number': order.order_number,
                'sender_is_receiver': order.sender_is_receiver,
                'total': float(order.total or 0),
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
    payments_list = Payment.objects.select_related('order', 'order__customer')
   
    if search_query:
        payments_list = payments_list.filter(
            Q(payment_number__icontains=search_query) |
            Q(order__order_number__icontains=search_query) |
            Q(transaction_id__icontains=search_query)
        )
   
    # Filter by payment status
    if status_filter == 'pending':
        payments_list = payments_list.filter(payment_status='pending')
    elif status_filter == 'completed':
        payments_list = payments_list.filter(payment_status='completed')
   
    if method_filter:
        payments_list = payments_list.filter(payment_method=method_filter)
   
    payments_list = payments_list.order_by('-payment_date')
   
    # Total revenue = sum of ALL payment amounts (all sales including pending and completed)
    total_revenue = Payment.objects.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

    pending_payments_count = Payment.objects.filter(payment_status='pending').count()

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

    customers_transacted = Customer.objects.filter(
        orders__payments__payment_date__gte=month_start_datetime,
        orders__payments__payment_date__lt=next_month_start_datetime,
    ).distinct().count()

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
        'payments': payments_list,
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
        payment.payment_status = data.get('payment_status', payment.payment_status)
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
    """Update payment status or amount for an order.

    When a remaining balance is confirmed as paid, the payment becomes Fully Paid,
    the payment amount is set to the order total, and Balance Payment becomes zero.
    """
    try:
        data = json.loads(request.body or b'{}')
        order_id = data.get('order_id')
        new_payment_status = data.get('payment_status')
        payment_amount = data.get('payment_amount')
        balance_payment = data.get('balance_payment')
        additional_payment = data.get('additional_payment')
        mark_balance_paid = data.get('mark_balance_paid') in (
            True, 1, '1', 'true', 'True', 'on', 'yes'
        )

        if not order_id:
            return JsonResponse({
                'success': False,
                'message': 'order_id is required.'
            }, status=400)

        order = Order.objects.select_for_update().get(order_id=order_id)
        payment = order.payments.select_for_update().first()

        if not payment:
            return JsonResponse({
                'success': False,
                'message': 'No payment found for this order.'
            }, status=404)

        valid_statuses = {choice[0] for choice in Payment.PAYMENT_STATUS_CHOICES}

        if new_payment_status:
            if new_payment_status not in valid_statuses:
                return JsonResponse({
                    'success': False,
                    'message': 'Invalid payment status.'
                }, status=400)
            payment.payment_status = new_payment_status

        if payment_amount is not None:
            try:
                parsed_amount = Decimal(str(payment_amount))
            except Exception:
                return JsonResponse({
                    'success': False,
                    'message': 'Payment amount must be a valid monetary amount.'
                }, status=400)

            if parsed_amount < 0 or parsed_amount > order.total:
                return JsonResponse({
                    'success': False,
                    'message': 'Payment amount must be between 0 and the order total.'
                }, status=400)

            payment.amount = parsed_amount

        if balance_payment is not None:
            try:
                parsed_balance = Decimal(str(balance_payment or 0))
            except Exception:
                return JsonResponse({
                    'success': False,
                    'message': 'Balance Payment must be a valid monetary amount.'
                }, status=400)

            if parsed_balance < 0:
                return JsonResponse({
                    'success': False,
                    'message': 'Balance Payment cannot be negative.'
                }, status=400)

            order.balance_payment = parsed_balance
            order.save(update_fields=['balance_payment', 'updated_at'])

        if additional_payment is not None:
            order.additional_payment = str(additional_payment or '').strip()[:255]
            order.save(update_fields=['additional_payment', 'updated_at'])

        if payment.payment_status == 'completed' and mark_balance_paid:
            payment.amount = order.total
            order.balance_payment = Decimal('0.00')
            order.save(update_fields=['balance_payment', 'updated_at'])

        payment.save()

        return JsonResponse({
            'success': True,
            'balance_payment': float(order.balance_payment or 0),
            'additional_payment': order.additional_payment or '',
            'payment': {
                'id': payment.payment_id,
                'payment_number': payment.payment_number,
                'status': payment.payment_status,
                'amount': float(payment.amount),
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
    """AJAX endpoint to get payment information by order ID"""
    try:
        order_id = request.GET.get('order_id')
        if not order_id:
            return JsonResponse({'success': False, 'message': 'order_id is required'}, status=400)
        
        order = Order.objects.get(order_id=order_id)
        payment = order.payments.first()
        
        if not payment:
            return JsonResponse({'success': False, 'message': 'No payment found for this order'}, status=404)
        
        return JsonResponse({
            'success': True,
            'payment': {
                'id': payment.payment_id,
                'payment_number': payment.payment_number,
                'amount': float(payment.amount),
                'payment_method': payment.payment_method,
                'payment_status': payment.payment_status,
            }
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
    """Generate reports using live completed-order data in Manila time."""

    # Preserve the existing monthly/yearly archival workflow before building
    # the live current-month report. Archived historical report data is kept.

    today = get_manila_today()
    current_month_start = today.replace(day=1)
    if today.month == 12:
        next_month_start = today.replace(year=today.year + 1, month=1, day=1)
    else:
        next_month_start = today.replace(month=today.month + 1, day=1)

    # Current-month completed orders are the single source of truth for the
    # Total Monthly Sales card and calendar. Changes in Orders are reflected
    # immediately whenever this page is loaded or refreshed.
    current_month_orders = (
        Order.objects.filter(
            delivery_date__gte=current_month_start,
            delivery_date__lt=next_month_start,
            status='completed',
        )
        .select_related('customer')
        .order_by('delivery_date', 'updated_at', 'order_id')
    )

    total_monthly_sales = current_month_orders.aggregate(
        total_orders=Count('order_id'),
        total_revenue=Sum('total'),
    )
    total_monthly_sales['total_orders'] = total_monthly_sales['total_orders'] or 0
    total_monthly_sales['total_revenue'] = total_monthly_sales['total_revenue'] or Decimal('0.00')

    current_month_sales_by_day = {}
    current_month_orders_by_day = {}

    for order in current_month_orders:
        order_date = order.delivery_date
        if not order_date:
            continue

        day_key = str(order_date.day)
        current_month_sales_by_day.setdefault(day_key, 0.0)
        current_month_orders_by_day.setdefault(day_key, [])

        current_month_sales_by_day[day_key] += float(order.total or 0)
        current_month_orders_by_day[day_key].append({
            'order_number': order.order_number,
            'customer_name': order.customer_name,
            'total': float(order.total or 0),
            'order_id': order.order_id,
            'order_date': order_date.isoformat(),
        })

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

    pending_payments = Payment.objects.filter(payment_status='pending').count()
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

        if order.status == 'completed':
            status_text = 'Completed'
        else:
            payment = order.payments.first()
            if payment and payment.payment_status == 'completed':
                status_text = 'Fully Paid'
            else:
                status_text = 'Down Payment'

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
    pending_payments   = Payment.objects.filter(payment_status='pending').count()
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
    """AJAX endpoint to update an order's status. When completed, also mark payment as completed."""
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

        # When order is marked as completed, also mark the payment as completed (fully paid)
        if new_status == 'completed':
            payment = order.payments.select_for_update().first()
            if payment:
                payment.payment_status = 'completed'
                payment.amount = order.total
                payment.save(update_fields=['payment_status', 'amount', 'updated_at'])
            order.balance_payment = Decimal('0.00')
            order.save(update_fields=['balance_payment', 'updated_at'])

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
def order_update_rider_ajax(request):
    """Save rider name and manually entered delivery fee charge for an order."""
    try:
        data = json.loads(request.body)
        order_id = data.get('order_id')
        rider_name = str(data.get('rider_name', '')).strip()

        try:
            delivery_fee_charge = Decimal(str(data.get('delivery_fee_charge', 0) or 0))
        except Exception:
            return JsonResponse({
                'success': False,
                'message': 'Delivery fee charge must be a valid monetary amount.'
            }, status=400)

        if delivery_fee_charge < 0:
            return JsonResponse({
                'success': False,
                'message': 'Delivery fee charge cannot be negative.'
            }, status=400)

        order = Order.objects.get(order_id=order_id)
        order.rider_name = rider_name
        order.delivery_fee_charge = delivery_fee_charge
        order.save(update_fields=['rider_name', 'delivery_fee_charge', 'updated_at'])

        return JsonResponse({
            'success': True,
            'delivery_fee_charge': float(order.delivery_fee_charge)
        })
    except Order.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'Order not found'}, status=404)
    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)}, status=500)
