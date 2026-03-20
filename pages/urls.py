from django.urls import path
from . import views

app_name = 'pages'

urlpatterns = [
    # ========== AUTHENTICATION ==========
    path('', views.login_view, name='login'),
    path('login/', views.login_view, name='login_alt'),
    path('logout/', views.logout_view, name='logout'),
    
    # ========== MAIN PAGE VIEWS ==========
    path('dashboard/', views.dashboard, name='dashboard'),
    path('customers/', views.customers, name='customers'),
    path('inventory/', views.inventory, name='inventory'),
    path('orders/', views.orders, name='orders'),
    path('payments/', views.payments, name='payments'),
    path('reports/', views.reports, name='reports'),
    path('chatbox/', views.chatbox, name='chatbox'),
    path('features/', views.features, name='features'),
    
    # ========== ADMIN UTILITIES ==========
    path('clear-all-data/', views.clear_all_data, name='clear_all_data'),
    
    # ========== AJAX ENDPOINTS ==========
    # Customer
    path('ajax/customer/create/', views.customer_create_ajax, name='customer_create_ajax'),
    path('ajax/customers/list/', views.get_customers_ajax, name='get_customers_ajax'),
    
    # Product/Inventory
    path('ajax/product/create/', views.product_create_ajax, name='product_create_ajax'),
    path('ajax/product/update-stock/', views.product_update_stock_ajax, name='product_update_stock_ajax'),
    path('ajax/product/edit/', views.product_edit_ajax, name='product_edit_ajax'),
    path('ajax/product/delete/', views.product_delete_ajax, name='product_delete_ajax'),
    path('ajax/products/list/', views.get_products_ajax, name='get_products_ajax'),
    
    # Orders
    path('ajax/order/create/', views.order_create_ajax, name='order_create_ajax'),
    path('ajax/order/update-status/', views.order_update_status_ajax, name='order_update_status_ajax'),
    path('ajax/order/update-fulfilled/', views.order_update_fulfilled_ajax, name='order_update_fulfilled_ajax'),
    path('ajax/order/update-rider/', views.order_update_rider_ajax, name='order_update_rider_ajax'),
    
    # Order Photos (Real-time Image Sync)
    path('upload-order-photo/', views.upload_order_photo, name='upload_order_photo'),
    path('get-order-photo/', views.get_order_photo, name='get_order_photo'),
    path('delete-order-photo/', views.delete_order_photo, name='delete_order_photo'),
    
    # Payments
    path('ajax/payment/update/', views.payment_update_ajax, name='payment_update_ajax'),
    path('ajax/payment/update-by-order/', views.payment_update_by_order_ajax, name='payment_update_by_order_ajax'),
    path('ajax/payment/get-by-order/', views.payment_get_by_order_ajax, name='payment_get_by_order_ajax'),
]