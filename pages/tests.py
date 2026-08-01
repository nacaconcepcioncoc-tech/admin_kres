from datetime import date, datetime, time, timedelta
from decimal import Decimal
import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .auto_delete_utils import (
    check_and_delete_completed_orders,
    check_and_delete_employee_standing_yearly_data,
)
from .models import (
    Customer,
    Employee,
    EmployeeStandingPin,
    EmployeeMonthlyPerformance,
    MonthlyCleanupRun,
    MonthlyPerformanceSummary,
    MonthlySalesArchive,
    Order,
    OrderItem,
    Payment,
    PerformanceRecord,
    Product,
)


class EmployeeStandingPinTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            'admin', password='secret', is_staff=True
        )
        self.client.force_login(self.user)
        self.pin = EmployeeStandingPin(is_active=True, updated_by=self.user)
        self.pin.set_pin('2468')
        self.pin.save()

    def test_direct_page_access_requires_pin(self):
        response = self.client.get(reverse('pages:employee_standing'))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('pages:employee_standing_pin'), response.url)

    def test_ajax_access_requires_pin_without_html_redirect(self):
        response = self.client.get(
            reverse('pages:employee_monthly_evaluation_ajax'),
            {'employee_id': 1, 'month': 1, 'year': 2026},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn('verify_url', response.json())

    def test_correct_pin_grants_access_for_current_login_session(self):
        response = self.client.post(
            reverse('pages:employee_standing_pin'),
            {'pin': '2468', 'next': reverse('pages:employee_standing')},
        )
        self.assertRedirects(response, reverse('pages:employee_standing'), fetch_redirect_response=False)
        response = self.client.get(reverse('pages:employee_standing'))
        self.assertEqual(response.status_code, 200)

    def test_incorrect_pin_does_not_grant_access(self):
        response = self.client.post(reverse('pages:employee_standing_pin'), {'pin': '0000'})
        self.assertEqual(response.status_code, 403)
        self.assertNotIn('employee_standing_pin_verified', self.client.session)

    def test_pin_rotation_invalidates_existing_verification(self):
        self.client.post(reverse('pages:employee_standing_pin'), {'pin': '2468'})
        self.pin.set_pin('1357')
        self.pin.save()
        response = self.client.get(reverse('pages:employee_standing'))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('pages:employee_standing_pin'), response.url)

    def test_external_next_url_is_rejected(self):
        response = self.client.post(
            reverse('pages:employee_standing_pin') + '?next=https://example.com/',
            {'pin': '2468'},
        )
        self.assertRedirects(response, reverse('pages:employee_standing'), fetch_redirect_response=False)

    def test_unstaffed_user_is_denied(self):
        regular_user = get_user_model().objects.create_user('regular', password='secret')
        self.client.force_login(regular_user)
        response = self.client.get(reverse('pages:employee_standing'))
        self.assertEqual(response.status_code, 403)

    def test_lock_clears_verification_and_requires_pin_again(self):
        self.client.post(reverse('pages:employee_standing_pin'), {'pin': '2468'})
        self.assertTrue(self.client.session.get('employee_standing_pin_verified'))

        response = self.client.post(reverse('pages:employee_standing_lock'))
        self.assertRedirects(response, reverse('pages:dashboard'), fetch_redirect_response=False)

        session = self.client.session
        for key in (
            'employee_standing_pin_verified',
            'employee_standing_pin_user_id',
            'employee_standing_pin_id',
            'employee_standing_pin_version',
        ):
            self.assertNotIn(key, session)

        response = self.client.get(reverse('pages:employee_standing'))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('pages:employee_standing_pin'), response.url)

    def test_lock_endpoint_rejects_get(self):
        response = self.client.get(reverse('pages:employee_standing_lock'))
        self.assertEqual(response.status_code, 405)

    def test_employee_standing_disables_cache_and_revalidates_bfcache(self):
        self.client.post(reverse('pages:employee_standing_pin'), {'pin': '2468'})
        response = self.client.get(reverse('pages:employee_standing'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('no-store', response.headers.get('Cache-Control', ''))
        self.assertContains(response, 'event.persisted')

    def test_manual_demerits_are_saved_and_duplicate_month_is_updated(self):
        employee = Employee.objects.create(full_name='Monthly Employee', position='Florist')
        self.client.post(reverse('pages:employee_standing_pin'), {'pin': '2468'})
        endpoint = reverse('pages:employee_monthly_evaluation_ajax')
        payload = {
            'employee_id': employee.pk,
            'month': 8,
            'year': 2026,
            'stars': 2,
            'demerits': 7,
            'admin_remarks': 'Seven documented monthly demerits.',
        }

        created = self.client.post(endpoint, payload)
        payload.update(demerits=9, admin_remarks='Updated after final review.')
        updated = self.client.post(endpoint, payload)

        self.assertEqual(created.status_code, 200, created.content)
        self.assertTrue(created.json()['created'])
        self.assertEqual(updated.status_code, 200, updated.content)
        self.assertFalse(updated.json()['created'])
        self.assertEqual(
            EmployeeMonthlyPerformance.objects.filter(
                employee=employee, month=8, year=2026
            ).count(),
            1,
        )
        evaluation = EmployeeMonthlyPerformance.objects.get(employee=employee, month=8, year=2026)
        self.assertEqual(evaluation.stars, 2)
        self.assertEqual(evaluation.demerits, 9)
        employee.refresh_from_db()
        self.assertEqual(employee.total_stars, 2)
        self.assertEqual(employee.total_demerits, 9)

    def test_monthly_evaluation_requires_remarks_and_valid_ranges(self):
        employee = Employee.objects.create(full_name='Validated Employee', position='Florist')
        self.client.post(reverse('pages:employee_standing_pin'), {'pin': '2468'})
        endpoint = reverse('pages:employee_monthly_evaluation_ajax')
        base = {
            'employee_id': employee.pk,
            'month': 8,
            'year': 2026,
            'stars': 2,
            'demerits': 3,
            'admin_remarks': '',
        }

        no_remarks = self.client.post(endpoint, base)
        invalid_stars = self.client.post(endpoint, {**base, 'stars': 3, 'admin_remarks': 'Reason'})
        negative_demerits = self.client.post(endpoint, {**base, 'demerits': -1, 'admin_remarks': 'Reason'})

        self.assertEqual(no_remarks.status_code, 400)
        self.assertEqual(invalid_stars.status_code, 400)
        self.assertEqual(negative_demerits.status_code, 400)
        self.assertFalse(EmployeeMonthlyPerformance.objects.exists())


class LoginAjaxTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='login-admin',
            email='admin@example.com',
            password='correct-password',
        )
        self.endpoint = reverse('pages:login')

    def test_incorrect_credentials_return_json_without_redirect(self):
        response = self.client.post(
            self.endpoint,
            {'email': 'login-admin', 'password': 'wrong-password'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
            HTTP_ACCEPT='application/json',
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {
            'success': False,
            'message': 'Incorrect username or password',
        })
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_correct_credentials_create_session_and_return_safe_redirect(self):
        response = self.client.post(
            f'{self.endpoint}?next={reverse("pages:orders")}',
            {'email': 'admin@example.com', 'password': 'correct-password', 'remember': 'on'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
            HTTP_ACCEPT='application/json',
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()['success'])
        self.assertEqual(response.json()['redirect_url'], reverse('pages:orders'))
        self.assertEqual(int(self.client.session['_auth_user_id']), self.user.pk)
        self.assertTrue(self.client.session['remember_me_auth'])
        self.assertFalse(self.client.session.get_expire_at_browser_close())
        self.assertGreaterEqual(
            self.client.session.get_expiry_age(),
            settings.REMEMBER_ME_SESSION_AGE - 5,
        )

    def test_without_remember_me_session_expires_when_browser_closes(self):
        response = self.client.post(
            self.endpoint,
            {'email': 'login-admin', 'password': 'correct-password'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
            HTTP_ACCEPT='application/json',
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertNotIn('remember_me_auth', self.client.session)
        self.assertTrue(self.client.session.get_expire_at_browser_close())

    def test_remembered_session_renews_expiry_after_authenticated_activity(self):
        self.client.post(
            self.endpoint,
            {'email': 'login-admin', 'password': 'correct-password', 'remember': 'on'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        session = self.client.session
        session.set_expiry(60)
        session.save()

        response = self.client.get(self.endpoint)

        self.assertRedirects(
            response,
            reverse('pages:dashboard'),
            fetch_redirect_response=False,
        )
        self.assertGreaterEqual(
            self.client.session.get_expiry_age(),
            settings.REMEMBER_ME_SESSION_AGE - 5,
        )

    def test_explicit_logout_clears_remembered_session(self):
        self.client.post(
            self.endpoint,
            {'email': 'login-admin', 'password': 'correct-password', 'remember': 'on'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

        response = self.client.post(reverse('pages:logout'))

        self.assertRedirects(response, reverse('pages:login'), fetch_redirect_response=False)
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertNotIn('remember_me_auth', self.client.session)

    def test_normal_form_post_still_uses_html_fallback(self):
        response = self.client.post(
            self.endpoint,
            {'email': 'login-admin', 'password': 'wrong-password'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Incorrect username or password')


class OrderWorkflowTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('tester', password='secret')
        self.client.force_login(self.user)

    def test_create_order_with_supported_payment_and_same_sender_receiver_data(self):
        payload = {
            'customer_email': '09171234567@kres.local',
            'customer_first_name': 'Maria',
            'customer_last_name': 'Santos',
            'customer_phone': '09171234567',
            'customer_address': 'Cagayan de Oro',
            'sender_name': 'Maria Santos',
            'sender_phone': '09171234567',
            'receiver_name': 'Maria Santos',
            'receiver_phone': '09171234567',
            'delivery_address': 'Cagayan de Oro',
            'delivery_date': (timezone.localdate() + timedelta(days=1)).isoformat(),
            'delivery_time': '10:30',
            'notes': '[DROP OFF] Handle with care',
            'items': [{'product_name': 'Rose Bouquet', 'quantity': 1, 'unit_price': '1500.00'}],
            'payment_method': 'gcash_james',
            'payment_status': 'pending',
            'payment_amount': '500.00',
            'tax': 0,
            'discount': 0,
        }
        response = self.client.post(
            reverse('pages:order_create_ajax'),
            data=payload,
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200, response.content)
        order = Order.objects.get()
        self.assertEqual(order.sender_name, 'Maria Santos')
        self.assertEqual(order.customer_phone, '09171234567')
        self.assertEqual(order.payments.get().payment_method, 'gcash_james')
        self.assertEqual(order.payments.get().amount, Decimal('500.00'))

    def test_monthly_reset_deletes_only_completed_and_archives_by_delivery_date(self):
        customer = Customer.objects.create(
            first_name='Test', last_name='Customer', email='test@example.com', phone='09000000000'
        )
        product = Product.objects.create(name='Bouquet', sku='TEST-1', price=Decimal('100.00'))
        previous_month_end = date(2026, 6, 30)
        completed = Order.objects.create(customer=customer, status='completed', delivery_date=previous_month_end)
        pending = Order.objects.create(customer=customer, status='pending', delivery_date=previous_month_end)
        OrderItem.objects.create(order=completed, product=product, quantity=1, unit_price=Decimal('100.00'))
        OrderItem.objects.create(order=pending, product=product, quantity=1, unit_price=Decimal('100.00'))
        completed.calculate_totals()
        pending.calculate_totals()

        fake_now = timezone.make_aware(datetime(2026, 7, 1, 8, 0))
        with patch('pages.auto_delete_utils.timezone.now', return_value=fake_now):
            result = check_and_delete_completed_orders()

        self.assertTrue(result)
        self.assertFalse(Order.objects.filter(pk=completed.pk).exists())
        self.assertTrue(Order.objects.filter(pk=pending.pk).exists())
        archive = MonthlySalesArchive.objects.get(month_name='June', year=2026)
        self.assertEqual(archive.sales_by_day['30'], 100.0)
        self.assertEqual(archive.orders_by_day['30'][0]['order_number'], completed.order_number)

    def test_monthly_reset_is_idempotent_and_preserves_permanent_data(self):
        customer = Customer.objects.create(
            first_name='Permanent', last_name='Customer', email='keep@example.com', phone='09000000001'
        )
        employee = Employee.objects.create(full_name='Permanent Employee', position='Florist')
        old_record = PerformanceRecord.objects.create(
            employee=employee, record_type='star', description='June record', points=2,
            record_date=date(2026, 6, 15),
        )
        current_record = PerformanceRecord.objects.create(
            employee=employee, record_type='star', description='July record', points=1,
            record_date=date(2026, 7, 1),
        )
        pending = Order.objects.create(
            customer=customer, status='pending', delivery_date=date(2026, 6, 30)
        )

        fake_now = timezone.make_aware(datetime(2026, 7, 1, 8, 0))
        with patch('pages.auto_delete_utils.timezone.now', return_value=fake_now):
            first = check_and_delete_completed_orders()
            second = check_and_delete_completed_orders()

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertTrue(Customer.objects.filter(pk=customer.pk).exists())
        self.assertTrue(Employee.objects.filter(pk=employee.pk).exists())
        self.assertTrue(Order.objects.filter(pk=pending.pk).exists())
        self.assertTrue(PerformanceRecord.objects.filter(pk=old_record.pk).exists())
        self.assertTrue(PerformanceRecord.objects.filter(pk=current_record.pk).exists())
        self.assertEqual(MonthlyCleanupRun.objects.count(), 1)

    def test_monthly_reset_only_runs_on_day_one_and_does_not_catch_up_old_months(self):
        customer = Customer.objects.create(
            first_name='Catchup', last_name='Customer', email='catchup@example.com', phone='09000000002'
        )
        april = Order.objects.create(
            customer=customer, status='completed', delivery_date=date(2026, 4, 30), total=Decimal('100.00')
        )
        june = Order.objects.create(
            customer=customer, status='completed', delivery_date=date(2026, 6, 30), total=Decimal('200.00')
        )
        july_pending = Order.objects.create(
            customer=customer, status='pending', delivery_date=date(2026, 7, 5), total=Decimal('300.00')
        )

        fake_now = timezone.make_aware(datetime(2026, 8, 15, 8, 0))
        with patch('pages.auto_delete_utils.timezone.now', return_value=fake_now):
            result = check_and_delete_completed_orders()

        self.assertFalse(result)
        self.assertEqual(Order.objects.filter(pk__in=[april.pk, june.pk]).count(), 2)
        self.assertTrue(Order.objects.filter(pk=july_pending.pk).exists())
        self.assertFalse(MonthlySalesArchive.objects.exists())

    def test_monthly_archive_merges_late_completed_orders(self):
        customer = Customer.objects.create(
            first_name='Archive', last_name='Customer', email='archive@example.com', phone='09000000003'
        )
        MonthlySalesArchive.objects.create(
            month_name='July',
            year=2026,
            sales_by_day={'1': 50.0},
            orders_by_day={'1': [{'order_id': 999, 'order_number': 'OLD', 'total': 50.0}]},
            total_sales=Decimal('50.00'),
        )
        late_order = Order.objects.create(
            customer=customer, status='completed', delivery_date=date(2026, 7, 30), total=Decimal('100.00')
        )

        fake_now = timezone.make_aware(datetime(2026, 8, 1, 8, 0))
        with patch('pages.auto_delete_utils.timezone.now', return_value=fake_now):
            check_and_delete_completed_orders()

        archive = MonthlySalesArchive.objects.get(month_name='July', year=2026)
        self.assertEqual(archive.total_sales, Decimal('150.00'))
        self.assertEqual(archive.orders_by_day['1'][0]['order_number'], 'OLD')
        self.assertEqual(archive.orders_by_day['30'][0]['order_number'], late_order.order_number)

    def test_monthly_reset_cascades_related_rows_and_deletes_only_orphaned_customer(self):
        removed_customer = Customer.objects.create(
            first_name='Removed', last_name='Customer', email='removed@example.com', phone='09000000005'
        )
        kept_customer = Customer.objects.create(
            first_name='Kept', last_name='Customer', email='kept@example.com', phone='09000000006'
        )
        product = Product.objects.create(name='Arrangement', sku='TEST-2', price=Decimal('200.00'))
        completed = Order.objects.create(
            customer=removed_customer, status='completed', delivery_date=date(2026, 7, 15), total=Decimal('200.00')
        )
        pending = Order.objects.create(
            customer=kept_customer, status='pending', delivery_date=date(2026, 7, 15), total=Decimal('200.00')
        )
        item = OrderItem.objects.create(
            order=completed, product=product, quantity=1, unit_price=Decimal('200.00')
        )
        payment = Payment.objects.create(
            order=completed, amount=Decimal('200.00'), payment_status='completed', payment_method='cash'
        )

        fake_now = timezone.make_aware(datetime(2026, 8, 1, 8, 0))
        with patch('pages.auto_delete_utils.timezone.now', return_value=fake_now):
            result = check_and_delete_completed_orders()

        self.assertEqual(result[1:3], (1, 1))
        self.assertFalse(OrderItem.objects.filter(pk=item.pk).exists())
        self.assertFalse(Payment.objects.filter(pk=payment.pk).exists())
        self.assertFalse(Customer.objects.filter(pk=removed_customer.pk).exists())
        self.assertTrue(Customer.objects.filter(pk=kept_customer.pk).exists())
        self.assertTrue(Order.objects.filter(pk=pending.pk).exists())

    def test_employee_standing_records_reset_only_on_january_first(self):
        employee = Employee.objects.create(full_name='Preserved Profile', position='Florist')
        old_evaluation = EmployeeMonthlyPerformance.objects.create(
            employee=employee, month=12, year=2025, stars=2, demerits=1, admin_remarks='Old'
        )
        current_evaluation = EmployeeMonthlyPerformance.objects.create(
            employee=employee, month=1, year=2026, stars=1, demerits=0, admin_remarks='Current'
        )
        old_legacy = PerformanceRecord.objects.create(
            employee=employee, record_type='star', points=1, description='Old', record_date=date(2025, 12, 1)
        )
        old_summary = MonthlyPerformanceSummary.objects.create(
            employee=employee, month=date(2025, 12, 1), final_points=1, stars=1,
            demerits=0, performance_status='Good'
        )

        fake_now = timezone.make_aware(datetime(2026, 1, 1, 8, 0))
        with patch('pages.auto_delete_utils.timezone.now', return_value=fake_now):
            result = check_and_delete_employee_standing_yearly_data()

        self.assertTrue(result[0])
        self.assertFalse(EmployeeMonthlyPerformance.objects.filter(pk=old_evaluation.pk).exists())
        self.assertFalse(PerformanceRecord.objects.filter(pk=old_legacy.pk).exists())
        self.assertFalse(MonthlyPerformanceSummary.objects.filter(pk=old_summary.pk).exists())
        self.assertTrue(EmployeeMonthlyPerformance.objects.filter(pk=current_evaluation.pk).exists())
        self.assertTrue(Employee.objects.filter(pk=employee.pk, full_name='Preserved Profile').exists())

    def test_employee_standing_yearly_reset_returns_false_outside_january_first(self):
        fake_now = timezone.make_aware(datetime(2026, 8, 1, 8, 0))
        with patch('pages.auto_delete_utils.timezone.now', return_value=fake_now):
            result = check_and_delete_employee_standing_yearly_data()

        self.assertIs(result, False)

    def test_done_updates_order_payment_and_balance_and_is_idempotent(self):
        customer = Customer.objects.create(
            first_name='Done', last_name='Customer', email='done@example.com', phone='09000000004'
        )
        order = Order.objects.create(
            customer=customer,
            status='pending',
            delivery_date=timezone.localdate(),
            total=Decimal('750.00'),
            balance_payment=Decimal('500.00'),
        )
        payment = Payment.objects.create(
            order=order, amount=Decimal('250.00'), payment_status='partial', payment_method='cash'
        )
        endpoint = reverse('pages:order_update_status_ajax')
        payload = json.dumps({'order_id': order.order_id, 'status': 'completed'})

        first = self.client.post(endpoint, data=payload, content_type='application/json')
        second = self.client.post(endpoint, data=payload, content_type='application/json')

        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(second.status_code, 200, second.content)
        self.assertTrue(second.json()['already_updated'])
        order.refresh_from_db()
        payment.refresh_from_db()
        self.assertEqual(order.status, 'completed')
        self.assertEqual(order.balance_payment, Decimal('0.00'))
        self.assertEqual(payment.payment_status, 'completed')
        self.assertEqual(payment.amount, Decimal('750.00'))

    def test_orders_template_uses_named_routes_and_wires_view_details(self):
        response = self.client.get(reverse('pages:orders'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse('pages:order_update_status_ajax'))
        self.assertContains(response, reverse('pages:payment_get_by_order_ajax'))
        self.assertContains(response, "document.querySelectorAll('.btn-view-details')")
