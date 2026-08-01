# Employee Standing PIN deployment

No Supabase changes were made during this inspection.

## 1. Back up and review pending migrations

The connected Supabase database currently records `pages` migrations only through `0014`, while this code contains `0015` through `0018`. Migration `0016` includes an order/customer data migration, so take a Supabase backup and test on a branch or staging database first.

```powershell
python manage.py showmigrations pages
python manage.py migrate --plan
python manage.py migrate pages 0018
python manage.py showmigrations pages
```

The pending migrations add the Employee cached-summary fields, the performance-record submission key, monthly summary/evaluation tables, the receiver-name field and data migration, and `pages_employee_standing_pin`.

## 2. Keep the PIN table out of the Supabase Data API

Run this in Supabase SQL Editor **after** the Django migration succeeds. Django accesses Postgres through `DATABASE_URL`; the browser does not need access to this table.

```sql
alter table public.pages_employee_standing_pin enable row level security;

revoke all privileges
on table public.pages_employee_standing_pin
from anon, authenticated;

revoke all privileges
on sequence public.pages_employee_standing_pin_id_seq
from anon, authenticated;
```

Do not create an `anon` or `authenticated` policy for this table. With no Data API grants or policies, clients cannot read the password hash. The Django database role must retain normal table access.

The existing `pages_employee` and `pages_performancerecord` tables currently grant all table privileges to `anon` and `authenticated`, although RLS is enabled with no policies. If no browser-side Supabase client uses them, harden those tables too:

```sql
revoke all privileges
on table public.pages_employee,
         public.pages_performancerecord,
         public.employee_monthly_performance,
         public.pages_monthlyperformancesummary
from anon, authenticated;
```

## 3. Create the first PIN in Django Admin

1. Deploy the updated code and complete the migrations.
2. Sign in as a Django superuser.
3. Open `/admin/pages/employeestandingpin/add/`.
4. Enter and confirm a 4–12 digit PIN, leave **Active** selected, and save.
5. Open `/employee-standing/` in a new login session and verify the PIN prompt.

Only a Django password hash is stored. Changing or disabling the active PIN immediately invalidates existing Employee Standing PIN sessions.

## 4. Verify production

```powershell
python manage.py check --deploy
python manage.py showmigrations pages
```

Confirm all of the following:

- Direct access to `/employee-standing/` redirects to the PIN page.
- Direct access to `/ajax/employee-monthly-evaluation/` returns HTTP 403 until verification.
- A wrong PIN is rejected and five failures lock that login session for five minutes.
- A correct PIN returns to the original same-site URL and remains valid only for that authenticated user/session.
- Changing the PIN in Django Admin forces re-verification.
