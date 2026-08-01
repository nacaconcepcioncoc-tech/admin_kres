from django.urls import path
from . import views

app_name = "pages"

urlpatterns = [
    # Authentication
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path(
        "employee-standing/verify-pin/",
        views.employee_standing_pin,
        name="employee_standing_pin",
    ),
    path(
        "employee-standing/lock/",
        views.employee_standing_lock,
        name="employee_standing_lock",
    ),

    # Main pages
    path("", views.dashboard, name="dashboard"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("orders/", views.orders, name="orders"),
    path("customers/", views.customers, name="customers"),
    path("payments/", views.payments, name="payments"),
    path("reports/", views.reports, name="reports"),
    path(
        "employee-standing/",
        views.employee_standing,
        name="employee_standing",
    ),

    # AJAX routes
    path(
        "ajax/customers/create/",
        views.customer_create_ajax,
        name="customer_create_ajax",
    ),
    path(
        "ajax/orders/create/",
        views.order_create_ajax,
        name="order_create_ajax",
    ),
    path(
        "ajax/orders/update/",
        views.order_update_ajax,
        name="order_update_ajax",
    ),
    path(
        "ajax/payments/update/",
        views.payment_update_ajax,
        name="payment_update_ajax",
    ),
    path(
        "ajax/payments/update-by-order/",
        views.payment_update_by_order_ajax,
        name="payment_update_by_order_ajax",
    ),
    path(
        "ajax/payments/get-by-order/",
        views.payment_get_by_order_ajax,
        name="payment_get_by_order_ajax",
    ),
    path(
        "ajax/employee-monthly-evaluation/",
        views.employee_monthly_evaluation_ajax,
        name="employee_monthly_evaluation_ajax",
    ),
    path(
        "ajax/orders/export-data/",
        views.orders_export_data_ajax,
        name="orders_export_data_ajax",
    ),
    path(
        "ajax/customers/",
        views.get_customers_ajax,
        name="get_customers_ajax",
    ),
    path(
        "ajax/orders/update-status/",
        views.order_update_status_ajax,
        name="order_update_status_ajax",
    ),
    path(
        "ajax/orders/update-fulfilled/",
        views.order_update_fulfilled_ajax,
        name="order_update_fulfilled_ajax",
    ),
    path(
        "ajax/orders/update-rider/",
        views.order_update_rider_ajax,
        name="order_update_rider_ajax",
    ),

    # Utility routes
    path("clear-all-data/", views.clear_all_data, name="clear_all_data"),
    path("features/", views.features, name="features"),
]
