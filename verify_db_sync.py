#!/usr/bin/env python
"""
Database Verification and Sync Script
Verifies that all records are properly synced and connected in the Django database
"""

import os
import sys
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'storefront.settings')
django.setup()

from django.contrib.auth.models import User
from django.db import connection
from pages.models import Customer, Product, Order, OrderItem, Payment, StockAlert

def print_section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print('='*60)

def check_database_connection():
    """Verify database connection is working"""
    print_section("DATABASE CONNECTION CHECK")
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            result = cursor.fetchone()
            if result:
                print("✓ Database connection: ACTIVE")
                print(f"✓ Database file: {connection.settings_dict['NAME']}")
                print(f"✓ Database engine: {connection.settings_dict['ENGINE']}")
                return True
    except Exception as e:
        print(f"✗ Database connection FAILED: {e}")
        return False

def check_admin_users():
    """Check if admin/superuser exists"""
    print_section("DJANGO ADMIN USERS")
    
    admins = User.objects.filter(is_staff=True)
    superusers = User.objects.filter(is_superuser=True)
    
    print(f"Total Admin Users: {admins.count()}")
    print(f"Total Superusers: {superusers.count()}")
    
    if admins.count() > 0:
        print("\nAdmin Accounts:")
        for admin in admins:
            status = "✓ Active" if admin.is_active else "✗ Inactive"
            su_badge = " [SUPERUSER]" if admin.is_superuser else ""
            print(f"  - {admin.username} ({status}){su_badge}")
    else:
        print("✗ WARNING: No admin users found!")
        return False
    
    return True

def check_data_records():
    """Check record counts in all tables"""
    print_section("DATA RECORD COUNTS")
    
    records = {
        'Customers': Customer.objects.count(),
        'Products': Product.objects.count(),
        'Orders': Order.objects.count(),
        'Order Items': OrderItem.objects.count(),
        'Payments': Payment.objects.count(),
        'Stock Alerts': StockAlert.objects.count(),
    }
    
    total = 0
    for name, count in records.items():
        print(f"  {name:.<40} {count:>5}")
        total += count
    
    print(f"  {'Total Records':.<40} {total:>5}")
    return total > 0

def check_data_integrity():
    """Check for orphaned or broken relationships"""
    print_section("DATA INTEGRITY CHECK")
    
    issues = []
    
    # Check for order items without orders
    orphaned_items = OrderItem.objects.filter(order__isnull=True).count()
    if orphaned_items > 0:
        issues.append(f"Orphaned Order Items: {orphaned_items}")
    else:
        print("✓ No orphaned Order Items")
    
    # Check for payments without orders
    orphaned_payments = Payment.objects.filter(order__isnull=True).count()
    if orphaned_payments > 0:
        issues.append(f"Orphaned Payments: {orphaned_payments}")
    else:
        print("✓ No orphaned Payments")
    
    # Check for orders without customer
    orders_no_customer = Order.objects.filter(customer__isnull=True).count()
    if orders_no_customer > 0:
        issues.append(f"Orders without Customer: {orders_no_customer}")
    else:
        print("✓ All Orders have valid Customers")
    
    # Check for order items without product
    items_no_product = OrderItem.objects.filter(product__isnull=True).count()
    if items_no_product > 0:
        issues.append(f"Order Items without Product: {items_no_product}")
    else:
        print("✓ All Order Items have valid Products")
    
    if issues:
        print("\n✗ INTEGRITY ISSUES FOUND:")
        for issue in issues:
            print(f"  - {issue}")
        return False
    
    return True

def check_model_relationships():
    """Verify model relationships are properly set up"""
    print_section("MODEL RELATIONSHIPS CHECK")
    
    all_good = True
    
    # Check customers with orders
    customers_with_orders = Customer.objects.filter(orders__isnull=False).distinct().count()
    print(f"Customers with Orders: {customers_with_orders}")
    
    # Check average orders per customer
    if Customer.objects.count() > 0:
        avg_orders = Order.objects.count() / Customer.objects.count()
        print(f"Average Orders per Customer: {avg_orders:.2f}")
    
    # Check orders with items and payments
    orders_with_items = Order.objects.filter(items__isnull=False).distinct().count()
    orders_with_payments = Order.objects.filter(payments__isnull=False).distinct().count()
    
    print(f"Orders with Items: {orders_with_items}")
    print(f"Orders with Payments: {orders_with_payments}")
    
    if orders_with_items == 0 and Order.objects.count() > 0:
        print("✗ WARNING: Orders exist but have no items!")
        all_good = False
    else:
        print("✓ Order-OrderItem relationships are valid")
    
    if orders_with_payments == 0 and Order.objects.count() > 0:
        print("✗ WARNING: Orders exist but have no payments!")
        all_good = False
    else:
        print("✓ Order-Payment relationships are valid")
    
    # Sample relationship check
    sample_order = Order.objects.select_related('customer').prefetch_related('items', 'payments').first()
    if sample_order:
        print(f"\nSample Order: {sample_order.order_number}")
        print(f"  - Customer: {sample_order.customer.first_name} {sample_order.customer.last_name}")
        print(f"  - Items: {sample_order.items.count()}")
        print(f"  - Payments: {sample_order.payments.count()}")
        print(f"  - Total: ₱{sample_order.total:,.2f}")
    
    return all_good

def check_admin_registration():
    """Check if all models are registered in Django admin"""
    print_section("DJANGO ADMIN REGISTRATION")
    
    from django.contrib import admin
    
    registered_models = []
    for model, admin_class in admin.site._registry.items():
        registered_models.append(model.__name__)
    
    expected_models = ['Customer', 'Product', 'Order', 'Payment', 'StockAlert']
    
    for model_name in expected_models:
        if model_name in registered_models:
            print(f"✓ {model_name} is registered in Django admin")
        else:
            print(f"✗ {model_name} is NOT registered in Django admin")
    
    return all(model in registered_models for model in expected_models)

def main():
    """Run all verification checks"""
    print("\n")
    print("╔" + "="*58 + "╗")
    print("║" + " "*10 + "DJANGO DATABASE SYNC & VERIFICATION" + " "*13 + "║")
    print("╚" + "="*58 + "╝")
    
    results = {
        'Database Connection': check_database_connection(),
        'Admin Users': check_admin_users(),
        'Data Records': check_data_records(),
        'Data Integrity': check_data_integrity(),
        'Model Relationships': check_model_relationships(),
        'Admin Registration': check_admin_registration(),
    }
    
    # Summary
    print_section("SUMMARY")
    
    all_passed = True
    for check, result in results.items():
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"  {check:.<40} {status}")
        if not result:
            all_passed = False
    
    print("\n")
    if all_passed:
        print("╔" + "="*58 + "╗")
        print("║" + " "*15 + "ALL CHECKS PASSED" + " "*25 + "║")
        print("║" + " "*8 + "Database is properly configured and synced" + " "*8 + "║")
        print("╚" + "="*58 + "╝\n")
        return 0
    else:
        print("╔" + "="*58 + "╗")
        print("║" + " "*10 + "SOME CHECKS FAILED - SEE DETAILS ABOVE" + " "*10 + "║")
        print("╚" + "="*58 + "╝\n")
        return 1

if __name__ == '__main__':
    sys.exit(main())
