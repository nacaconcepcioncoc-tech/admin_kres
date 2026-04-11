from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.db.models import Sum, Count, Q, F, DecimalField, OuterRef, Subquery
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_protect
from django.core.files.storage import default_storage
from django.conf import settings
from datetime import timedelta, datetime
from decimal import Decimal
import json
import logging
from .models import Customer, Product, Order, OrderItem, Payment, StockAlert, MonthlySalesArchive, YearlySalesSnapshot
from .manila_tz_utils import get_manila_timezone, get_manila_today, get_manila_now, is_delivery_tomorrow
from .auto_delete_utils import (
    check_and_delete_completed_orders,
    check_and_delete_yearly_sales_data,
    check_and_delete_orphaned_customers,
    check_and_delete_completed_payments,
    check_and_reset_monthly_stock,
    get_next_month_deletion_date,
    get_next_year_deletion_date
)




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
def login_view(request):
    """Admin login view"""
    logger = logging.getLogger(__name__)
    if request.method == 'POST':
        username_or_email = request.POST.get('email')
        password = request.POST.get('password')
        staff_Id = request.POST.get('staff_Id')
       
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
                request.session.set_expiry(settings.SESSION_COOKIE_AGE)
            else:
                # Expire at browser close when "Remember Me" is not selected.
                request.session.set_expiry(0)

            next_page = request.GET.get('next', '')
            if next_page and url_has_allowed_host_and_scheme(
                url=next_page,
                allowed_hosts={request.get_host()},
                require_https=request.is_secure(),
            ):
                return redirect(next_page)
            return redirect('pages:dashboard')
        else:
            messages.error(request, 'Invalid username/email or password')
   
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
    """Dashboard with real-time statistics from database"""
   
    # Get date ranges
    today = timezone.now().date()
    week_ago = today - timedelta(days=7)
    month_ago = today - timedelta(days=30)
    # First day of current month
    current_month_start = today.replace(day=1)
   
    # Calculate statistics from database
    total_customers = Customer.objects.count()
    total_products = Product.objects.filter(is_active=True).count()
    total_orders = Order.objects.count()
   
    # Revenue calculations from actual payments
    total_revenue = Payment.objects.aggregate(
        total=Sum('amount'))['total'] or Decimal('0.00')
   
    weekly_revenue = Payment.objects.filter(
        payment_status='completed',
        payment_date__gte=week_ago
    ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
   
    monthly_revenue = Payment.objects.filter(
        payment_status='completed',
        payment_date__gte=month_ago
    ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
   
    # Order statistics
    pending_orders = Order.objects.filter(status='pending').count()
    # Completed orders for the current month (from 1st of month to today)
    completed_orders = Order.objects.filter(
        status='completed',
        created_at__gte=timezone.make_aware(datetime.combine(current_month_start, datetime.min.time()))
    ).count()
   
    # Stock alerts
    low_stock_count = Product.objects.filter(
        is_active=True,
        stock_quantity__lte=F('low_stock_threshold')
    ).count()
   
    out_of_stock_count = Product.objects.filter(
        is_active=True,
        stock_quantity=0
    ).count()
   
    # Recent orders from database
    recent_orders = Order.objects.select_related('customer').order_by('-created_at')[:5]
   
    # Active alerts from database
    active_alerts = StockAlert.objects.filter(alert_status='active').select_related('product')[:10]
   
    # Notification data
    new_customers = Customer.objects.filter(created_at__gte=today).count()
    pending_payments = Payment.objects.filter(payment_status='pending').count()
   
    context = {
        'total_customers': total_customers,
        'total_products': total_products,
        'total_orders': total_orders,
        'total_revenue': total_revenue,
        'weekly_revenue': weekly_revenue,
        'monthly_revenue': monthly_revenue,
        'pending_orders': pending_orders,
        'completed_orders': completed_orders,
        'low_stock_count': low_stock_count,
        'out_of_stock_count': out_of_stock_count,
        'recent_orders': recent_orders,
        'active_alerts': active_alerts,
        'new_customers_count': new_customers,
        'pending_payments': pending_payments,
    }
   
    return render(request, 'dashboard.html', context)




# ============================================================================
# CUSTOMERS VIEWS - WITH AJAX SUPPORT
# ============================================================================
@login_required(login_url='login')
def customers(request):
    """List all customers from database with search and status filter"""
   
    # Check if we need to delete orphaned customers from the previous month
    check_and_delete_orphaned_customers()

    search_query = request.GET.get('search', '')
    status_filter = request.GET.get('status', '')
    customers_list = Customer.objects.all()

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
   
    # Notification data
    recent_orders = Order.objects.select_related('customer').order_by('-created_at')[:3]
    low_stock_count = Product.objects.filter(
        is_active=True,
        stock_quantity__lte=F('low_stock_threshold')
    ).count()
    pending_payments = Payment.objects.filter(payment_status='pending').count()
   
    # Get next month's deletion date for warning message
    next_deletion_date = get_next_month_deletion_date()

    context = {
        'customers': customers_list,
        'search_query': search_query,
        'status_filter': status_filter,
        'recent_orders': recent_orders,
        'low_stock_count': low_stock_count,
        'pending_payments': pending_payments,
        'new_customers_count': Customer.objects.filter(created_at__gte=timezone.now().date()).count(),
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




# ============================================================================
# INVENTORY VIEWS - WITH AJAX SUPPORT
# ============================================================================
@login_required(login_url='login')
def inventory(request):
    """List all products from database"""
   
    # Check if we need to reset stock quantities for the new month
    check_and_reset_monthly_stock()

    search_query = request.GET.get('search', '')
    category_filter = request.GET.get('category', '')
    stock_filter = request.GET.get('stock_status', '')
   
    # Get only Flowers and Fillers products, exclude auto-created CUSTOM- products
    products_list = Product.objects.filter(
        is_active=True,
        category__in=['FLOWERS', 'FILLERS']
    ).exclude(sku__startswith='CUSTOM-')
   
    if search_query:
        products_list = products_list.filter(
            Q(name__icontains=search_query) |
            Q(sku__icontains=search_query)
        )
   
    if category_filter:
        products_list = products_list.filter(category=category_filter)
   
    if stock_filter == 'low':
        products_list = products_list.filter(stock_quantity__lte=F('low_stock_threshold'))
    elif stock_filter == 'out':
        products_list = products_list.filter(stock_quantity=0)
   
    products_list = products_list.order_by('name')
   
    # Notification data
    recent_orders = Order.objects.select_related('customer').order_by('-created_at')[:3]
    low_stock_items = Product.objects.filter(
        is_active=True,
        category__in=['FLOWERS', 'FILLERS'],
        stock_quantity__lte=F('low_stock_threshold')
    ).exclude(sku__startswith='CUSTOM-')[:5]
    pending_payments = Payment.objects.filter(payment_status='pending').count()
    new_customers_count = Customer.objects.filter(created_at__gte=timezone.now().date()).count()
    low_stock_count = Product.objects.filter(
        is_active=True,
        category__in=['FLOWERS', 'FILLERS'],
        stock_quantity__lte=F('low_stock_threshold')
    ).exclude(sku__startswith='CUSTOM-').count()
   
    # Get next month's stock reset date for warning message
    next_deletion_date = get_next_month_deletion_date()

    context = {
        'products': products_list,
        'search_query': search_query,
        'category_filter': category_filter,
        'stock_filter': stock_filter,
        'recent_orders': recent_orders,
        'low_stock_items': low_stock_items,
        'low_stock_count': low_stock_count,
        'pending_payments': pending_payments,
        'new_customers_count': new_customers_count,
        'next_deletion_date': next_deletion_date,
    }
   
    return render(request, 'inventory.html', context)




@login_required(login_url='login')
@require_http_methods(["POST"])
@csrf_protect
def product_create_ajax(request):
    """AJAX endpoint to create product"""
    try:
        data = json.loads(request.body)
        import time

        # Auto-generate SKU if not provided
        sku = data.get('sku') or ''
        if not sku:
            cat_prefix = (data.get('category', 'PRD') or 'PRD')[:3].upper()
            name_part = (data.get('name', '') or '').replace(' ', '-').upper()[:10]
            sku = f"{cat_prefix}-{name_part}-{str(int(time.time()))[-4:]}"

        # Create product in database
        product = Product.objects.create(
            name=data.get('name'),
            description='',
            sku=sku,
            category=data.get('category', ''),
            price=Decimal(str(data.get('price', 0))),
            cost_price=None,
            stock_quantity=int(data.get('stock_quantity', 0)),
            low_stock_threshold=int(data.get('low_stock_threshold', 5)),
            unit=data.get('unit', 'pcs'),
            is_active=True
        )
       
        # Check for stock alerts
        StockAlert.check_and_create_alerts()
       
        return JsonResponse({
            'success': True,
            'message': f'Product {product.name} created successfully!',
            'product': {
                'product_id': product.product_id,
                'name': product.name,
                'sku': product.sku,
                'category': product.category,
                'price': float(product.price),
                'stock_quantity': product.stock_quantity,
                'unit': product.unit,
                'stock_status': product.get_stock_status(),
            }
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'message': f'Error creating product: {str(e)}'
        }, status=400)




@login_required(login_url='login')
@require_http_methods(["POST"])
@csrf_protect
def product_update_stock_ajax(request):
    """AJAX endpoint to update product stock"""
    try:
        data = json.loads(request.body)
        product_id = data.get('product_id')
        new_stock = int(data.get('stock_quantity', 0))
       
        # Get product from database
        product = Product.objects.get(product_id=product_id)
        old_stock = product.stock_quantity
       
        # Update stock in database
        product.stock_quantity = new_stock
        product.save()
       
        # Check and create stock alerts
        StockAlert.check_and_create_alerts()
       
        return JsonResponse({
            'success': True,
            'message': f'Stock updated for {product.name}: {old_stock} → {new_stock}',
            'product': {
                'product_id': product.product_id,
                'name': product.name,
                'stock_quantity': product.stock_quantity,
                'low_stock_threshold': product.low_stock_threshold,
                'stock_status': product.get_stock_status(),
            }
        })
    except Product.DoesNotExist:
        return JsonResponse({
            'success': False,
            'message': 'Product not found'
        }, status=404)
    except Exception as e:
        return JsonResponse({
            'success': False,
            'message': f'Error updating stock: {str(e)}'
        }, status=400)




@login_required(login_url='login')
@require_http_methods(["POST"])
@csrf_protect
def product_edit_ajax(request):
    """AJAX endpoint to edit/update a product"""
    try:
        data = json.loads(request.body)
        product_id = data.get('product_id')
        product = Product.objects.get(product_id=product_id)

        if 'name' in data:
            product.name = data['name']
        if 'description' in data:
            product.description = data.get('description', '')
        if 'sku' in data:
            product.sku = data['sku']
        if 'category' in data:
            product.category = data.get('category', '')
        if 'price' in data:
            product.price = Decimal(str(data['price']))
        if 'cost_price' in data and data['cost_price']:
            product.cost_price = Decimal(str(data['cost_price']))
        if 'stock_quantity' in data:
            product.stock_quantity = int(data['stock_quantity'])
        if 'low_stock_threshold' in data:
            product.low_stock_threshold = int(data['low_stock_threshold'])
        if 'unit' in data:
            product.unit = data.get('unit', 'pcs')
        product.save()
        StockAlert.check_and_create_alerts()
        return JsonResponse({
            'success': True,
            'message': f'Product {product.name} updated successfully!',
            'product': {
                'product_id': product.product_id,
                'name': product.name,
                'sku': product.sku,
                'category': product.category,
                'price': float(product.price),
                'stock_quantity': product.stock_quantity,
                'unit': product.unit,
                'stock_status': product.get_stock_status(),
            }
        })
    except Product.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'Product not found'}, status=404)
    except Exception as e:
        return JsonResponse({'success': False, 'message': f'Error updating product: {str(e)}'}, status=400)


@login_required(login_url='login')
@require_http_methods(["POST"])
@csrf_protect
def product_delete_ajax(request):
    """AJAX endpoint to delete a product"""
    try:
        data = json.loads(request.body)
        product_id = data.get('product_id')
        product = Product.objects.get(product_id=product_id)
        name = product.name
        product.delete()
        return JsonResponse({'success': True, 'message': f'Product "{name}" deleted successfully!'})
    except Product.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'Product not found'}, status=404)
    except Exception as e:
        return JsonResponse({'success': False, 'message': f'Error deleting product: {str(e)}'}, status=400)

@login_required(login_url='login')
def orders(request):
    """List all orders from database"""
    
    # Check if we need to delete completed orders from the previous month
    check_and_delete_completed_orders()
   
    search_query = request.GET.get('search', '')
    status_filter = request.GET.get('status', '')
   
    # Get orders from database
    orders_list = Order.objects.select_related('customer').prefetch_related('items__product')
   
    if search_query:
        orders_list = orders_list.filter(
            Q(order_number__icontains=search_query) |
            Q(customer__first_name__icontains=search_query) |
            Q(customer__last_name__icontains=search_query) |
            Q(customer__email__icontains=search_query)
        )
   
    if status_filter:
        orders_list = orders_list.filter(status=status_filter)
   
    orders_list = orders_list.order_by('-created_at')
   
    # Get all completed orders (for completed orders modal, separate from status filter)
    completed_orders = Order.objects.filter(status='completed').select_related('customer').prefetch_related('items__product').order_by('-updated_at')
   
    # Get all products for order creation
    products = Product.objects.filter(is_active=True).order_by('name')
   
    # Notification data
    new_customers = Customer.objects.filter(created_at__gte=timezone.now().date()).count()
    low_stock_count = Product.objects.filter(
        is_active=True,
        stock_quantity__lte=F('low_stock_threshold')
    ).count()
    pending_payments = Payment.objects.filter(payment_status='pending').count()
    
    # Get next month's deletion date for warning message
    next_deletion_date = get_next_month_deletion_date()
    
    # Get Manila timezone info for delivery date filtering
    manila_today = get_manila_today()
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
        'low_stock_count': low_stock_count,
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
       
        # Validate required fields (address not required for pickup)
        delivery_type = data.get('notes', '').startswith('[PICK UP]')
        required_fields = ['customer_email', 'customer_first_name', 'customer_phone', 'items']
        if not delivery_type:
            required_fields.append('customer_address')
        
        for field in required_fields:
            if not data.get(field):
                return JsonResponse({
                    'success': False,
                    'message': f'Missing required field: {field}'
                }, status=400)
       
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
       
        # ======== STEP 2: CREATE ORDER IN DATABASE ========

        # Parse delivery date
        delivery_date_val = None
        raw_delivery_date = data.get('delivery_date', '')
        if raw_delivery_date:
            try:
                delivery_date_val = datetime.strptime(raw_delivery_date, '%Y-%m-%d').date()
            except ValueError:
                pass
        
        # Parse delivery time
        delivery_time_val = None
        raw_delivery_time = data.get('delivery_time', '')
        if raw_delivery_time:
            try:
                delivery_time_val = datetime.strptime(raw_delivery_time, '%H:%M').time()
            except ValueError:
                pass


        order = Order.objects.create(
            customer=customer,
            status='pending',
            notes=data.get('notes', ''),
            tax=Decimal(str(data.get('tax', 0))),
            discount=Decimal(str(data.get('discount', 0))),
            delivery_date=delivery_date_val,
            delivery_time=delivery_time_val,
            customer_phone=data.get('customer_phone', ''),
            customer_address=data.get('customer_address', ''),
            fulfilled_by=data.get('fulfilled_by', ''),
            sender_name=data.get('sender_name', ''),
            sender_phone=data.get('sender_phone', ''),
            rider_name=data.get('rider_name', ''),
            rider_phone=data.get('rider_phone', ''),
            rider_vehicle=data.get('rider_vehicle', ''),
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
                import time
                product = Product.objects.create(
                    name=product_name,
                    sku=f'CUSTOM-{int(time.time())}',
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
        
        # Use payment_amount if provided, otherwise use order total
        if payment_amount:
            payment_amount = Decimal(str(payment_amount))
        else:
            payment_amount = order.total
       
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
                'customer_name': f'{customer.first_name} {customer.last_name}',
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
    except Exception as e:
        logging.getLogger(__name__).exception('Order creation failed')
        return JsonResponse({
            'success': False,
            'message': f'Error creating order: {str(e)}'
        }, status=400)




# ============================================================================
# PAYMENTS VIEWS - WITH AJAX SUPPORT
# ============================================================================
@login_required(login_url='login')
def payments(request):
    """List all payments from database with status filter"""
   
    # Check if we need to delete completed payments from the previous month
    check_and_delete_completed_payments()

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
    customers_transacted = Customer.objects.filter(orders__payments__isnull=False).distinct().count()
    orders_completed = Order.objects.filter(status='completed').count()

    # Notification data
    recent_orders = Order.objects.select_related('customer').order_by('-created_at')[:3]
    new_customers = Customer.objects.filter(created_at__gte=timezone.now().date()).count()
    low_stock_count = Product.objects.filter(
        is_active=True,
        stock_quantity__lte=F('low_stock_threshold')
    ).count()

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
        'low_stock_count': low_stock_count,
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
def payment_update_by_order_ajax(request):
    """AJAX endpoint to update payment status by order ID (does NOT change order status)"""
    try:
        data = json.loads(request.body)
        order_id = data.get('order_id')
        new_payment_status = data.get('payment_status')
        payment_amount = data.get('payment_amount')

        order = Order.objects.get(order_id=order_id)
        payment = order.payments.first()
        if not payment:
            return JsonResponse({'success': False, 'message': 'No payment found for this order'}, status=404)

        if new_payment_status:
            payment.payment_status = new_payment_status
        if payment_amount is not None:
            payment.amount = Decimal(str(payment_amount))
        
        payment.save()

        # Intentionally do NOT change order.status here

        return JsonResponse({
            'success': True,
            'payment': {
                'id': payment.payment_id,
                'payment_number': payment.payment_number,
                'status': payment.payment_status,
                'amount': float(payment.amount),
            }
        })
    except Order.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'Order not found'}, status=404)
    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)}, status=400)


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
    """Generate comprehensive reports from database with strict separation of sales and inventory"""
    
    # Check if we need to delete yearly sales data (on January 1st)
    check_and_delete_yearly_sales_data()
   
    # Get date ranges
    today = timezone.now().date()
    week_ago = today - timedelta(days=7)
    month_ago = today - timedelta(days=30)
   
    # ======== SALES OVERVIEW - FROM ORDER MODEL ONLY ========
    # Total sales from all completed orders
    total_sales = Order.objects.filter(
        status='completed'
    ).aggregate(total=Sum('total'))['total'] or Decimal('0.00')
   
    # Daily sales from completed orders delivered today
    daily_sales = Order.objects.filter(
        delivery_date=today,
        status='completed'
    ).aggregate(
        total_orders=Count('order_id'),
        total_revenue=Sum('total')
    )
    daily_sales['total_orders'] = daily_sales['total_orders'] or 0
    daily_sales['total_revenue'] = daily_sales['total_revenue'] or Decimal('0.00')
   
    # Weekly sales from completed orders delivered in the last 7 days
    weekly_sales = Order.objects.filter(
        delivery_date__gte=week_ago,
        delivery_date__lte=today,
        status='completed'
    ).aggregate(
        total_orders=Count('order_id'),
        total_revenue=Sum('total')
    )
    weekly_sales['total_orders'] = weekly_sales['total_orders'] or 0
    weekly_sales['total_revenue'] = weekly_sales['total_revenue'] or Decimal('0.00')
   
    # Monthly sales from completed orders delivered in current month
    current_month_start = today.replace(day=1)
    if today.month == 12:
        current_month_end = today.replace(year=today.year + 1, month=1, day=1) - timedelta(days=1)
    else:
        current_month_end = today.replace(month=today.month + 1, day=1) - timedelta(days=1)
    
    monthly_sales = Order.objects.filter(
        delivery_date__gte=current_month_start,
        delivery_date__lte=current_month_end,
        status='completed'
    ).aggregate(
        total_orders=Count('order_id'),
        total_revenue=Sum('total')
    )
    monthly_sales['total_orders'] = monthly_sales['total_orders'] or 0
    monthly_sales['total_revenue'] = monthly_sales['total_revenue'] or Decimal('0.00')
   
    # ======== COLLECT MONTHLY SALES DATA BY DAY FOR CALENDAR ========
    from datetime import date as date_class
    
    monthly_sales_by_day = {}
    monthly_orders_by_day = {}
    month_names = ['January', 'February', 'March', 'April', 'May', 'June',
                   'July', 'August', 'September', 'October', 'November', 'December']
    current_year = today.year
   
    for month_num in range(1, 13):
        month_name = month_names[month_num - 1]
        daily_totals = {}
        daily_orders_list = {}
       
        # Get completed orders grouped by ACTUAL delivery date
        month_start = date_class(current_year, month_num, 1)
        if month_num == 12:
            month_end = date_class(current_year + 1, 1, 1) - timedelta(days=1)
        else:
            month_end = date_class(current_year, month_num + 1, 1) - timedelta(days=1)
        
        orders_in_month = Order.objects.filter(
            delivery_date__gte=month_start,
            delivery_date__lte=month_end,
            status='completed'
        ).select_related('customer').order_by('delivery_date', 'updated_at')
        
        # Group orders by delivery date
        for order in orders_in_month:
            order_date = order.delivery_date
            if not order_date:
                continue
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
        
        # MERGE with archived sales data from previous years
        # If this is the current year/month, don't merge archived data (we have live data)
        # For past months/years, pull archived data if available
        if month_num < current_year or (month_num == current_year and month_num < today.month):
            # This is a past month, check if we have archived data
            # Note: For current year's past months, we might have live data, but we still check archive as fallback
            pass  # Keep the live data from above
        
        # Check if there's archived data for this month (from previous years)
        # Only use archive if this is NOT the current month/year
        if month_num != today.month or current_year > today.year:
            archived_data = MonthlySalesArchive.objects.filter(
                month_name=month_name,
                year=current_year
            ).first()
            
            # If no live data but archive exists, use archive
            if not daily_totals and archived_data:
                daily_totals = dict(archived_data.sales_by_day)
                daily_orders_list = dict(archived_data.orders_by_day)
       
        monthly_sales_by_day[month_name] = daily_totals
        monthly_orders_by_day[month_name] = daily_orders_list
   
    # ======== SALES BREAKDOWN - PAYMENT METHOD DISTRIBUTION (FROM PAYMENT MODEL) ========
    payment_methods = Payment.objects.filter(
        payment_status='completed'
    ).values('payment_method').annotate(
        total=Sum('amount'),
        count=Count('payment_id')
    ).order_by('-total')
   
    # ======== SALES PERFORMANCE OVERVIEW - ALL ORDERED PRODUCTS (FROM ORDERITEM MODEL) ========
    # Aggregate all order items by product_name directly — no Product table lookup needed
    top_selling_bouquets = OrderItem.objects.values(
        'product_name'
    ).annotate(
        total_quantity=Sum('quantity'),
        total_revenue=Sum(F('quantity') * F('unit_price'), output_field=DecimalField())
    ).order_by('-total_quantity')

    # Build a simple list with .name, .total_quantity, .total_revenue for the template
    top_selling_products = []
    for item in top_selling_bouquets:
        obj = type('OrderedProduct', (), {
            'name': item['product_name'],
            'total_quantity': item['total_quantity'],
            'total_revenue': item['total_revenue'],
        })()
        top_selling_products.append(obj)
   
    # ======== INVENTORY SUMMARY - OPERATIONAL OVERVIEW (FROM PRODUCT MODEL ONLY) ========
    # Total inventory items
    total_products = Product.objects.filter(is_active=True).count()
   
    # Total remaining (current) stocks
    total_stock = Product.objects.filter(
        is_active=True
    ).aggregate(total=Sum('stock_quantity'))['total'] or 0
   
    # Low stock items count
    low_stock_items = Product.objects.filter(
        is_active=True,
        stock_quantity__lte=F('low_stock_threshold'),
        stock_quantity__gt=0
    ).order_by('stock_quantity')
    low_stock_count = low_stock_items.count()
   
    # Out of stock items count
    out_of_stock_items = Product.objects.filter(
        is_active=True,
        stock_quantity=0
    )
    out_of_stock_count = out_of_stock_items.count()
   
    # Get all products with stock status for inventory table (exclude auto-created CUSTOM products from orders)
    all_products = Product.objects.filter(is_active=True).exclude(sku__startswith='CUSTOM-').order_by('name')
    products_with_status = []
    for product in all_products:
        product.stock_status = product.get_stock_status()
        products_with_status.append(product)

    # Notification context
    pending_payments = Payment.objects.filter(payment_status='pending').count()
    new_customers_count = Customer.objects.filter(created_at__date=today).count()
    
    # Get next year's deletion date for warning message
    next_year_deletion_date = get_next_year_deletion_date()

    context = {
        # Sales Overview
        'total_sales': total_sales,
        'daily_sales': daily_sales,
        'weekly_sales': weekly_sales,
        'monthly_sales': monthly_sales,
        'monthly_sales_by_day': monthly_sales_by_day,
        'monthly_orders_by_day': monthly_orders_by_day,
        
        # Sales Breakdown
        'payment_methods': payment_methods,
        
        # Sales Performance Overview
        'top_selling_products': top_selling_products,
        
        # Inventory Summary - Operational Overview
        'total_products': total_products,
        'total_stock': total_stock,
        'low_stock_count': low_stock_count,
        'out_of_stock_count': out_of_stock_count,
        'all_products': products_with_status,
        
        # Notifications
        'pending_payments': pending_payments,
        'new_customers_count': new_customers_count,
        
        # Auto-deletion dates
        'next_year_deletion_date': next_year_deletion_date,
    }
   
    return render(request, 'reports.html', context)




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
                'customer': f"{order.customer.first_name} {order.customer.last_name}".strip(),
                'items': items_text,
                'amount': float(order.total or 0),
                'status': status_text,
            }
        )

    return JsonResponse({'success': True, 'orders': export_rows})


@require_http_methods(["GET"])
@login_required(login_url='login')
def get_products_ajax(request):
    """Get all products as JSON"""
    products = Product.objects.filter(is_active=True).values(
        'product_id', 'name', 'sku', 'price', 'stock_quantity', 'category'
    )
    return JsonResponse(list(products), safe=False)




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
def features(request):
    """Features page"""
    return render(request, 'features.html')

# ── Helper: shared notification context ─────────────────────────────
def get_notification_context():
    from django.utils import timezone
    today = timezone.now().date()
    low_stock_count    = Product.objects.filter(is_active=True, stock_quantity__lte=10).count()
    pending_payments   = Payment.objects.filter(payment_status='pending').count()
    new_customers_count = Customer.objects.filter(created_at__date=today).count()
    return {
        'low_stock_count':    low_stock_count,
        'pending_payments':   pending_payments,
        'new_customers_count': new_customers_count,
    }


# ── AJAX: Update order status ────────────────────────────────────────
@login_required(login_url='login')
@require_http_methods(["POST"])
@csrf_protect
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

        order = Order.objects.get(order_id=order_id)
        order.status = new_status
        order.save()

        # When order is marked as completed, also mark the payment as completed (fully paid)
        if new_status == 'completed':
            payment = order.payments.first()
            if payment:
                payment.payment_status = 'completed'
                payment.save()

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
    """Save rider name and phone for an order."""
    try:
        data = json.loads(request.body)
        order_id    = data.get('order_id')
        rider_name  = data.get('rider_name', '').strip()
        rider_phone = data.get('rider_phone', '').strip()

        order = Order.objects.get(order_id=order_id)
        order.rider_name  = rider_name
        order.rider_phone = rider_phone
        order.save()

        return JsonResponse({'success': True})
    except Order.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'Order not found'}, status=404)
    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)}, status=500)




# ============================================================================
# ORDER PHOTO MANAGEMENT (Real-time Image Sync)
# ============================================================================
import os
import hashlib

def get_order_photo_prefix(order_id):
    """Get the storage prefix for order photos (works with both local and S3)"""
    return f'order_photos/{order_id}'

def get_order_photo_url(order_id, filename):
    """Get the URL for accessing an order photo (works with both local and S3)"""
    storage_path = f'{get_order_photo_prefix(order_id)}/{filename}'
    return default_storage.url(storage_path)

def get_absolute_media_url(request, storage_path):
    """
    Convert a storage URL to an absolute URL.
    - For S3/cloud storage: default_storage.url() already returns an absolute URL
    - For local storage: default_storage.url() returns a relative path like '/media/...'
      which needs to be prefixed with the request's origin to work across devices
    """
    url = default_storage.url(storage_path)
    if url.startswith('/'):
        # Local storage - convert relative URL to absolute using request origin
        return f'{request.scheme}://{request.get_host()}{url}'
    return url

def generate_photo_hash(file_obj):
    """Generate MD5 hash of photo file"""
    hasher = hashlib.md5()
    file_obj.seek(0)
    for chunk in iter(lambda: file_obj.read(8192), b''):
        hasher.update(chunk)
    return hasher.hexdigest()

@login_required(login_url='login')
@require_http_methods(["POST"])
@csrf_protect
def upload_order_photo(request):
    """
    AJAX endpoint to upload a photo for an order.
    - Works with: Local filesystem OR AWS S3 (auto-detected via settings)
    - Receives: order_id (POST data), photo (file)
    - Returns: JSON with success flag and photo_hash for change detection
    - Stores: Photo in order_photos/{order_id}/ with timestamp
    - Polling: Client will poll /get-order-photo/ to detect updates from other devices
    """
    try:
        order_id = request.POST.get('order_id')
        photo_file = request.FILES.get('photo')

        if not order_id or not photo_file:
            return JsonResponse({'success': False, 'message': 'Missing order_id or photo'}, status=400)

        # Verify order exists
        try:
            order = Order.objects.get(order_id=order_id)
        except Order.DoesNotExist:
            return JsonResponse({'success': False, 'message': 'Order not found'}, status=404)

        photo_prefix = get_order_photo_prefix(order_id)
        
        # Delete existing photos for this order (keep only latest)
        try:
            existing_files = default_storage.listdir(photo_prefix)
            if existing_files and existing_files[1]:  # existing_files = (dirs, files)
                for existing_file in existing_files[1]:
                    file_path = f'{photo_prefix}/{existing_file}'
                    default_storage.delete(file_path)
        except (OSError, FileNotFoundError):
            pass

        # Save new photo with timestamp in filename
        timestamp = int(timezone.now().timestamp() * 1000)  # milliseconds
        original_name = photo_file.name
        name_parts = original_name.rsplit('.', 1)
        extension = f'.{name_parts[1]}' if len(name_parts) > 1 else ''
        new_filename = f"photo_{timestamp}{extension}"
        storage_path = f'{photo_prefix}/{new_filename}'

        # Save file using Django's storage backend
        photo_file.seek(0)
        saved_path = default_storage.save(storage_path, photo_file)

        # Generate hash
        photo_file.seek(0)
        photo_hash = generate_photo_hash(photo_file)

        return JsonResponse({
            'success': True,
            'photo_url': get_absolute_media_url(request, saved_path),
            'photo_hash': photo_hash,
            'photo_name': original_name
        })
    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)}, status=500)


@require_http_methods(["GET"])
@login_required(login_url='login')
def get_order_photo(request):
    """
    AJAX endpoint to retrieve a photo for an order.
    - Works with: Local filesystem OR AWS S3 (auto-detected via settings)
    - Receives: order_id (query param)
    - Returns: JSON with photo_url, photo_hash, and photo_name
    - Used by: Polling mechanism (3-second intervals) to detect cross-device updates
    - Hash comparison: Client compares hash to detect changes from other devices
    """
    try:
        order_id = request.GET.get('order_id')

        if not order_id:
            return JsonResponse({'success': False, 'message': 'Missing order_id'}, status=400)

        # Verify order exists
        try:
            order = Order.objects.get(order_id=order_id)
        except Order.DoesNotExist:
            return JsonResponse({'success': False, 'message': 'Order not found'}, status=404)

        photo_prefix = get_order_photo_prefix(order_id)

        try:
            dirs, files = default_storage.listdir(photo_prefix)
            
            if not files:
                return JsonResponse({'success': True, 'photo_url': None, 'photo_hash': None, 'photo_name': None})

            # Get most recently modified file
            latest_file = max(files, key=lambda f: default_storage.get_modified_time(f'{photo_prefix}/{f}'))
            storage_path = f'{photo_prefix}/{latest_file}'

            # Generate hash from current file
            with default_storage.open(storage_path, 'rb') as f:
                hasher = hashlib.md5()
                for chunk_data in iter(lambda: f.read(8192), b''):
                    hasher.update(chunk_data)
                photo_hash = hasher.hexdigest()

            return JsonResponse({
                'success': True,
                'photo_url': get_absolute_media_url(request, storage_path),
                'photo_hash': photo_hash,
                'photo_name': latest_file
            })
        except (OSError, FileNotFoundError):
            return JsonResponse({'success': True, 'photo_url': None, 'photo_hash': None, 'photo_name': None})

    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)}, status=500)


@login_required(login_url='login')
@require_http_methods(["POST"])
@csrf_protect
def delete_order_photo(request):
    """
    AJAX endpoint to delete a photo for an order.
    - Works with: Local filesystem OR AWS S3 (auto-detected via settings)
    - Receives: order_id (JSON body)
    - Returns: JSON with success flag
    - Deletes: All photos in order's photo directory
    """
    try:
        data = json.loads(request.body)
        order_id = data.get('order_id')

        if not order_id:
            return JsonResponse({'success': False, 'message': 'Missing order_id'}, status=400)

        # Verify order exists
        try:
            order = Order.objects.get(order_id=order_id)
        except Order.DoesNotExist:
            return JsonResponse({'success': False, 'message': 'Order not found'}, status=404)

        photo_prefix = get_order_photo_prefix(order_id)

        # Delete all photos in the prefix
        try:
            dirs, files = default_storage.listdir(photo_prefix)
            for file in files:
                file_path = f'{photo_prefix}/{file}'
                default_storage.delete(file_path)
        except (OSError, FileNotFoundError):
            pass

        return JsonResponse({'success': True})
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'message': 'Invalid JSON'}, status=400)
    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)}, status=500)
