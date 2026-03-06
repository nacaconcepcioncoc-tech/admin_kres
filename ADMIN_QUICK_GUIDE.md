# KRES Co. Admin - Automatic Deletion System - Quick Guide

## 🎯 What's New

Your admin system now **automatically cleans up old data** on a schedule:

### Orders Page
- **When**: First day of every month (1:00 AM)
- **What**: Completed orders from last month are deleted
- **Why**: Keeps the current orders list clean and fresh
- **Protected**: Pending orders stay forever

### Reports Page (Sales Calendar)
- **When**: January 1st every year (1:00 AM)
- **What**: All sales data from last year is cleared
- **Why**: Fresh start each year
- **Preserved**: Yearly summaries are saved before deletion

---

## ⚠️ Admin Responsibilities

### Before Month-End (For Orders)
```
🟡 OPTIONAL: Export completed orders to Excel if you want to keep them
   - The system will auto-delete on the 1st at midnight
   - You'll get a warning message showing the date: 
     "⚠️ Completed orders will be automatically cleared on [April 1, 2026]"
```

### Before Year-End (For Sales Calendar)  
```
🟡 OPTIONAL: Back up your sales data before December 31st
   - The system will auto-delete on January 1 at midnight
   - You'll get a warning message showing the year:
     "⚠️ All sales records will be automatically cleared on January 1, 2027"
```

---

## ✅ What Happens Automatically (No Action Needed)

### On April 1, 2026 at 1:00 AM
```
1. ✓ System archives all March 2026 completed orders' sales data
2. ✓ System deletes the March completed orders from the database
3. ✓ April's orders page loads fresh with only April data
4. ✓ Sales Calendar still shows March data (preserved in archive)
```

### On January 1, 2027 at 1:00 AM
```
1. ✓ System creates a yearly snapshot of all 2026 sales
2. ✓ System deletes all 2026 monthly data
3. ✓ January 2027 starts completely clean
4. ✓ You can request 2026 historical snapshot if needed
```

---

## 🔍 Checking the System Status

### Via Django Admin Shell
```bash
# Check if any orders have been archived
python manage.py shell

from pages.models import MonthlySalesArchive, YearlySalesSnapshot

# View monthly archives
archives = MonthlySalesArchive.objects.all()
for archive in archives:
    print(f"{archive} - Total: ₱{archive.total_sales}")

# Example Output:
# March 2025 Sales Archive - Total: ₱15,432.50
# February 2025 Sales Archive - Total: ₱12,890.00

# View yearly snapshots
snapshots = YearlySalesSnapshot.objects.all()
for snapshot in snapshots:
    print(f"Year {snapshot.year} - ₱{snapshot.total_yearly_sales}")
```

---

## 🛑 If Deletion Didn't Happen

The system **automatically handles** deletion two ways:

### Method 1: Page Load Check (Most Reliable)
- ✅ When you or anyone visits the Orders page on April 1
- ✅ When you or anyone visits the Reports page on January 1
- ⚠️ What if nobody visits? Deletion waits until someone does

### Method 2: Scheduled Task (Optional Setup)
- Best for guaranteed execution even if nobody visits
- **Requires**: Setting up a cron job or APScheduler
- See `AUTO_DELETE_IMPLEMENTATION.md` for instructions

**To manually trigger deletion:**
```bash
python manage.py auto_delete_old_orders
```

---

## 📊 Data Preservation Guarantee

### Completed Orders Are Deleted, But Sales Data Is NOT
```
DELETED:
  ✗ Order receipts from last month (Orders table)
  ✗ Order details (customer, items, etc.)

PRESERVED:
  ✓ Sales totals by day (March 15 = $500)
  ✓ Order numbers and amounts
  ✓ Customer names and sales figures
  ✓ All data visible in Sales Calendar

LOCATION OF PRESERVED DATA:
  → Reports Page → Sales Calendar → Select March → See all sales
```

### Yearly Data Is Deleted, But Snapshots Are Kept
```
DELETED:
  ✗ All 12 months of 2026 sales day-by-day data
  ✗ Individual order records from 2026

PRESERVED:
  ✓ Complete 2026 yearly snapshot (total sales, by month)
  ✓ All summary reports
  ✓ Historical records in database

REQUEST HISTORICAL DATA:
  → Contact your system admin
  → Query: YearlySalesSnapshot.objects.get(year=2026)
```

---

## 🎯 User Messages They'll See

### Orders Page Warning
Located in "Completed Orders" modal:
```
⚠️ Completed orders will be automatically cleared on April 1, 2026
Export to Excel to save a copy.
```

### Reports Page Warning
Located at top of Sales Calendar:
```
⚠️ All sales records will be automatically cleared on January 1, 2027
Please export or back up your data before year-end.
```

Both dates **update automatically** each month/year - no manual maintenance!

---

## 🔐 Safety Features

### ✅ Pending Orders Are NEVER Deleted
- Deletion only affects `status='completed'`
- Processing, Pending, Cancelled orders stay forever
- Only fully completed orders get cleaned up

### ✅ Sales Data is Always Backed Up First
- Before deleting completed orders, sales data is archived
- Before deleting yearly data, full snapshot is created
- You can always query these archives for historical reports

### ✅ Date-Based Checks (No Human Error)
- System uses your server's date/time
- Automatic as long as someone visits pages on those dates
- Manual override available via management command

### ✅ JSON Archives are Queryable
```bash
python manage.py shell

from pages.models import MonthlySalesArchive
import json

# Get March 2025 sales
march_data = MonthlySalesArchive.objects.get(month_name='March', year=2025)

# Access sales by day
sales_dict = march_data.sales_by_day
# {"1": 500.00, "5": 1250.00, "15": 2300.50, ...}

# Access orders list
orders_dict = march_data.orders_by_day
# {"5": [{order_number: "ORD-001", customer_name: "John Doe", total: 1250.00}]}
```

---

## ⚡ Quick Troubleshooting

**Q: Orders haven't been deleted, but it's April 2?**
- A: Has anyone visited the Orders page yet? System checks on page load.
- A: Run: `python manage.py auto_delete_old_orders`

**Q: Sales data disappeared from Reports!**
- A: Check if the date is January 2 or later (yearly reset runs at Jan 1)
- A: Query archives: `MonthlySalesArchive.objects.filter(year=2025)`

**Q: Can I manually delay the deletion?**
- A: Not automatically, but you can comment out the check_and_delete calls in views.py
- A: Better: Set up APScheduler with a later time (see detailed guide)

**Q: Where's the Excel export feature?**
- A: Button in the Completed Orders modal - users can click "Export to Excel"
- A: Remind users to export BEFORE the 1st of the month

---

## 📅 Important Dates in 2026

| Date | Action | Ordered? |
|------|--------|----------|
| April 1 | Delete March completed orders | From 1st of each month |
| May 1 | Delete April completed orders | From 1st of each month |
| June 1 | Delete May completed orders | From 1st of each month |
| ... | ... | ... |
| January 1, 2027 | Delete all 2026 sales data | Annual reset |

---

## 🔧 Admin Settings (If Needed)

### To Change Deletion Time (Advanced)
Edit `pages/auto_delete_utils.py` - currently set to check on day 1 at any time:
```python
# Current: Deletes whenever someone visits page on the 1st
# To require scheduled task: Comment out checks in views.py
```

### To Disable Auto-Deletion Temporarily
1. Open `pages/views.py`
2. Find `check_and_delete_completed_orders()` and `check_and_delete_yearly_sales_data()`  
3. Comment out those lines temporarily
4. Remember to enable again!

### To Set Up Scheduled Execution
See `AUTO_DELETE_IMPLEMENTATION.md` section "How to Set Up Automatic Execution"

---

## 📞 Getting Help

- **System errors?** Check Django logs for stack traces
- **Need to restore deleted data?** Query `MonthlySalesArchive` or `YearlySalesSnapshot`
- **Want to change deletion dates?** Edit `auto_delete_utils.py` and redeploy
- **Suspicious deletions?** Check Django admin for activity logs

---

**Setup Date**: March 6, 2026  
**Status**: ✅ Active and Monitoring  
**Next Deletion**: April 1, 2026 (completed orders)  
**Year-End Reset**: January 1, 2027 (sales calendar)
