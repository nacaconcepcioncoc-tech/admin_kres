# Database Maintenance & Advanced Troubleshooting

## Database Health Monitoring

### Automated Verification Script

A comprehensive verification script has been created at the project root:

**File:** `verify_db_sync.py`

**Features:**
- Checks database connection status
- Verifies Django admin users
- Counts all records
- Validates data integrity
- Checks model relationships
- Verifies admin registration

**Usage:**
```bash
python verify_db_sync.py
```

**Expected Output:** You should see ✓ PASS for all checks with a summary showing:
- Database Connection: ACTIVE
- Admin User: kres_admin (Active, Superuser)
- All 119 records properly synced
- Zero orphaned records
- All relationships intact

---

## Regular Maintenance Tasks

### Daily Tasks
- Monitor stock alerts for critical items
- Check for failed payments
- Review pending orders

### Weekly Tasks
```bash
# Backup database
copy db.sqlite3 db.sqlite3.backup.$(date)

# Run system check
python manage.py check

# Verify data integrity
python verify_db_sync.py
```

### Monthly Tasks
```bash
# Remove old sessions
python manage.py clearsessions

# Check for orphaned records (advanced)
python manage.py shell -c "
from pages.models import Order, OrderItem, Payment
# Check for any issues
orphaned = OrderItem.objects.filter(order__isnull=True).count()
print(f'Orphaned items: {orphaned}')
"

# Export data for archival
python manage.py dumpdata pages > backup_$(date).json
```

---

## Database Backup & Recovery

### Creating Backups

**Automatic Simple Backup:**
```bash
powershell -Command "
copy db.sqlite3 ('db.sqlite3.backup.' + (Get-Date -Format 'yyyyMMdd_HHmmss'))
"
```

**Django Data Export:**
```bash
# Export all data as JSON
python manage.py dumpdata pages > backup_full.json

# Export specific models
python manage.py dumpdata pages.Customer > customers.json
python manage.py dumpdata pages.Product > products.json
python manage.py dumpdata pages.Order > orders.json
python manage.py dumpdata pages.Payment > payments.json
```

### Restoring from Backup

**Restore Database File:**
```bash
copy db.sqlite3.backup.20260306_120000 db.sqlite3
```

**Restore from JSON Dump:**
```bash
python manage.py loaddata backup_full.json
```

---

## Advanced Troubleshooting

### Issue: "No Such Table" Error

**Symptoms:**
```
django.db.utils.OperationalError: no such table: pages_customer
```

**Solution:**
```bash
# Run migrations
python manage.py migrate

# Or if completely broken:
# 1. Delete db.sqlite3
# 2. python manage.py migrate
# 3. python manage.py createsuperuser
```

### Issue: Corrupted Database File

**Check Integrity:**
```bash
# Using Python sqlite3
python manage.py shell -c "
import sqlite3
conn = sqlite3.connect('db.sqlite3')
cursor = conn.cursor()
cursor.execute('PRAGMA integrity_check')
result = cursor.fetchone()
print('Database integrity:', result)
conn.close()
"
```

**Repair:**
```bash
# Backup original
copy db.sqlite3 db.sqlite3.corrupted

# Reload from last good backup or JSON
python manage.py loaddata backup_full.json
```

### Issue: Admin Login Not Working

**Debug:**
```bash
# Check user exists and is active
python manage.py shell -c "
from django.contrib.auth.models import User
user = User.objects.get(username='kres_admin')
print(f'User exists: {user.username}')
print(f'Is active: {user.is_active}')
print(f'Is staff: {user.is_staff}')
print(f'Is superuser: {user.is_superuser}')
"

# Reset if needed
python manage.py changepassword kres_admin
```

### Issue: Missing or Orphaned Records

**Find Orphaned Items:**
```bash
python manage.py shell -c "
from pages.models import OrderItem, Payment

# Orphaned items (no order)
orphaned_items = OrderItem.objects.filter(order__isnull=True)
print(f'Orphaned order items: {orphaned_items.count()}')
for item in orphaned_items:
    print(f'  - {item} (ID: {item.id})')

# Orphaned payments (no order)
orphaned_payments = Payment.objects.filter(order__isnull=True)
print(f'Orphaned payments: {orphaned_payments.count()}')
for payment in orphaned_payments:
    print(f'  - {payment} (ID: {payment.id})')
"
```

**Clean Up Orphaned Records:**
```bash
python manage.py shell -c "
from pages.models import OrderItem, Payment

# This will DELETE orphaned records - use carefully!
deleted_items, _ = OrderItem.objects.filter(order__isnull=True).delete()
deleted_payments, _ = Payment.objects.filter(order__isnull=True).delete()

print(f'Deleted orphaned items: {deleted_items}')
print(f'Deleted orphaned payments: {deleted_payments}')
"
```

---

## Performance Optimization

### Database Indexes

Current indexes are automatically created by Django from the models. To verify:

```bash
python manage.py shell -c "
from django.db import connection
with connection.cursor() as cursor:
    cursor.execute(\"PRAGMA index_list(pages_order)\")
    indexes = cursor.fetchall()
    for idx in indexes:
        print(f'Index: {idx}')
"
```

### Query Optimization

If admin interface is slow, optimize queries:

**In pages/admin.py, add:**
```python
@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        # Use select_related for foreign keys
        return qs.select_related('customer').prefetch_related('items', 'payments')
```

### Database Cleanup

```bash
# Remove expired sessions (if using database sessions)
python manage.py clearsessions

# Vacuum database (optimize file size)
python manage.py shell -c "
from django.db import connection
with connection.cursor() as cursor:
    cursor.execute('VACUUM')
    print('Database vacuumed and optimized')
"
```

---

## Advanced Data Operations

### Bulk Update Products

```bash
python manage.py shell -c "
from pages.models import Product

# Example: Update all products in a category
products = Product.objects.filter(category='FLOWERS')
products.update(is_active=True)
print(f'Updated {products.count()} products')
"
```

### Generate Stock Alerts

```bash
python manage.py shell -c "
from pages.models import StockAlert, Product

# Manually trigger alert check
StockAlert.check_and_create_alerts()
print('Stock alerts generated')
"
```

### Export Orders Report

```bash
python manage.py shell -c "
from pages.models import Order
import csv
from datetime import datetime

orders = Order.objects.select_related('customer').prefetch_related('items', 'payments')
filename = f'orders_export_{datetime.now().strftime(\"%Y%m%d\")}.csv'

with open(filename, 'w', newline='') as f:
    writer = csv.writer(f)
    writer.writerow(['Order #', 'Customer', 'Items', 'Total', 'Status', 'Date'])
    for order in orders:
        writer.writerow([
            order.order_number,
            f'{order.customer.first_name} {order.customer.last_name}',
            order.items.count(),
            f'{order.total:.2f}',
            order.status,
            order.created_at.strftime('%Y-%m-%d')
        ])

print(f'Report exported to: {filename}')
"
```

---

## Production Deployment Checklist

Before deploying to production:

- [ ] Set `DEBUG = False` in settings.py
- [ ] Update `ALLOWED_HOSTS` with actual domain names
- [ ] Change `SECRET_KEY` to a random secure value
- [ ] Use a production database (PostgreSQL recommended)
- [ ] Set up HTTPS/SSL certificates
- [ ] Configure static files serving (CloudFront, S3, etc.)
- [ ] Set up proper logging
- [ ] Configure backup strategy
- [ ] Test admin interface thoroughly
- [ ] Set up monitoring and alerts

---

## Monitoring & Alerts

### Create a Monitoring Task

```bash
# Run verification daily
# Add to cron job or Windows Task Scheduler:
python verify_db_sync.py > logs/daily_check.log 2>&1
```

### Monitor from Admin Interface

Check regularly:
1. **StockAlert Admin** - Review low stock items
2. **Payment Admin** - Ensure all payments processed
3. **Order Admin** - Monitor order status flow
4. **Customer Admin** - Track new customer registrations

---

## Documentation References

- **Full Report**: See `DATABASE_SYNC_REPORT.md`
- **Quick Access Guide**: See `ADMIN_ACCESS_GUIDE.md`
- **Django Docs**: https://docs.djangoproject.com/
- **SQLite Docs**: https://www.sqlite.org/docs.html
- **Django Admin**: https://docs.djangoproject.com/en/6.0/ref/contrib/admin/

---

## Support Resources

### If Issues Persist

1. Run verification script: `python verify_db_sync.py`
2. Check Django system: `python manage.py check`
3. Review logs and error messages
4. Check file permissions on db.sqlite3
5. Verify Python version compatibility
6. Test database with: `python manage.py dbshell`

### Emergency Recovery

```bash
# Full database export before critical operations
python manage.py dumpdata > complete_backup.json

# If everything fails
# 1. Restore from backup
# 2. Re-run migrations
# 3. Re-load data

python manage.py migrate --run-syncdb
python manage.py loaddata complete_backup.json
```

---

**Last Updated:** March 6, 2026  
**Database Type:** SQLite3  
**Status:** Production Ready ✅
