# Django–Supabase Synchronization Audit

Audit date: July 24, 2026 (Asia/Manila)

## Outcome

The six requested pages all use Django views and Django ORM models as their
shared data layer. In production, those ORM models connect to Supabase
PostgreSQL through `DATABASE_URL`. No hardcoded page data was found in the
active dashboard, orders, customers, payments, reports, or employee-standing
data flows.

The main failure is database migration drift: the live Supabase database has
only the Pages migrations through `0008_alter_orderitem_product`, while the
latest application models require migrations through
`0012_employee_performance_records`.

## Issues found and resolutions

### 1. Live schema is behind the application

Root cause:

- `pages_order` is missing `delivery_address`, `sender_address`,
  `sender_is_receiver`, `balance_payment`, `delivery_fee_charge`, and
  `additional_payment`.
- `pages_employee` is missing `total_stars`, `total_demerits`,
  `overall_rating`, `created_at`, and `updated_at`.
- `pages_performancerecord` does not exist.
- The application already contains the additive Django migrations that create
  these fields and the employee performance table.

Impact:

- Orders, Dashboard, Customers, and Payments can raise database-column errors.
- Employee Standing cannot load or save performance records.
- Reports and cross-page summaries can become unavailable because all six
  pages share the same ORM models.

Resolution:

- Preserved migrations `0007_order_sender_identity` through
  `0012_employee_performance_records`.
- Changed the deployment build so migrations must succeed. A failed migration
  now stops the release instead of silently deploying incompatible code.
- No existing records are deleted by these migrations.

Important: the production schema mutation was not applied during this audit
because the production-write safety review required explicit approval. Deploy
this corrected project only after approving the included migrations.

### 2. Wrong Supabase Storage bucket name

Root cause:

- The code and deployment file defaulted to `employee-profiles`.
- The actual public Supabase bucket is `employee-profile-photos`.

Impact:

- Employee photos stored as object paths produced invalid public URLs.

Resolution:

- Updated the default bucket in `storefront/settings.py`, `pages/models.py`,
  and `render.yaml` to `employee-profile-photos`.
- Full existing HTTP(S) photo URLs remain supported.

### 3. Employee profile AJAX contract mismatch

Root cause:

- The profile page posted JSON using a URL name that did not exist.
- The Django endpoint only read form-encoded POST data.
- The template used obsolete record properties (`date` and `type`) instead of
  the current model fields (`record_date` and `record_type`).

Impact:

- Adding a performance record from the employee profile failed.
- Existing performance rows could render blank values.

Resolution:

- Corrected the URL name.
- Made the endpoint accept both JSON and normal form submissions.
- Corrected the template field mappings and the 1–100 point range.
- Kept CSRF protection and login protection enabled.

### 4. Production database could silently fall back to SQLite

Root cause:

- A missing `DATABASE_URL` used the bundled local SQLite file even when
  `DEBUG=False`.

Impact:

- A deployment could appear online while reading a different database,
  producing inconsistent or empty page data.

Resolution:

- Production now raises a clear startup error when `DATABASE_URL` is absent.
- SQLite remains available for local development only.
- PostgreSQL connections now use health checks, reuse connections, and require
  SSL in production.

### 5. Migration failures were hidden

Root cause:

- `build.sh` used `python manage.py migrate || true`.

Impact:

- Render continued deploying when the database did not match the code.

Resolution:

- Replaced it with `python manage.py migrate --noinput`.

### 6. Hardcoded administrator credentials

Root cause:

- `build.sh` contained a fixed superuser email, username, and password and
  recreated that account during every build.

Impact:

- The credential was exposed in project source and created a serious account
  security risk.

Resolution:

- Removed automatic hardcoded superuser creation.
- Existing database users are preserved.
- Rotate the exposed administrator password immediately.

### 7. RLS and API-path findings

- All application `public.pages_*` tables currently have RLS enabled with no
  REST policies.
- This does not block the current architecture because the browser does not
  query Supabase REST directly; authenticated Django views query PostgreSQL
  through the server-side `DATABASE_URL`.
- Adding permissive anonymous policies would expose admin-system data and was
  intentionally not done.
- Supabase's remediation reference for tables with RLS enabled but no policy is:
  https://supabase.com/docs/guides/database/database-linter?lint=0008_rls_enabled_no_policy
- Supabase Auth currently has no users; authentication is provided by Django's
  `auth_user` table and Django sessions.
- There are no Supabase Edge Functions.
- The employee photo bucket is public and contains no object policies. Public
  reading is consistent with the profile-photo design; uploads should continue
  through a trusted admin/server path.

## Page synchronization map

| Page | Live source | Synchronization behavior |
| --- | --- | --- |
| Dashboard | `Order`, `Customer`, `Payment` | Recalculates monthly revenue, counts, and tomorrow deliveries from current database rows on each request. |
| Orders | `Order`, `OrderItem`, `Product`, `Customer`, `Payment` | Creates and updates related rows inside database transactions; other pages read the same rows. |
| Customers | `Customer` plus related `Order` and `Payment` | Profiles and status derive from current related orders. |
| Payments | `Payment` plus related `Order` and `Customer` | Payment changes update the shared order/payment records used by Dashboard and Reports. |
| Reports | completed `Order` rows plus archive tables | Rebuilds current-month totals from live completed orders and preserves archived periods. |
| Employee Standing | `pages_employee` and `pages_performancerecord` | Loads employees dynamically and recalculates saved performance summaries. |

## Validation completed

- All Python source files compile successfully.
- All standalone JavaScript files pass `node --check`.
- Active source contains no remaining old employee bucket name.
- Active source contains no obsolete employee AJAX URL name.
- Active build source contains no hardcoded administrator creation command.
- Live schema, primary keys, foreign keys, migration history, RLS state,
  Storage buckets, row estimates, API URL, publishable-key availability, and
  Edge Functions were inspected.

Full Django runtime tests require installing the dependencies in
`requirements.txt` and applying the pending migrations to a database.
