KRES Create New Order Fixes

Replace the matching project files with these versions:
- templates/orders.html
- pages/views.py
- pages/auto_delete_utils.py
- storefront/settings.py
- templates/payments.html
- pages/tests.py

Key fixes:
- Removed all Reference Photo UI, JavaScript, upload calls, polling, localStorage, and storage dependencies.
- Kept Special Note.
- Added exact payment methods: Cash, GCash James, GCash Banban, GCash Kysan, RCBC.
- Added Receiver is the same as Sender behavior.
- Added stronger backend validation and atomic order creation.
- Completed orders from the previous delivery month are archived then deleted on the first day.
- Pending/processing/cancelled orders are never deleted by the monthly reset.
- Archived completed orders remain available to the Reports monthly sales calendar.
- Deliveries Tomorrow now uses the Django/Manila server-side result instead of browser timezone calculations.
- Updated payment display labels.
- Added tests for order creation and monthly completed-order archival.

After replacing files, run in your virtual environment:
python manage.py check
python manage.py makemigrations --check
python manage.py test pages

No new migration is required by these changes because the database fields were not changed.
