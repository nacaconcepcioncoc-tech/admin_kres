from datetime import date, datetime, time, timedelta
from decimal import Decimal
import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .auto_delete_utils import (
    check_and_delete_completed_orders,
    check_and_delete_employee_standing_yearly_data,
    has_valid_fully_paid_cleanup_structure,
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
    YearlySalesSnapshot,
)
from .payment_state import (
    STATE_DOWN_PAYMENT,
    STATE_FULLY_PAID,
    STATE_UNRECONCILED,
    calculate_order_payment_display_state,
    calculate_order_payment_state,
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

    def test_orders_use_fifo_schedule_with_pickup_winning_exact_ties(self):
        customer = Customer.objects.create(
            first_name='FIFO', last_name='Customer',
            email='fifo@example.com', phone='09170000077',
        )

        def scheduled(suffix, delivery_date, delivery_time, notes, status='pending'):
            return Order.objects.create(
                customer=customer,
                order_number=f'FIFO-{suffix}',
                status=status,
                delivery_date=delivery_date,
                delivery_time=delivery_time,
                notes=notes,
                total=Decimal('100.00'),
            )

        pickup_tie = scheduled('PICKUP', date(2026, 9, 2), time(9, 0), '[PICK UP]')
        dropoff_tie = scheduled('DROPOFF', date(2026, 9, 2), time(9, 0), '[DROP OFF]')
        later_time = scheduled('LATER-TIME', date(2026, 9, 2), time(10, 0), '[DROP OFF]')
        missing_time = scheduled('NO-TIME', date(2026, 9, 2), None, '[PICK UP]')
        later_date = scheduled('LATER-DATE', date(2026, 9, 3), time(8, 0), '[DROP OFF]')
        missing_date = scheduled('NO-DATE', None, time(7, 0), '[PICK UP]')

        expected = [
            pickup_tie.pk,
            dropoff_tie.pk,
            later_time.pk,
            missing_time.pk,
            later_date.pk,
            missing_date.pk,
        ]

        response = self.client.get(reverse('pages:orders'))
        self.assertEqual(
            [order.pk for order in response.context['orders']],
            expected,
        )

        filtered = self.client.get(reverse('pages:orders'), {'status': 'pending'})
        refreshed = self.client.get(reverse('pages:orders'), {'status': 'pending'})
        self.assertEqual([order.pk for order in filtered.context['orders']], expected)
        self.assertEqual([order.pk for order in refreshed.context['orders']], expected)

    def test_customer_total_spent_uses_received_payment_ledger(self):
        customer = Customer.objects.create(
            first_name='Ledger', last_name='Customer',
            email='ledger-customer@example.com', phone='09170000001',
        )

        down_order = Order.objects.create(
            customer=customer, status='pending', total=Decimal('2200.00'),
            balance_payment=Decimal('1200.00'),
            delivery_fee_charge=Decimal('150.00'),
        )
        Payment.objects.create(
            order=down_order, amount=Decimal('1000.00'), payment_method='cash',
            payment_status=Payment.STATUS_DOWN_PAYMENT,
            payment_type=Payment.TYPE_DOWN_PAYMENT,
        )
        self.assertEqual(customer.get_total_spent(), Decimal('1000.00'))

        Payment.objects.create(
            order=down_order, amount=Decimal('1200.00'), payment_method='cash',
            payment_status=Payment.STATUS_FULLY_PAID,
            payment_type=Payment.TYPE_BALANCE_PAYMENT,
        )
        down_order.balance_payment = Decimal('0.00')
        down_order.save(update_fields=['balance_payment', 'updated_at'])
        self.assertEqual(customer.get_total_spent(), Decimal('2200.00'))

        full_order = Order.objects.create(
            customer=customer, status='pending', total=Decimal('2000.00'),
            balance_payment=Decimal('0.00'),
            delivery_fee_charge=Decimal('300.00'),
        )
        Payment.objects.create(
            order=full_order, amount=Decimal('2000.00'), payment_method='cash',
            payment_status=Payment.STATUS_FULLY_PAID,
            payment_type=Payment.TYPE_FULL_PAYMENT,
        )
        self.assertEqual(customer.get_total_spent(), Decimal('4200.00'))

        # Neither order lifecycle changes nor cached/unpaid/delivery-fee values
        # represent received money or alter the ledger-derived result.
        down_order.status = 'completed'
        down_order.balance_payment = Decimal('999.00')
        down_order.delivery_fee_charge = Decimal('999.00')
        down_order.save(update_fields=[
            'status', 'balance_payment', 'delivery_fee_charge', 'updated_at',
        ])
        self.assertEqual(customer.get_total_spent(), Decimal('4200.00'))

        # Reopening the profile derives the same value; it never increments a
        # stored running total or duplicates an existing transaction.
        first_response = self.client.get(reverse('pages:customers'))
        second_response = self.client.get(reverse('pages:customers'))
        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 200)
        self.assertContains(first_response, 'data-total="₱ 4200.00"')
        self.assertContains(second_response, 'data-total="₱ 4200.00"')

    def test_payment_state_uses_typed_ledger_not_order_status_or_delivery_fee(self):
        scenarios = []

        def create_order(suffix, status, total='2200.00', balance='0.00', fee='0.00'):
            customer = Customer.objects.create(
                first_name='State',
                last_name=suffix,
                email=f'payment-state-{suffix}@example.com',
                phone=f'0917{len(scenarios):07d}',
            )
            return Order.objects.create(
                customer=customer,
                status=status,
                total=Decimal(total),
                balance_payment=Decimal(balance),
                delivery_fee_charge=Decimal(fee),
            )

        for suffix, order_status in (('pending-down', 'pending'), ('completed-down', 'completed')):
            order = create_order(suffix, order_status, balance='1200.00')
            Payment.objects.create(
                order=order, amount=Decimal('1000.00'), payment_method='cash',
                payment_status=Payment.STATUS_DOWN_PAYMENT,
                payment_type=Payment.TYPE_DOWN_PAYMENT,
            )
            scenarios.append((order, STATE_DOWN_PAYMENT))

        for suffix, order_status in (('pending-full', 'pending'), ('completed-full', 'completed')):
            order = create_order(suffix, order_status)
            Payment.objects.create(
                order=order, amount=Decimal('2200.00'), payment_method='cash',
                payment_status=Payment.STATUS_FULLY_PAID,
                payment_type=Payment.TYPE_FULL_PAYMENT,
            )
            scenarios.append((order, STATE_FULLY_PAID))

        settled = create_order('settled-down', 'pending', fee='300.00')
        Payment.objects.create(
            order=settled, amount=Decimal('1000.00'), payment_method='cash',
            payment_status=Payment.STATUS_DOWN_PAYMENT,
            payment_type=Payment.TYPE_DOWN_PAYMENT,
        )
        Payment.objects.create(
            order=settled, amount=Decimal('1200.00'), payment_method='cash',
            payment_status=Payment.STATUS_FULLY_PAID,
            payment_type=Payment.TYPE_BALANCE_PAYMENT,
        )
        scenarios.append((settled, STATE_FULLY_PAID))

        legacy = create_order('legacy', 'completed', balance='500.00')
        Payment.objects.create(
            order=legacy, amount=Decimal('1700.00'), payment_method='cash',
            payment_status='completed', payment_type=None,
        )
        scenarios.append((legacy, STATE_UNRECONCILED))

        for order, expected in scenarios:
            with self.subTest(order=order.order_number, expected=expected):
                state = calculate_order_payment_state(order, order.payments.order_by('payment_date', 'payment_id'))
                self.assertEqual(state['code'], expected)

        # Changing only the delivery lifecycle status cannot change payment state.
        scenarios[0][0].status = 'completed'
        scenarios[0][0].save(update_fields=['status', 'updated_at'])
        self.assertEqual(
            calculate_order_payment_state(
                scenarios[0][0], scenarios[0][0].payments.all()
            )['code'],
            STATE_DOWN_PAYMENT,
        )

        payments_response = self.client.get(reverse('pages:payments'))
        displayed_states = {
            payment.order_id: payment.display_payment_state
            for payment in payments_response.context['payments']
        }
        for order, expected in scenarios:
            display_expected = (
                STATE_DOWN_PAYMENT
                if expected == STATE_UNRECONCILED
                else expected
            )
            self.assertEqual(displayed_states[order.order_id], display_expected)

        orders_response = self.client.get(reverse('pages:orders'))
        order_states = {
            order.order_id: order.display_payment_state
            for order in orders_response.context['orders']
        }
        for order, expected in scenarios:
            display_expected = (
                STATE_DOWN_PAYMENT
                if expected == STATE_UNRECONCILED
                else expected
            )
            self.assertEqual(order_states[order.order_id], display_expected)

    def test_legacy_payment_status_display_uses_cached_balance_only(self):
        def legacy_order(suffix, balance):
            customer = Customer.objects.create(
                first_name='Legacy', last_name=suffix,
                email=f'legacy-display-{suffix}@example.com',
                phone=f'09170000{len(suffix):03d}',
            )
            order = Order.objects.create(
                customer=customer,
                total=Decimal('1000.00'),
                balance_payment=Decimal(balance),
                status='completed',
            )
            payment = Payment.objects.create(
                order=order,
                amount=Decimal('500.00'),
                payment_method='cash',
                payment_status='completed',
                payment_type=None,
            )
            return order, payment

        open_order, open_payment = legacy_order('open', '500.00')
        settled_order, settled_payment = legacy_order('settled', '0.00')

        self.assertEqual(
            calculate_order_payment_state(
                open_order, open_order.payments.all()
            )['code'],
            STATE_UNRECONCILED,
        )
        self.assertEqual(
            calculate_order_payment_display_state(
                open_order, open_order.payments.all()
            )['code'],
            STATE_DOWN_PAYMENT,
        )
        self.assertEqual(
            calculate_order_payment_display_state(
                settled_order, settled_order.payments.all()
            )['code'],
            STATE_FULLY_PAID,
        )

        orders_response = self.client.get(reverse('pages:orders'))
        payments_response = self.client.get(reverse('pages:payments'))
        order_states = {
            order.pk: order.display_payment_state
            for order in orders_response.context['orders']
        }
        payment_states = {
            payment.order_id: payment.display_payment_state
            for payment in payments_response.context['payments']
        }
        self.assertEqual(order_states[open_order.pk], STATE_DOWN_PAYMENT)
        self.assertEqual(order_states[settled_order.pk], STATE_FULLY_PAID)
        self.assertEqual(payment_states[open_order.pk], STATE_DOWN_PAYMENT)
        self.assertEqual(payment_states[settled_order.pk], STATE_FULLY_PAID)
        self.assertNotContains(orders_response, 'Unreconciled')
        self.assertNotContains(payments_response, 'Unreconciled')

        export_response = self.client.get(reverse('pages:orders_export_data_ajax'))
        exported_states = {
            row['customer']: row['status']
            for row in export_response.json()['orders']
        }
        self.assertEqual(exported_states[open_order.customer_name], 'Down Payment')
        self.assertEqual(exported_states[settled_order.customer_name], 'Fully Paid')

        # Display fallback must not relax final-settlement or cleanup protection.
        settlement = self.client.post(
            reverse('pages:payment_update_by_order_ajax'),
            data=json.dumps({
                'order_id': open_order.pk,
                'balance_payment_amount': '500.00',
            }),
            content_type='application/json',
        )
        self.assertEqual(settlement.status_code, 409, settlement.content)
        for order in (open_order, settled_order):
            order.cleanup_payments = list(order.payments.all())
            self.assertFalse(has_valid_fully_paid_cleanup_structure(order))

        open_payment.refresh_from_db()
        settled_payment.refresh_from_db()
        open_order.refresh_from_db()
        settled_order.refresh_from_db()
        self.assertIsNone(open_payment.payment_type)
        self.assertIsNone(settled_payment.payment_type)
        self.assertEqual(open_payment.amount, Decimal('500.00'))
        self.assertEqual(settled_payment.amount, Decimal('500.00'))
        self.assertEqual(open_order.balance_payment, Decimal('500.00'))
        self.assertEqual(settled_order.balance_payment, Decimal('0.00'))

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
        self.assertEqual(order.payments.get().payment_type, Payment.TYPE_DOWN_PAYMENT)
        self.assertEqual(order.payments.get().payment_status, Payment.STATUS_DOWN_PAYMENT)
        self.assertEqual(order.total, Decimal('1500.00'))
        self.assertEqual(order.balance_payment, Decimal('1000.00'))

    def test_dropoff_delivery_fee_is_separate_from_order_payment_and_revenue(self):
        customer = Customer.objects.create(
            first_name='Dropoff', last_name='Fee', email='dropoff-fee@example.com', phone='09170000101'
        )
        order = Order.objects.create(
            customer=customer,
            notes='[DROP OFF]',
            total=Decimal('2000.00'),
            balance_payment=Decimal('0.00'),
        )
        Payment.objects.create(
            order=order,
            amount=Decimal('2000.00'),
            payment_status=Payment.STATUS_FULLY_PAID,
            payment_type=Payment.TYPE_FULL_PAYMENT,
            payment_method='cash',
        )

        response = self.client.post(
            reverse('pages:order_update_rider_ajax'),
            data=json.dumps({'order_id': order.pk, 'delivery_fee_charge': '150.00'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200, response.content)
        order.refresh_from_db()
        self.assertEqual(order.delivery_fee_charge, Decimal('150.00'))
        self.assertEqual(order.total, Decimal('2000.00'))
        self.assertEqual(order.balance_payment, Decimal('0.00'))
        self.assertEqual(order.payments.count(), 1)
        self.assertEqual(
            self.client.get(reverse('pages:dashboard')).context['total_revenue'],
            Decimal('2000.00'),
        )

        for replacement in ('0.00', '100.00', '200.00'):
            with self.subTest(replacement=replacement):
                duplicate = self.client.post(
                    reverse('pages:order_update_rider_ajax'),
                    data=json.dumps({
                        'order_id': order.pk,
                        'delivery_fee_charge': replacement,
                    }),
                    content_type='application/json',
                )
                self.assertEqual(duplicate.status_code, 409, duplicate.content)

        pickup_conversion = self.client.post(
            reverse('pages:order_update_ajax'),
            data=json.dumps({'order_id': order.pk, 'notes': '[PICK UP]'}),
            content_type='application/json',
        )
        self.assertEqual(pickup_conversion.status_code, 409, pickup_conversion.content)
        order.refresh_from_db()
        self.assertEqual(order.delivery_fee_charge, Decimal('150.00'))
        self.assertEqual(order.notes, '[DROP OFF]')

    def test_zero_delivery_fee_cannot_be_saved_as_the_one_time_fee(self):
        customer = Customer.objects.create(
            first_name='Zero', last_name='Fee', email='zero-fee@example.com', phone='09170000104'
        )
        order = Order.objects.create(
            customer=customer,
            notes='[DROP OFF]',
            total=Decimal('1000.00'),
        )

        response = self.client.post(
            reverse('pages:order_update_rider_ajax'),
            data=json.dumps({'order_id': order.pk, 'delivery_fee_charge': '0.00'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400, response.content)
        order.refresh_from_db()
        self.assertEqual(order.delivery_fee_charge, Decimal('0.00'))

    def test_pickup_rejects_delivery_fee_and_keeps_zero(self):
        customer = Customer.objects.create(
            first_name='Pickup', last_name='Fee', email='pickup-fee@example.com', phone='09170000102'
        )
        order = Order.objects.create(
            customer=customer,
            notes='[PICK UP]',
            total=Decimal('1000.00'),
        )

        response = self.client.post(
            reverse('pages:order_update_rider_ajax'),
            data=json.dumps({'order_id': order.pk, 'delivery_fee_charge': '100.00'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400, response.content)
        order.refresh_from_db()
        self.assertEqual(order.delivery_fee_charge, Decimal('0.00'))

    def test_pickup_allows_blank_contact_and_cash_down_then_balance_payment(self):
        payload = {
            'customer_email': 'pickup-cash@kres.local',
            'customer_first_name': 'Pickup',
            'customer_last_name': 'Customer',
            'customer_phone': '',
            'sender_name': 'Pickup Customer',
            'sender_phone': '',
            'receiver_name': '',
            'receiver_phone': '',
            'delivery_address': '',
            'delivery_date': (timezone.localdate() + timedelta(days=1)).isoformat(),
            'delivery_time': '10:30',
            'notes': '[PICK UP]',
            'items': [{'product_name': 'Pickup Bouquet', 'quantity': 1, 'unit_price': '1000.00'}],
            'payment_method': 'cash',
            'payment_status': 'down_payment',
            'payment_amount': '400.00',
        }

        created = self.client.post(
            reverse('pages:order_create_ajax'),
            data=json.dumps(payload),
            content_type='application/json',
        )

        self.assertEqual(created.status_code, 200, created.content)
        order = Order.objects.get()
        initial_payment = order.payments.get(payment_type=Payment.TYPE_DOWN_PAYMENT)
        self.assertEqual(order.sender_phone, '')
        self.assertEqual(order.customer_phone, '')
        self.assertEqual(order.delivery_address, '')
        self.assertEqual(order.delivery_fee_charge, Decimal('0.00'))
        self.assertEqual(order.balance_payment, Decimal('600.00'))
        self.assertEqual(initial_payment.payment_method, 'cash')

        settled = self.client.post(
            reverse('pages:payment_update_by_order_ajax'),
            data=json.dumps({'order_id': order.pk, 'balance_payment_amount': '600.00'}),
            content_type='application/json',
        )

        self.assertEqual(settled.status_code, 200, settled.content)
        final_payment = order.payments.get(payment_type=Payment.TYPE_BALANCE_PAYMENT)
        self.assertEqual(final_payment.payment_method, 'cash')
        self.assertEqual(order.payments.count(), 2)

    def test_dropoff_still_requires_sender_contact_number(self):
        payload = {
            'customer_email': 'dropoff-no-phone@kres.local',
            'customer_first_name': 'Dropoff',
            'customer_phone': '',
            'sender_name': 'Dropoff Customer',
            'sender_phone': '',
            'receiver_name': 'Receiver',
            'receiver_phone': '09170000110',
            'delivery_address': 'Cagayan de Oro',
            'notes': '[DROP OFF]',
            'items': [{'product_name': 'Dropoff Bouquet', 'quantity': 1, 'unit_price': '1000.00'}],
            'payment_method': 'cash',
            'payment_status': 'fully_paid',
            'payment_amount': '1000.00',
        }

        response = self.client.post(
            reverse('pages:order_create_ajax'),
            data=json.dumps(payload),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400, response.content)
        self.assertFalse(Order.objects.exists())

    def test_saving_rider_name_does_not_clear_existing_delivery_fee(self):
        customer = Customer.objects.create(
            first_name='Rider', last_name='Fee', email='rider-fee@example.com', phone='09170000103'
        )
        order = Order.objects.create(
            customer=customer,
            notes='[DROP OFF]',
            delivery_fee_charge=Decimal('175.00'),
            total=Decimal('1000.00'),
        )

        response = self.client.post(
            reverse('pages:order_update_rider_ajax'),
            data=json.dumps({'order_id': order.pk, 'rider_name': 'Juan Rider'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200, response.content)
        order.refresh_from_db()
        self.assertEqual(order.rider_name, 'Juan Rider')
        self.assertEqual(order.delivery_fee_charge, Decimal('175.00'))

    def test_create_order_preserves_exact_currency_strings(self):
        payload = {
            'customer_email': 'exact-money@kres.local',
            'customer_first_name': 'Exact',
            'customer_last_name': 'Money',
            'customer_phone': '09170000000',
            'sender_name': 'Exact Money',
            'sender_phone': '09170000000',
            'receiver_name': 'Exact Money',
            'receiver_phone': '09170000000',
            'delivery_address': 'Cagayan de Oro',
            'delivery_date': (timezone.localdate() + timedelta(days=1)).isoformat(),
            'delivery_time': '10:30',
            'notes': '[DROP OFF]',
            'items': [{'product_name': 'Exact Bouquet', 'quantity': 1, 'unit_price': '499.00'}],
            'payment_method': 'gcash_james',
            'payment_status': 'pending',
            'payment_amount': '400.00',
            'balance_payment': '99.00',
        }

        response = self.client.post(
            reverse('pages:order_create_ajax'), data=payload, content_type='application/json'
        )

        self.assertEqual(response.status_code, 200, response.content)
        order = Order.objects.get()
        payment = order.payments.get()
        self.assertEqual(response.json()['order']['total'], '499.00')
        self.assertEqual(response.json()['payment']['amount'], '400.00')
        self.assertEqual(order.items.get().unit_price, Decimal('499.00'))
        self.assertEqual(order.total, Decimal('499.00'))
        self.assertEqual(payment.amount, Decimal('400.00'))
        self.assertEqual(order.balance_payment, Decimal('99.00'))

    def test_1799_order_derives_799_balance_without_inventing_cents(self):
        payload = {
            'customer_email': 'exact-1799@kres.local',
            'customer_first_name': 'Exact',
            'customer_last_name': 'Example',
            'customer_phone': '09170000001',
            'sender_name': 'Exact Example',
            'sender_phone': '09170000001',
            'receiver_name': 'Exact Example',
            'receiver_phone': '09170000001',
            'delivery_address': 'Cagayan de Oro',
            'delivery_date': (timezone.localdate() + timedelta(days=1)).isoformat(),
            'delivery_time': '10:30',
            'notes': '[DROP OFF]',
            'items': [{'product_name': '1799 Bouquet', 'quantity': 1, 'unit_price': '1799'}],
            'payment_method': 'gcash_banban',
            'payment_status': 'pending',
            'payment_amount': '1000',
            'balance_payment': '',
        }

        response = self.client.post(
            reverse('pages:order_create_ajax'), data=payload, content_type='application/json'
        )

        self.assertEqual(response.status_code, 200, response.content)
        order = Order.objects.get()
        payment = order.payments.get()
        self.assertEqual(order.items.get().unit_price, Decimal('1799.00'))
        self.assertEqual(order.total, Decimal('1799.00'))
        self.assertEqual(payment.amount, Decimal('1000.00'))
        self.assertEqual(order.balance_payment, Decimal('799.00'))
        self.assertEqual(order.additional_payment, '')
        self.assertEqual(payment.payment_status, Payment.STATUS_DOWN_PAYMENT)
        self.assertEqual(payment.payment_type, Payment.TYPE_DOWN_PAYMENT)

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
        payment = Payment.objects.create(
            order=completed,
            amount=Decimal('100.00'),
            payment_status=Payment.STATUS_FULLY_PAID,
            payment_type=Payment.TYPE_FULL_PAYMENT,
            payment_method='cash',
            payment_date=timezone.make_aware(datetime(2026, 6, 30, 10, 0)),
        )

        fake_now = timezone.make_aware(datetime(2026, 7, 1, 8, 0))
        with patch('pages.auto_delete_utils.timezone.now', return_value=fake_now):
            result = check_and_delete_completed_orders()

        self.assertTrue(result)
        self.assertFalse(Order.objects.filter(pk=completed.pk).exists())
        self.assertTrue(Order.objects.filter(pk=pending.pk).exists())
        archive = MonthlySalesArchive.objects.get(month_name='June', year=2026)
        self.assertEqual(archive.sales_by_day['30'], 100.0)
        self.assertEqual(archive.orders_by_day['30'], [{
            'customer_name': 'TEST CUSTOMER',
            'total': 100.0,
        }])

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

    @override_settings(MONTHLY_CLEANUP_SECRET='test-cleanup-secret-at-least-32-chars')
    def test_scheduled_cleanup_endpoint_requires_post_and_correct_secret(self):
        endpoint = reverse('pages:scheduled_monthly_cleanup')

        self.assertEqual(self.client.get(endpoint).status_code, 405)
        self.assertEqual(self.client.post(endpoint, secure=True).status_code, 403)
        self.assertEqual(
            self.client.post(
                endpoint,
                HTTP_AUTHORIZATION='Bearer incorrect-secret',
                secure=True,
            ).status_code,
            403,
        )

    @override_settings(MONTHLY_CLEANUP_SECRET='')
    def test_scheduled_cleanup_endpoint_rejects_missing_server_secret(self):
        response = self.client.post(
            reverse('pages:scheduled_monthly_cleanup'),
            HTTP_AUTHORIZATION='Bearer any-value',
            secure=True,
        )
        self.assertEqual(response.status_code, 503)

    @override_settings(MONTHLY_CLEANUP_SECRET='test-cleanup-secret-at-least-32-chars')
    def test_scheduled_cleanup_catches_up_and_duplicate_trigger_is_safe(self):
        customer = Customer.objects.create(
            first_name='Scheduler', last_name='Catchup',
            email='scheduler-catchup@example.com', phone='09000000991',
        )
        eligible = Order.objects.create(
            customer=customer,
            status='completed',
            delivery_date=date(2026, 8, 28),
            total=Decimal('100.00'),
            balance_payment=Decimal('0.00'),
            delivery_fee_charge=Decimal('25.00'),
        )
        payment = Payment.objects.create(
            order=eligible,
            amount=Decimal('100.00'),
            payment_status=Payment.STATUS_FULLY_PAID,
            payment_type=Payment.TYPE_FULL_PAYMENT,
            payment_method='cash',
            payment_date=timezone.make_aware(datetime(2026, 8, 28, 10, 0)),
        )
        endpoint = reverse('pages:scheduled_monthly_cleanup')
        headers = {
            'HTTP_AUTHORIZATION': 'Bearer test-cleanup-secret-at-least-32-chars'
        }
        fake_now = timezone.make_aware(datetime(2026, 9, 2, 8, 0))

        with patch('pages.auto_delete_utils.timezone.now', return_value=fake_now):
            first = self.client.post(endpoint, secure=True, **headers)
            second = self.client.post(endpoint, secure=True, **headers)

        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(first.json()['status'], 'processed')
        self.assertEqual(second.status_code, 200, second.content)
        self.assertEqual(second.json()['status'], 'already_processed')
        self.assertEqual(MonthlyCleanupRun.objects.filter(month=date(2026, 8, 1)).count(), 1)
        self.assertFalse(Order.objects.filter(pk=eligible.pk).exists())
        self.assertFalse(Payment.objects.filter(pk=payment.pk).exists())
        archive = MonthlySalesArchive.objects.get(month_name='August', year=2026)
        self.assertEqual(archive.total_sales, Decimal('100.00'))

    def test_reports_exposes_no_manual_cleanup_control_or_internal_endpoint(self):
        self.user.is_staff = True
        self.user.save(update_fields=['is_staff'])

        response = self.client.get(reverse('pages:reports'))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Reset Previous Month Completed Orders')
        self.assertNotContains(response, reverse('pages:scheduled_monthly_cleanup'))

    def test_reports_exposes_lightweight_archived_current_year_months(self):
        MonthlySalesArchive.objects.create(
            month_name='August', year=2026,
            sales_by_day={'30': 1500.0},
            orders_by_day={'30': [{
                'customer_name': 'Louis Baslan',
                'total': 1500.0,
            }]},
            total_sales=Decimal('1500.00'),
        )
        retained_customer = Customer.objects.create(
            first_name='Pending', last_name='Customer',
            email='pending-report@example.com', phone='09000000883',
        )
        retained_order = Order.objects.create(
            customer=retained_customer, status='pending',
            delivery_date=date(2026, 8, 30), total=Decimal('500.00'),
            balance_payment=Decimal('300.00'),
        )
        Payment.objects.create(
            order=retained_order, amount=Decimal('200.00'),
            payment_type=Payment.TYPE_DOWN_PAYMENT,
            payment_status=Payment.STATUS_DOWN_PAYMENT,
            payment_method='cash',
            payment_date=timezone.make_aware(datetime(2026, 8, 30, 12, 0)),
        )
        fake_now = timezone.make_aware(datetime(2026, 9, 2, 8, 0))

        with patch('pages.auto_delete_utils.timezone.now', return_value=fake_now):
            response = self.client.get(reverse('pages:reports'))

        year_data = response.context['reports_year_data']
        self.assertEqual(year_data['8']['customers_by_day']['30'][0], {
            'customer_name': 'Louis Baslan',
            'total': 1500.0,
        })
        self.assertEqual(year_data['8']['sales_by_day']['30'], 1700.0)
        self.assertEqual(year_data['8']['customers_by_day']['30'], [
            {'customer_name': 'Louis Baslan', 'total': 1500.0},
            {'customer_name': 'PENDING CUSTOMER', 'total': 200.0},
        ])
        self.assertIn('9', year_data)

    @override_settings(MONTHLY_CLEANUP_SECRET='test-cleanup-secret-at-least-32-chars')
    def test_january_monthly_then_yearly_cleanup_is_ordered_and_idempotent(self):
        customer = Customer.objects.create(
            first_name='Year', last_name='Boundary',
            email='year-boundary@example.com', phone='09000000882',
        )
        eligible = Order.objects.create(
            customer=customer, status='completed', delivery_date=date(2026, 12, 31),
            total=Decimal('100.00'), balance_payment=Decimal('0.00'),
        )
        Payment.objects.create(
            order=eligible, amount=Decimal('100.00'),
            payment_type=Payment.TYPE_FULL_PAYMENT,
            payment_status=Payment.STATUS_FULLY_PAID,
            payment_method='cash',
            payment_date=timezone.make_aware(datetime(2026, 12, 31, 10, 0)),
        )
        december_pending = Order.objects.create(
            customer=customer, status='pending', delivery_date=date(2026, 12, 31),
            total=Decimal('200.00'), balance_payment=Decimal('200.00'),
        )
        endpoint = reverse('pages:scheduled_monthly_cleanup')
        headers = {
            'HTTP_AUTHORIZATION': 'Bearer test-cleanup-secret-at-least-32-chars'
        }
        fake_now = timezone.make_aware(datetime(2027, 1, 1, 8, 0))

        with patch('pages.auto_delete_utils.timezone.now', return_value=fake_now):
            first = self.client.post(endpoint, secure=True, **headers)
            repeated = self.client.post(endpoint, secure=True, **headers)
            # The scheduler endpoint is deliberately unauthenticated and uses a
            # secure request context. Restore the UI session explicitly before
            # verifying the authenticated Reports page.
            self.client.force_login(self.user)
            reports_response = self.client.get(
                reverse('pages:reports'),
                secure=True,
            )

        self.assertEqual(first.json()['yearly_reports'], 'processed')
        self.assertEqual(repeated.json()['status'], 'already_processed')
        self.assertEqual(
            repeated.json()['yearly_reports'],
            'not_due_or_already_processed',
        )
        self.assertFalse(Order.objects.filter(pk=eligible.pk).exists())
        self.assertTrue(Order.objects.filter(pk=december_pending.pk).exists())
        self.assertFalse(MonthlySalesArchive.objects.filter(year=2026).exists())
        snapshot = YearlySalesSnapshot.objects.get(year=2026)
        self.assertEqual(snapshot.total_yearly_sales, Decimal('100.00'))
        self.assertEqual(snapshot.calendar_data, {})
        self.assertEqual(snapshot.all_months_archive, {})
        self.assertEqual(YearlySalesSnapshot.objects.filter(year=2026).count(), 1)
        self.assertEqual(reports_response.status_code, 200, reports_response.content)
        self.assertEqual(set(reports_response.context['reports_year_data']), {'1'})

    def test_monthly_cleanup_requires_valid_fully_paid_payment_structure(self):
        previous_month_date = date(2026, 6, 15)
        current_month_date = date(2026, 7, 1)
        paid_at = timezone.make_aware(datetime(2026, 6, 15, 10, 0))

        def customer(suffix):
            return Customer.objects.create(
                first_name=suffix,
                last_name='Cleanup',
                email=f'{suffix.lower()}-cleanup@example.com',
                phone=f'0917000{len(suffix):04d}',
            )

        full_customer = customer('Full')
        shared_customer = customer('Shared')
        down_only_customer = customer('DownOnly')
        positive_customer = customer('Positive')
        pending_customer = customer('Pending')
        pending_down_customer = customer('PendingDown')
        current_customer = customer('Current')
        legacy_customer = customer('Legacy')

        eligible_full = Order.objects.create(
            customer=full_customer, status='completed', delivery_date=previous_month_date,
            total=Decimal('100.00'), balance_payment=Decimal('0.00'),
        )
        full_payment = Payment.objects.create(
            order=eligible_full, amount=Decimal('100.00'),
            payment_type=Payment.TYPE_FULL_PAYMENT,
            payment_status=Payment.STATUS_FULLY_PAID,
            payment_method='cash', payment_date=paid_at,
        )

        eligible_split = Order.objects.create(
            customer=shared_customer, status='completed', delivery_date=previous_month_date,
            total=Decimal('100.00'), balance_payment=Decimal('0.00'),
        )
        split_down = Payment.objects.create(
            order=eligible_split, amount=Decimal('40.00'),
            payment_type=Payment.TYPE_DOWN_PAYMENT,
            payment_status=Payment.STATUS_DOWN_PAYMENT,
            payment_method='cash', payment_date=paid_at,
        )
        split_balance = Payment.objects.create(
            order=eligible_split, amount=Decimal('60.00'),
            payment_type=Payment.TYPE_BALANCE_PAYMENT,
            payment_status=Payment.STATUS_FULLY_PAID,
            payment_method='cash', payment_date=paid_at,
        )
        protected_shared_order = Order.objects.create(
            customer=shared_customer, status='pending', delivery_date=previous_month_date,
            total=Decimal('50.00'), balance_payment=Decimal('50.00'),
        )

        down_only = Order.objects.create(
            customer=down_only_customer, status='completed', delivery_date=previous_month_date,
            total=Decimal('100.00'), balance_payment=Decimal('60.00'),
        )
        down_only_payment = Payment.objects.create(
            order=down_only, amount=Decimal('40.00'),
            payment_type=Payment.TYPE_DOWN_PAYMENT,
            payment_status=Payment.STATUS_DOWN_PAYMENT,
            payment_method='cash', payment_date=paid_at,
        )

        positive_balance = Order.objects.create(
            customer=positive_customer, status='completed', delivery_date=previous_month_date,
            total=Decimal('100.00'), balance_payment=Decimal('40.00'),
        )
        Payment.objects.create(
            order=positive_balance, amount=Decimal('60.00'),
            payment_type=Payment.TYPE_FULL_PAYMENT,
            payment_status=Payment.STATUS_FULLY_PAID,
            payment_method='cash', payment_date=paid_at,
        )

        pending_order = Order.objects.create(
            customer=pending_customer, status='pending', delivery_date=previous_month_date,
            total=Decimal('100.00'), balance_payment=Decimal('0.00'),
        )
        Payment.objects.create(
            order=pending_order, amount=Decimal('100.00'),
            payment_type=Payment.TYPE_FULL_PAYMENT,
            payment_status=Payment.STATUS_FULLY_PAID,
            payment_method='cash', payment_date=paid_at,
        )

        pending_down_order = Order.objects.create(
            customer=pending_down_customer, status='pending',
            delivery_date=previous_month_date,
            total=Decimal('100.00'), balance_payment=Decimal('60.00'),
        )
        pending_down_payment = Payment.objects.create(
            order=pending_down_order, amount=Decimal('40.00'),
            payment_type=Payment.TYPE_DOWN_PAYMENT,
            payment_status=Payment.STATUS_DOWN_PAYMENT,
            payment_method='cash', payment_date=paid_at,
        )

        current_month_order = Order.objects.create(
            customer=current_customer, status='completed', delivery_date=current_month_date,
            total=Decimal('100.00'), balance_payment=Decimal('0.00'),
        )
        Payment.objects.create(
            order=current_month_order, amount=Decimal('100.00'),
            payment_type=Payment.TYPE_FULL_PAYMENT,
            payment_status=Payment.STATUS_FULLY_PAID,
            payment_method='cash', payment_date=paid_at,
        )

        legacy_order = Order.objects.create(
            customer=legacy_customer, status='completed', delivery_date=previous_month_date,
            total=Decimal('100.00'), balance_payment=Decimal('0.00'),
        )
        legacy_payment = Payment.objects.create(
            order=legacy_order, amount=Decimal('100.00'), payment_type=None,
            payment_status='completed', payment_method='cash', payment_date=paid_at,
        )

        fake_now = timezone.make_aware(datetime(2026, 7, 1, 8, 0))
        with patch('pages.auto_delete_utils.timezone.now', return_value=fake_now):
            first = check_and_delete_completed_orders()
            second = check_and_delete_completed_orders()

        self.assertEqual(first[1:3], (2, 1))
        self.assertFalse(second)
        self.assertFalse(Order.objects.filter(pk__in=[eligible_full.pk, eligible_split.pk]).exists())
        self.assertFalse(Payment.objects.filter(pk__in=[
            full_payment.pk, split_down.pk, split_balance.pk,
        ]).exists())
        self.assertTrue(Order.objects.filter(pk=down_only.pk).exists())
        self.assertTrue(Payment.objects.filter(pk=down_only_payment.pk).exists())
        self.assertTrue(Order.objects.filter(pk=positive_balance.pk).exists())
        self.assertTrue(Order.objects.filter(pk=pending_order.pk).exists())
        self.assertTrue(Order.objects.filter(pk=pending_down_order.pk).exists())
        self.assertTrue(Payment.objects.filter(pk=pending_down_payment.pk).exists())
        self.assertTrue(Order.objects.filter(pk=current_month_order.pk).exists())
        self.assertTrue(Order.objects.filter(pk=legacy_order.pk).exists())
        self.assertTrue(Payment.objects.filter(pk=legacy_payment.pk).exists())
        self.assertTrue(Order.objects.filter(pk=protected_shared_order.pk).exists())
        self.assertTrue(Customer.objects.filter(pk=shared_customer.pk).exists())
        self.assertFalse(Customer.objects.filter(pk=full_customer.pk).exists())

    def test_monthly_reset_catches_up_previous_month_but_not_older_months(self):
        customer = Customer.objects.create(
            first_name='Catchup', last_name='Customer', email='catchup@example.com', phone='09000000002'
        )
        april = Order.objects.create(
            customer=customer, status='completed', delivery_date=date(2026, 4, 30), total=Decimal('100.00')
        )
        june = Order.objects.create(
            customer=customer, status='completed', delivery_date=date(2026, 6, 30), total=Decimal('200.00')
        )
        july = Order.objects.create(
            customer=customer, status='completed', delivery_date=date(2026, 7, 30),
            total=Decimal('250.00'), balance_payment=Decimal('0.00')
        )
        Payment.objects.create(
            order=july, amount=Decimal('250.00'),
            payment_status=Payment.STATUS_FULLY_PAID,
            payment_type=Payment.TYPE_FULL_PAYMENT,
            payment_method='cash',
            payment_date=timezone.make_aware(datetime(2026, 7, 30, 10, 0)),
        )
        july_pending = Order.objects.create(
            customer=customer, status='pending', delivery_date=date(2026, 7, 5), total=Decimal('300.00')
        )

        fake_now = timezone.make_aware(datetime(2026, 8, 15, 8, 0))
        with patch('pages.auto_delete_utils.timezone.now', return_value=fake_now):
            result = check_and_delete_completed_orders()

        self.assertTrue(result)
        self.assertEqual(Order.objects.filter(pk__in=[april.pk, june.pk]).count(), 2)
        self.assertFalse(Order.objects.filter(pk=july.pk).exists())
        self.assertTrue(Order.objects.filter(pk=july_pending.pk).exists())
        self.assertTrue(MonthlyCleanupRun.objects.filter(month=date(2026, 7, 1)).exists())

    def test_lightweight_archive_aggregates_same_customer_same_day_payments(self):
        customer = Customer.objects.create(
            first_name='Louis', last_name='Baslan',
            email='louis-archive@example.com', phone='09000000881',
        )
        order = Order.objects.create(
            customer=customer, status='completed', delivery_date=date(2026, 8, 30),
            total=Decimal('1500.00'), balance_payment=Decimal('0.00'),
            delivery_fee_charge=Decimal('150.00'),
        )
        paid_at = timezone.make_aware(datetime(2026, 8, 30, 10, 0))
        Payment.objects.create(
            order=order, amount=Decimal('1000.00'),
            payment_type=Payment.TYPE_DOWN_PAYMENT,
            payment_status=Payment.STATUS_DOWN_PAYMENT,
            payment_method='cash', payment_date=paid_at,
        )
        Payment.objects.create(
            order=order, amount=Decimal('500.00'),
            payment_type=Payment.TYPE_BALANCE_PAYMENT,
            payment_status=Payment.STATUS_FULLY_PAID,
            payment_method='cash', payment_date=paid_at,
        )

        fake_now = timezone.make_aware(datetime(2026, 9, 2, 8, 0))
        with patch('pages.auto_delete_utils.timezone.now', return_value=fake_now):
            result = check_and_delete_completed_orders()

        self.assertEqual(result[1], 1)
        archive = MonthlySalesArchive.objects.get(month_name='August', year=2026)
        self.assertEqual(archive.orders_by_day['30'], [{
            'customer_name': 'LOUIS BASLAN',
            'total': 1500.0,
        }])
        self.assertEqual(archive.sales_by_day['30'], 1500.0)
        self.assertEqual(archive.total_sales, Decimal('1500.00'))
        archived_row = archive.orders_by_day['30'][0]
        self.assertEqual(set(archived_row), {'customer_name', 'total'})

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
        payment = Payment.objects.create(
            order=late_order,
            amount=Decimal('100.00'),
            payment_status=Payment.STATUS_FULLY_PAID,
            payment_type=Payment.TYPE_FULL_PAYMENT,
            payment_method='cash',
            payment_date=timezone.make_aware(datetime(2026, 7, 30, 10, 0)),
        )

        fake_now = timezone.make_aware(datetime(2026, 8, 1, 8, 0))
        with patch('pages.auto_delete_utils.timezone.now', return_value=fake_now):
            check_and_delete_completed_orders()

        archive = MonthlySalesArchive.objects.get(month_name='July', year=2026)
        self.assertEqual(archive.total_sales, Decimal('150.00'))
        self.assertEqual(archive.orders_by_day['1'][0]['customer_name'], 'Unnamed Customer')
        self.assertEqual(archive.orders_by_day['30'], [{
            'customer_name': 'ARCHIVE CUSTOMER',
            'total': 100.0,
        }])

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
            order=completed, amount=Decimal('200.00'),
            payment_status=Payment.STATUS_FULLY_PAID,
            payment_type=Payment.TYPE_FULL_PAYMENT,
            payment_method='cash',
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

    def test_done_updates_only_order_status_and_is_idempotent(self):
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
            order=order,
            amount=Decimal('250.00'),
            payment_status=Payment.STATUS_DOWN_PAYMENT,
            payment_type=Payment.TYPE_DOWN_PAYMENT,
            payment_method='cash',
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
        self.assertEqual(order.balance_payment, Decimal('500.00'))
        self.assertEqual(payment.payment_status, Payment.STATUS_DOWN_PAYMENT)
        self.assertEqual(payment.amount, Decimal('250.00'))

    def test_orders_template_uses_named_routes_and_wires_view_details(self):
        response = self.client.get(reverse('pages:orders'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse('pages:order_update_status_ajax'))
        self.assertContains(response, reverse('pages:payment_get_by_order_ajax'))
        self.assertContains(response, "document.querySelectorAll('.btn-view-details')")
        self.assertContains(response, 'FINAL BALANCE PAYMENT')
        self.assertContains(response, 'id="detailFinalBalancePaymentInput"')
        self.assertContains(response, 'class="details-tab-content"')
        self.assertContains(response, 'data-detail-panel="order"')
        self.assertContains(response, 'data-detail-panel="delivery"')
        self.assertContains(response, 'data-detail-panel="note"')
        self.assertNotContains(response, 'class="details-tab-nav"')
        self.assertContains(response, 'id="detailDeliveryAddressRow"')
        self.assertContains(response, 'id="detailDropoffAddress"')
        self.assertContains(
            response,
            "detailDeliveryAddressRow.style.display = isPickup ? 'none' : '';",
        )
        self.assertContains(response, 'No special note')
        self.assertNotContains(response, 'id="balancePayment"')
        self.assertNotContains(response, 'id="detailPaymentStatusSelect"')

    def test_exact_balance_payment_creates_second_immutable_row(self):
        customer = Customer.objects.create(
            first_name='Payment', last_name='Flow', email='payment-flow@example.com', phone='09000000021'
        )
        order = Order.objects.create(
            customer=customer, total=Decimal('499.00'), balance_payment=Decimal('99.00')
        )
        payment = Payment.objects.create(
            order=order,
            amount=Decimal('400.00'),
            payment_status=Payment.STATUS_DOWN_PAYMENT,
            payment_type=Payment.TYPE_DOWN_PAYMENT,
            payment_method='cash',
        )

        response = self.client.post(
            reverse('pages:payment_update_by_order_ajax'),
            data=json.dumps({'order_id': order.pk, 'balance_payment_amount': '99.00'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['order_amount'], '499.00')
        self.assertEqual(response.json()['down_payment'], '400.00')
        self.assertEqual(response.json()['settlement_amount'], '99.00')
        self.assertEqual(response.json()['balance_payment'], '0.00')
        self.assertEqual(response.json()['payment']['status'], Payment.STATUS_FULLY_PAID)
        self.assertEqual(response.json()['payment']['type'], Payment.TYPE_BALANCE_PAYMENT)
        order.refresh_from_db()
        payment.refresh_from_db()
        self.assertEqual(order.total, Decimal('499.00'))
        self.assertEqual(order.additional_payment, '')
        self.assertEqual(order.balance_payment, Decimal('0.00'))
        self.assertEqual(payment.amount, Decimal('400.00'))
        self.assertEqual(payment.payment_status, Payment.STATUS_DOWN_PAYMENT)
        self.assertEqual(order.payments.count(), 2)
        final_payment = order.payments.get(payment_type=Payment.TYPE_BALANCE_PAYMENT)
        self.assertEqual(final_payment.amount, Decimal('99.00'))
        self.assertEqual(final_payment.payment_status, Payment.STATUS_FULLY_PAID)

    def test_partial_balance_payment_is_rejected(self):
        customer = Customer.objects.create(
            first_name='Partial', last_name='Flow', email='partial-flow@example.com', phone='09000000022'
        )
        order = Order.objects.create(
            customer=customer, total=Decimal('499.00'), balance_payment=Decimal('99.00')
        )
        Payment.objects.create(
            order=order,
            amount=Decimal('400.00'),
            payment_status=Payment.STATUS_DOWN_PAYMENT,
            payment_type=Payment.TYPE_DOWN_PAYMENT,
            payment_method='cash',
        )
        endpoint = reverse('pages:payment_update_by_order_ajax')

        first = self.client.post(
            endpoint,
            data=json.dumps({'order_id': order.pk, 'balance_payment_amount': '50.00'}),
            content_type='application/json',
        )
        self.assertEqual(first.status_code, 400, first.content)
        order.refresh_from_db()
        payment = order.payments.get(payment_type=Payment.TYPE_DOWN_PAYMENT)
        self.assertEqual(order.total, Decimal('499.00'))
        self.assertEqual(order.balance_payment, Decimal('99.00'))
        self.assertEqual(payment.amount, Decimal('400.00'))
        self.assertEqual(order.payments.count(), 1)

    def test_zero_negative_and_below_balance_payments_are_rejected(self):
        customer = Customer.objects.create(
            first_name='Exact', last_name='Only', email='exact-only@example.com', phone='09000000026'
        )
        order = Order.objects.create(
            customer=customer, total=Decimal('499.00'), balance_payment=Decimal('99.00')
        )
        Payment.objects.create(
            order=order,
            amount=Decimal('400.00'),
            payment_status=Payment.STATUS_DOWN_PAYMENT,
            payment_type=Payment.TYPE_DOWN_PAYMENT,
            payment_method='cash',
        )
        endpoint = reverse('pages:payment_update_by_order_ajax')

        for invalid_amount in ('0', '-1', '98.99'):
            with self.subTest(invalid_amount=invalid_amount):
                response = self.client.post(
                    endpoint,
                    data=json.dumps({'order_id': order.pk, 'balance_payment_amount': invalid_amount}),
                    content_type='application/json',
                )
                self.assertEqual(response.status_code, 400, response.content)
                self.assertEqual(order.payments.count(), 1)

        order.refresh_from_db()
        self.assertEqual(order.balance_payment, Decimal('99.00'))

    def test_legacy_null_type_payment_cannot_receive_new_balance_row(self):
        customer = Customer.objects.create(
            first_name='Stored', last_name='Balance', email='stored-balance@example.com', phone='09000000024'
        )
        order = Order.objects.create(
            customer=customer, total=Decimal('499.00'), balance_payment=Decimal('75.00')
        )
        payment = Payment.objects.create(
            order=order, amount=Decimal('400.00'), payment_status='pending', payment_method='cash'
        )

        response = self.client.post(
            reverse('pages:payment_update_by_order_ajax'),
            data=json.dumps({'order_id': order.pk, 'balance_payment_amount': '75.00'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 409, response.content)
        order.refresh_from_db()
        payment.refresh_from_db()
        self.assertEqual(order.total, Decimal('499.00'))
        self.assertEqual(payment.amount, Decimal('400.00'))
        self.assertEqual(order.balance_payment, Decimal('75.00'))
        self.assertEqual(payment.payment_status, 'pending')
        self.assertIsNone(payment.payment_type)
        self.assertEqual(order.payments.count(), 1)

    def test_full_payment_creates_one_row_and_rejects_second_payment(self):
        payload = {
            'customer_email': 'full-payment@kres.local',
            'customer_first_name': 'Full',
            'customer_last_name': 'Payment',
            'customer_phone': '09170000009',
            'sender_name': 'Full Payment',
            'sender_phone': '09170000009',
            'receiver_name': 'Full Payment',
            'receiver_phone': '09170000009',
            'delivery_address': 'Cagayan de Oro',
            'delivery_date': (timezone.localdate() + timedelta(days=1)).isoformat(),
            'delivery_time': '10:30',
            'notes': '[DROP OFF]',
            'items': [{'product_name': 'Full Bouquet', 'quantity': 1, 'unit_price': '2200.00'}],
            'payment_method': 'cash',
            'payment_status': 'fully_paid',
            'payment_amount': '2200.00',
        }
        created = self.client.post(
            reverse('pages:order_create_ajax'), data=payload, content_type='application/json'
        )

        self.assertEqual(created.status_code, 200, created.content)
        self.assertEqual(created.json()['payment']['status'], Payment.STATUS_FULLY_PAID)
        order = Order.objects.get()
        payment = order.payments.get()
        self.assertEqual(payment.payment_type, Payment.TYPE_FULL_PAYMENT)
        self.assertEqual(payment.payment_status, Payment.STATUS_FULLY_PAID)
        self.assertEqual(payment.amount, Decimal('2200.00'))
        self.assertEqual(order.balance_payment, Decimal('0.00'))
        self.assertEqual(
            self.client.get(reverse('pages:dashboard')).context['total_revenue'],
            Decimal('2200.00'),
        )
        self.assertEqual(
            self.client.get(reverse('pages:reports')).context['total_monthly_sales']['total_revenue'],
            Decimal('2200.00'),
        )
        self.assertEqual(
            self.client.get(reverse('pages:payments')).context['total_revenue'],
            Decimal('2200.00'),
        )

        second = self.client.post(
            reverse('pages:payment_update_by_order_ajax'),
            data=json.dumps({'order_id': order.pk, 'balance_payment_amount': '1.00'}),
            content_type='application/json',
        )
        self.assertEqual(second.status_code, 409, second.content)
        self.assertEqual(order.payments.count(), 1)

    def test_third_payment_is_rejected_after_exact_balance_payment(self):
        customer = Customer.objects.create(
            first_name='Two', last_name='Only', email='two-only@example.com', phone='09000000025'
        )
        order = Order.objects.create(
            customer=customer, total=Decimal('2200.00'), balance_payment=Decimal('1200.00')
        )
        Payment.objects.create(
            order=order,
            amount=Decimal('1000.00'),
            payment_status=Payment.STATUS_DOWN_PAYMENT,
            payment_type=Payment.TYPE_DOWN_PAYMENT,
            payment_method='cash',
        )
        endpoint = reverse('pages:payment_update_by_order_ajax')
        first = self.client.post(
            endpoint,
            data=json.dumps({'order_id': order.pk, 'balance_payment_amount': '1200.00'}),
            content_type='application/json',
        )
        third = self.client.post(
            endpoint,
            data=json.dumps({'order_id': order.pk, 'balance_payment_amount': '1200.00'}),
            content_type='application/json',
        )

        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(third.status_code, 409, third.content)
        self.assertEqual(order.payments.count(), 2)

    def test_payment_above_balance_settles_without_negative_balance(self):
        customer = Customer.objects.create(
            first_name='Over', last_name='Settlement', email='over-settlement@example.com', phone='09000000028'
        )
        order = Order.objects.create(
            customer=customer, total=Decimal('2200.00'), balance_payment=Decimal('1200.00')
        )
        down_payment = Payment.objects.create(
            order=order,
            amount=Decimal('1000.00'),
            payment_status=Payment.STATUS_DOWN_PAYMENT,
            payment_type=Payment.TYPE_DOWN_PAYMENT,
            payment_method='cash',
        )

        response = self.client.post(
            reverse('pages:payment_update_by_order_ajax'),
            data=json.dumps({'order_id': order.pk, 'balance_payment_amount': '1250.00'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200, response.content)
        order.refresh_from_db()
        down_payment.refresh_from_db()
        self.assertEqual(order.total, Decimal('2200.00'))
        self.assertEqual(order.balance_payment, Decimal('0.00'))
        self.assertEqual(down_payment.amount, Decimal('1000.00'))
        self.assertEqual(order.payments.count(), 2)
        final_payment = order.payments.get(payment_type=Payment.TYPE_BALANCE_PAYMENT)
        self.assertEqual(final_payment.amount, Decimal('1250.00'))
        self.assertEqual(final_payment.payment_status, Payment.STATUS_FULLY_PAID)

    def test_revenue_pages_sum_received_rows_without_order_completion_or_duplicates(self):
        customer = Customer.objects.create(
            first_name='Ledger', last_name='Revenue', email='ledger-revenue@example.com', phone='09000000029'
        )
        order = Order.objects.create(
            customer=customer,
            total=Decimal('2200.00'),
            balance_payment=Decimal('1200.00'),
            status='pending',
        )
        Payment.objects.create(
            order=order,
            amount=Decimal('1000.00'),
            payment_status=Payment.STATUS_DOWN_PAYMENT,
            payment_type=Payment.TYPE_DOWN_PAYMENT,
            payment_method='cash',
        )

        dashboard_response = self.client.get(reverse('pages:dashboard'))
        reports_response = self.client.get(reverse('pages:reports'))
        payments_response = self.client.get(reverse('pages:payments'))

        self.assertEqual(dashboard_response.context['total_revenue'], Decimal('1000.00'))
        self.assertEqual(
            reports_response.context['total_monthly_sales']['total_revenue'],
            Decimal('1000.00'),
        )
        self.assertEqual(
            reports_response.context['total_monthly_sales']['total_transactions'],
            1,
        )
        self.assertEqual(payments_response.context['total_revenue'], Decimal('1000.00'))

        settlement = self.client.post(
            reverse('pages:payment_update_by_order_ajax'),
            data=json.dumps({'order_id': order.pk, 'balance_payment_amount': '1250.00'}),
            content_type='application/json',
        )
        duplicate = self.client.post(
            reverse('pages:payment_update_by_order_ajax'),
            data=json.dumps({'order_id': order.pk, 'balance_payment_amount': '1250.00'}),
            content_type='application/json',
        )

        self.assertEqual(settlement.status_code, 200, settlement.content)
        self.assertEqual(duplicate.status_code, 409, duplicate.content)
        self.assertEqual(order.payments.count(), 2)
        self.assertEqual(
            self.client.get(reverse('pages:dashboard')).context['total_revenue'],
            Decimal('2250.00'),
        )
        self.assertEqual(
            self.client.get(reverse('pages:reports')).context['total_monthly_sales']['total_revenue'],
            Decimal('2250.00'),
        )
        self.assertEqual(
            self.client.get(reverse('pages:payments')).context['total_revenue'],
            Decimal('2250.00'),
        )

        order.status = 'completed'
        order.save(update_fields=['status', 'updated_at'])
        self.assertEqual(
            self.client.get(reverse('pages:dashboard')).context['total_revenue'],
            Decimal('2250.00'),
        )

    def test_get_payment_is_read_only(self):
        customer = Customer.objects.create(
            first_name='Legacy', last_name='Flow', email='legacy-flow@example.com', phone='09000000023'
        )
        order = Order.objects.create(
            customer=customer, total=Decimal('499.00'), balance_payment=Decimal('99.00'),
            additional_payment='99.00',
        )
        payment = Payment.objects.create(
            order=order, amount=Decimal('400.00'), payment_status='pending', payment_method='cash'
        )

        response = self.client.get(
            reverse('pages:payment_get_by_order_ajax'), {'order_id': order.pk}
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['down_payment'], '400.00')
        self.assertEqual(response.json()['balance_payment'], '99.00')
        self.assertEqual(response.json()['payment']['payment_status'], STATE_UNRECONCILED)
        self.assertTrue(response.json()['has_legacy_payment'])
        self.assertFalse(response.json()['can_pay_balance'])
        order.refresh_from_db()
        payment.refresh_from_db()
        self.assertEqual(order.balance_payment, Decimal('99.00'))
        self.assertEqual(payment.amount, Decimal('400.00'))
        self.assertEqual(payment.payment_status, 'pending')

    def test_typed_down_payment_get_exposes_exact_locked_balance_action(self):
        customer = Customer.objects.create(
            first_name='View', last_name='Balance', email='view-balance@example.com', phone='09000000027'
        )
        order = Order.objects.create(
            customer=customer, total=Decimal('2200.00'), balance_payment=Decimal('999.00')
        )
        Payment.objects.create(
            order=order,
            amount=Decimal('1000.00'),
            payment_status=Payment.STATUS_DOWN_PAYMENT,
            payment_type=Payment.TYPE_DOWN_PAYMENT,
            payment_method='cash',
        )

        response = self.client.get(
            reverse('pages:payment_get_by_order_ajax'), {'order_id': order.pk}
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['required_balance_payment'], '1200.00')
        self.assertEqual(response.json()['balance_payment'], '1200.00')
        self.assertFalse(response.json()['has_legacy_payment'])
        self.assertTrue(response.json()['can_pay_balance'])
        order.refresh_from_db()
        self.assertEqual(order.balance_payment, Decimal('999.00'))
