# Automatic Order and Sales Data Deletion System

## Overview

The KRES Co. admin system now includes an automatic deletion system with the following features:

### Orders Page - Monthly Cleanup
- **Trigger**: First day of every month at midnight
- **What gets deleted**: All completed orders from the previous month
- **What's preserved**: Sales data remains in the Sales Calendar (Reports page)
- **Display**: Dynamic warning note shows "Completed orders will be automatically cleared on [next month date]. Export to Excel to save a copy."

### Reports Page - Yearly Reset
- **Trigger**: January 1st at midnight
- **What gets deleted**: All monthly sales archive data from the previous year
- **What's preserved**: Yearly snapshot of all historical sales data
- **Display**: Dynamic warning note shows "All sales records will be automatically cleared on January 1, [next year]. Please export or back up your data before year-end."

## How It Works

### Architecture

1. **New Models**
   - `MonthlySalesArchive`: Stores decomposed monthly sales data (sales by day, orders list, totals)
   - `YearlySalesSnapshot`: Stores complete yearly snapshots of all 12 months before annual reset

2. **Auto-Deletion Mechanism**
   - **On Page Load Check**: Every time a user accesses the Orders or Reports page, the system checks if deletion should occur
   - **Scheduled Task Option**: Can also run via management command with cron/celery for guaranteed execution

3. **Data Preservation**
   - When completed orders are deleted on the 1st of the month, their sales data is first archived in `MonthlySalesArchive`
   - The Reports page queries both live orders AND archived data to display complete sales history
   - Yearly snapshots are created on January 1st before deleting monthly archives

## Implementation Details

### Files Modified/Created

1. **Models** (`pages/models.py`)
   - `MonthlySalesArchive`: Stores monthly sales data by day
   - `YearlySalesSnapshot`: Stores yearly historical snapshots

2. **Utilities** (`pages/auto_delete_utils.py`)
   - `check_and_delete_completed_orders()`: Checks date and deletes previous month's completed orders
   - `check_and_delete_yearly_sales_data()`: Checks date and deletes previous year's data
   - `archive_monthly_sales_data()`: Archives monthly data before deletion
   - `create_yearly_snapshot()`: Creates yearly snapshots before deletion
   - `get_next_month_deletion_date()`: Returns the date for the next deletion
   - `get_next_year_deletion_date()`: Returns January 1st of next year

3. **Management Command** (`pages/management/commands/auto_delete_old_orders.py`)
   - Can be run manually or scheduled with cron/celery
   - `python manage.py auto_delete_old_orders`

4. **Views** (`pages/views.py`)
   - `orders()`: Now calls `check_and_delete_completed_orders()` on page load
   - `reports()`: Now calls `check_and_delete_yearly_sales_data()` on page load
   - Both views pass deletion date information to templates

5. **Templates**
   - `orders.html`: Added warning banner with next deletion date (dynamically updated via JavaScript)
   - `reports.html`: Added warning banner with next year's deletion date (dynamically updated via JavaScript)

6. **Migrations**
   - `0007_yearlysalessnapshot_monthlysalesarchive.py`: Creates the archive tables

### Database Schema

```sql
-- MonthlySalesArchive stores monthly sales decomposed by day
CREATE TABLE pages_monthlysalesarchive (
    archive_id INTEGER PRIMARY KEY,
    month_name VARCHAR(20),
    year INTEGER,
    sales_by_day JSON,  -- {"1": 1500.00, "2": 2300.50, ...}
    orders_by_day JSON, -- {"1": [{order_number, customer_name, total, ...}], ...}
    total_sales DECIMAL(10, 2),
    created_at TIMESTAMP,
    UNIQUE(month_name, year)
);

-- YearlySalesSnapshot stores complete yearly data before reset
CREATE TABLE pages_yearlysalessnapshot (
    snapshot_id INTEGER PRIMARY KEY,
    year INTEGER UNIQUE,
    calendar_data JSON,  -- {"January": {...}, "February": {...}, ...}
    all_months_archive JSON,
    total_yearly_sales DECIMAL(10, 2),
    created_at TIMESTAMP
);
```

## How to Set Up Automatic Execution

### Option 1: Page Load Check (Already Enabled)
- ✅ Automatically runs when users access Orders or Reports page
- ✅ No additional setup required
- ⚠️ Only executes when someone visits the page

### Option 2: Scheduled with APScheduler (Recommended for Small Deployments)

Install APScheduler:
```bash
pip install django-apscheduler
```

Add to `settings.py`:
```python
INSTALLED_APPS = [
    # ...
    'django_apscheduler',
]

SCHEDULER_AUTOSTART = True
```

Create a scheduled task (e.g., in `pages/apps.py`):
```python
from django.apps import AppConfig
from django.core.management import call_command

class PagesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'pages'
    
    def ready(self):
        from django_apscheduler.models import DjangoJobExecution
        from django_apscheduler.scheduling import start_scheduler
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
        
        scheduler = BackgroundScheduler()
        scheduler.add_job(
            lambda: call_command('auto_delete_old_orders'),
            trigger=CronTrigger(hour=0, minute=1),  # 12:01 AM daily
            id='auto_delete_old_orders',
            max_instances=1,
            replace_existing=True
        )
        scheduler.start()
```

### Option 3: Linux Cron Job (For Production Servers)

Add to crontab:
```bash
# Run the auto-deletion command at 12:05 AM every day
5 0 * * * cd /path/to/adminp-master && python manage.py auto_delete_old_orders >> /var/log/adminp_auto_delete.log 2>&1
```

### Option 4: Celery Beat (For Complex Deployments)

Add to `celery.py`:
```python
from celery.schedules import crontab

CELERY_BEAT_SCHEDULE = {
    'auto-delete-old-orders': {
        'task': 'pages.tasks.auto_delete_old_orders',
        'schedule': crontab(hour=0, minute=1),  # 12:01 AM daily
    },
}
```

Create `pages/tasks.py`:
```python
from celery import shared_task
from django.core.management import call_command

@shared_task
def auto_delete_old_orders():
    call_command('auto_delete_old_orders')
```

## Testing & Verification

### Test the Deletion Logic (Without Date Checking)

Manually archive and delete test data:
```bash
python manage.py shell

from pages.models import Order, MonthlySalesArchive
from pages.auto_delete_utils import archive_monthly_sales_data
from datetime import date

# Get test completed orders
test_orders = Order.objects.filter(status='completed')

# Archive them
archive_monthly_sales_data(test_orders, 'March', 2025)

# Verify archive was created
MonthlySalesArchive.objects.all()

# Delete the orders
test_orders.delete()

# Check Reports page - sales data should still display from archive
```

### Check Archive Tables
```bash
python manage.py shell

from pages.models import MonthlySalesArchive, YearlySalesSnapshot

# View monthly archives
MonthlySalesArchive.objects.all()

# Example output:
# <MonthlySalesArchive: March 2025 Sales Archive> - $15,432.50
# <MonthlySalesArchive: February 2025 Sales Archive> - $12,890.00

# View yearly snapshots
YearlySalesSnapshot.objects.all()

# Example output:
# <YearlySalesSnapshot: Sales Snapshot - Year 2024> - $185,643.25
```

## How Pending Orders Are Protected

- ✅ Only "completed" orders are deleted
- ✅ Pending, processing, and cancelled orders are never affected
- ✅ The deletion filter specifically targets: `Order.objects.filter(status='completed')`

## How Sales Data is Preserved After Order Deletion

### Data Flow Example:

**Before Deletion (March 1-31, live orders exist)**
```
Orders Table
- Order 001: $500 (completed, March 5)
- Order 002: $750 (completed, March 15)

Reports Page shows:
- March 5: $500
- March 15: $750
- Total: $1,250
```

**After Deletion (April 1st, previous month's deleted orders)**
```
Orders Table
(Orders deleted)

MonthlySalesArchive Table
- March 2025 entry: sales_by_day = {"5": 500, "15": 750}

Reports Page shows (from archive):
- March 5: $500
- March 15: $750
- Total: $1,250
```

## Warning Messages

### Orders Page
- **Location**: Completed Orders modal
- **Message**: "⚠️ Completed orders will be automatically cleared on [first day of next month]. Export to Excel to save a copy."
- **Auto-updated**: Yes, dynamically calculates next deletion date using JavaScript

### Reports Page
- **Location**: Sales Calendar section header
- **Message**: "⚠️ All sales records will be automatically cleared on January 1, [next year]. Please export or back up your data before year-end."
- **Auto-updated**: Yes, displays next year's date

## Troubleshooting

### Orders Not Deleting
1. Check if the current date is truly the 1st of the month
2. Verify orders have `status='completed'`
3. Check Django logs for errors during `check_and_delete_completed_orders()`
4. Manually run: `python manage.py auto_delete_old_orders`

### Sales Data Missing from Reports
1. Verify `MonthlySalesArchive` records exist
2. Check that archived orders had `status='completed'`
3. Run archive check: 
   ```bash
   python manage.py shell
   from pages.models import MonthlySalesArchive
   MonthlySalesArchive.objects.filter(month_name='March', year=2025)
   ```

### Deletion Not Triggering on Schedule
1. If using cron: Verify cron job exists (`crontab -l`)
2. If using APScheduler: Check Django logs for scheduler errors
3. Always test page load check manually by accessing Orders/Reports page

## Notes for Admins

- 📊 **Backup Regularly**: Even though sales data is archived, maintain regular database backups
- 📅 **Export Before Cutoff**: The warning messages remind users, but enforce export procedures
- 🔄 **Monitor Archive Growth**: Check `MonthlySalesArchive` size over time (typically 1-2 rows per month)
- 📈 **Year-End Process**: On December, remind admins to back up yearly data before January 1 reset

## Admin Actions

### Manual Archive Creation
```bash
python manage.py shell

from pages.auto_delete_utils import archive_monthly_sales_data
from pages.models import Order
from datetime import date

# Archive past month
march_orders = Order.objects.filter(
    status='completed',
    created_at__year=2025,
    created_at__month=3
)
archive_monthly_sales_data(march_orders, 'March', 2025)
```

### Manual Deletion (Advanced)
```bash
python manage.py shell

from pages.models import Order, MonthlySalesArchive
from pages.auto_delete_utils import archive_monthly_sales_data

# Get orders to delete
orders = Order.objects.filter(status='completed', created_at__year=2025, created_at__month=3)

# Archive first
archive_monthly_sales_data(orders, 'March', 2025)

# Delete
orders.delete()
```

---

**Implementation Date**: March 6, 2026
**Status**: ✅ Active and Ready
**Auto-execution**: Page Load Check (Always On) + Optional Scheduled Tasks
