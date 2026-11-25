"""Bacs Direct Debit and Apple/Google Pay at Stripe Checkout: what members can pay with, and
Direct Debits that clear (or fail) days after the member leaves Stripe's page."""
import json
from decimal import Decimal
from types import SimpleNamespace
from unittest import mock

from django.core import mail
from django.urls import reverse

from billing import payments
from billing.models import Invoice, PaymentSettings
from billing.stripe_setup import WEBHOOK_EVENTS, methods_text, payment_methods
from billing.test_payments import CARD, PaymentsTestCase, signed
from members.models import Member


def method(available, value):
    return {'available': available, 'display_preference': {'overridable': None, 'preference': value, 'value': value}}


DEFAULT_CONFIG = {
    'id': 'pmc_1', 'object': 'payment_method_configuration', 'active': True, 'is_default': True,
    'card': method(True, 'on'), 'apple_pay': method(True, 'on'), 'google_pay': method(True, 'on'),
    'bacs_debit': method(False, 'on'),   # switched on, still being activated by Stripe
}


def listing(*configs):
    result = mock.Mock()
    result.auto_paging_iter.return_value = list(configs)
    return result


class WebhookCase(PaymentsTestCase):
    def setUp(self):
        self.configure(**CARD)

    def send(self, event_type, payment_status=None, mode='payment', **extra):
        session = {'id': 'cs_1', 'object': 'checkout.session', 'mode': mode, 'amount_total': 4500,
                   'payment_intent': 'pi_1', 'metadata': {'invoice_pk': str(self.invoice.pk)}, **extra}
        if payment_status:
            session['payment_status'] = payment_status
        payload = json.dumps({'id': 'evt_1', 'object': 'event', 'type': event_type, 'data': {'object': session}})
        response = self.client.post(reverse('stripe_webhook_org', kwargs={'org_slug': 'test-club'}), payload,
                                    content_type='application/json', HTTP_STRIPE_SIGNATURE=signed(payload))
        self.assertEqual(response.status_code, 200)
        self.invoice.refresh_from_db()


class CheckoutDirectDebitTests(WebhookCase):
    def test_a_direct_debit_is_not_paid_until_it_clears(self):
        self.send('checkout.session.completed', payment_status='unpaid')
        self.assertEqual((self.invoice.status, self.invoice.autopay_status), ('unpaid', 'processing'))
        self.assertFalse(self.invoice.payments.exists())

        self.send('checkout.session.async_payment_succeeded', payment_status='paid')
        self.assertEqual((self.invoice.status, self.invoice.autopay_status), ('paid', 'paid'))
        payment = self.invoice.payments.get()
        self.assertEqual((payment.amount, payment.stripe_payment_id), (Decimal('45.00'), 'pi_1'))
        self.send('checkout.session.async_payment_succeeded', payment_status='paid')   # Stripe retries
        self.assertEqual(self.invoice.payments.count(), 1)

    def test_a_failed_direct_debit_is_flagged_and_the_member_told(self):
        self.send('checkout.session.completed', payment_status='unpaid')
        self.send('checkout.session.async_payment_failed', payment_status='unpaid')
        self.assertEqual((self.invoice.status, self.invoice.autopay_status), ('unpaid', 'failed'))
        self.assertIn("Direct Debit didn't go through", self.invoice.autopay_error)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("We couldn't take this payment: The Direct Debit didn't go through", mail.outbox[0].body)
        # The pay page says so, and offers the other ways to pay again.
        page = self.client.get(reverse('invoice_pay', kwargs={'token': payments.pay_token(self.invoice)}))
        self.assertContains(page, "We couldn't take this payment: The Direct Debit didn&#x27;t go through")
        self.assertContains(page, 'Pay £45.00')

    def test_cards_and_wallets_are_paid_straight_away(self):
        self.send('checkout.session.completed', payment_status='paid')
        self.assertEqual(self.invoice.status, 'paid')
        self.assertEqual(self.invoice.autopay_status, '')

    def test_the_pay_page_says_a_direct_debit_is_on_its_way(self):
        self.send('checkout.session.completed', payment_status='unpaid')
        page = self.client.get(reverse('invoice_pay', kwargs={'token': payments.pay_token(self.invoice)}))
        self.assertContains(page, 'This is being paid by Direct Debit. It usually takes about 3 working days.')
        self.assertNotContains(page, 'Pay £45.00')   # no second payment while this one clears

    def test_the_webhook_asks_for_the_new_events(self):
        self.assertIn('checkout.session.async_payment_succeeded', WEBHOOK_EVENTS)
        self.assertIn('checkout.session.async_payment_failed', WEBHOOK_EVENTS)
        payment_settings = PaymentSettings.objects.get()
        payment_settings.set_secrets({**payment_settings.get_secrets(), 'stripe_webhook_events':
                                      'checkout.session.completed checkout.session.expired payment_intent.succeeded '
                                      'payment_intent.payment_failed'})
        self.assertTrue(payment_settings.stripe_webhook_needs_update)


class SubscriptionTests(WebhookCase):
    def setUp(self):
        super().setUp()
        self.org.set_features({'member_portal': True})
        self.org.save()
        Member.objects.filter(pk=self.member.pk).update(monthly_fee=Decimal('30.00'), stripe_customer_id='cus_1')
        self.member.refresh_from_db()

    def test_subscriptions_offer_the_clubs_payment_methods(self):
        # Stripe API versions from 2026-09-30 refuse payment_method_types with a 400 error.
        with mock.patch('stripe.checkout.Session.create', return_value=mock.Mock(url='https://stripe.test/cs')) as create:
            response = self.client.post(reverse('portal_subscribe', kwargs={'token': self.member.token}))
        self.assertEqual(response['Location'], 'https://stripe.test/cs')
        self.assertNotIn('payment_method_types', create.call_args.kwargs)
        self.assertEqual(create.call_args.kwargs['mode'], 'subscription')

    def test_a_direct_debit_subscription_starts_when_the_first_payment_clears(self):
        extra = {'subscription': 'sub_1', 'metadata': {'member_pk': str(self.member.pk)}}
        self.send('checkout.session.completed', payment_status='unpaid', mode='subscription', **extra)
        self.member.refresh_from_db()
        self.assertEqual((self.member.stripe_subscription_id, self.member.subscription_status), ('sub_1', 'incomplete'))
        portal = self.client.get(reverse('member_portal', kwargs={'token': self.member.token}))
        self.assertContains(portal, 'Autopay starting')
        self.assertNotContains(portal, 'Set up autopay £30.00/month')

        self.send('checkout.session.async_payment_succeeded', payment_status='paid', mode='subscription', **extra)
        self.member.refresh_from_db()
        self.assertEqual(self.member.subscription_status, 'active')

    def test_a_card_subscription_is_active_straight_away(self):
        self.send('checkout.session.completed', payment_status='paid', mode='subscription',
                  subscription='sub_1', metadata={'member_pk': str(self.member.pk)})
        self.member.refresh_from_db()
        self.assertEqual(self.member.subscription_status, 'active')


class InstantPaymentTests(PaymentsTestCase):
    def setUp(self):
        self.configure(**CARD)

    def checkout(self, **kwargs):
        with mock.patch('stripe.checkout.Session.create', return_value=mock.Mock(url='https://stripe.test/cs')) as create:
            payments.start_checkout(self.invoice, 'https://dojo.test/ok', 'https://dojo.test/back', **kwargs)
        return create.call_args.kwargs

    def test_invoices_can_be_paid_by_direct_debit(self):
        self.assertNotIn('excluded_payment_method_types', self.checkout())

    def test_payments_that_must_clear_now_leave_direct_debit_out(self):
        self.assertEqual(self.checkout(instant_only=True)['excluded_payment_method_types'], ['bacs_debit'])

    def test_booking_a_single_session_is_instant_only(self):
        from classes import booking
        with mock.patch('billing.payments.start_checkout', return_value='https://stripe.test/cs') as start:
            booking.start_payment(SimpleNamespace(invoice=self.invoice, pk=7), 'https://dojo.test/book')
        self.assertTrue(start.call_args.kwargs['instant_only'])


class PaymentMethodsTests(PaymentsTestCase):
    def test_reads_the_default_configuration(self):
        other = {**DEFAULT_CONFIG, 'id': 'pmc_2', 'is_default': False, 'bacs_debit': method(True, 'on')}
        with mock.patch('stripe.PaymentMethodConfiguration.list', return_value=listing(other, DEFAULT_CONFIG)) as lister:
            rows = payment_methods('sk_test_abc')
        self.assertEqual(lister.call_args.kwargs['api_key'], 'sk_test_abc')
        self.assertEqual(rows, [('card', 'Card', 'on'), ('apple_pay', 'Apple Pay', 'on'),
                                ('google_pay', 'Google Pay', 'on'), ('bacs_debit', 'Bacs Direct Debit', 'pending')])

    def test_switched_off_and_unavailable_methods(self):
        config = {**DEFAULT_CONFIG, 'google_pay': method(False, 'off')}
        del config['bacs_debit']   # not offered in the account's country
        with mock.patch('stripe.PaymentMethodConfiguration.list', return_value=listing(config)):
            rows = payment_methods('sk_test_abc')
        self.assertEqual([state for _, _, state in rows], ['on', 'on', 'off'])
        self.assertEqual(len(rows), 3)

    def test_stripe_errors_mean_unknown(self):
        import stripe
        with mock.patch('stripe.PaymentMethodConfiguration.list', side_effect=stripe.error.APIConnectionError('down')):
            self.assertIsNone(payment_methods('sk_test_abc'))

    def test_methods_text(self):
        self.assertEqual(methods_text(['card']), 'Card')
        self.assertEqual(methods_text(['bacs_debit', 'card']), 'Card or Direct Debit')
        self.assertEqual(methods_text(['card', 'apple_pay', 'google_pay', 'bacs_debit']),
                         'Card, Apple Pay, Google Pay or Direct Debit')
        self.assertEqual(methods_text([]), '')


class SettingsAndPayPageTests(PaymentsTestCase):
    def setUp(self):
        self.client.force_login(self.admin)
        self.configure(**CARD)
        self.settings_url = reverse('org_settings', kwargs={'org_slug': self.org.slug})
        self.pay_url = reverse('invoice_pay', kwargs={'token': payments.pay_token(self.invoice)})

    def check(self, config):
        with mock.patch('billing.stripe_setup.check_connection', return_value=(True, 'Connected to Test Club (test mode).')), \
                mock.patch('stripe.PaymentMethodConfiguration.list', return_value=listing(config)):
            return self.client.get(self.settings_url + '?check_stripe=1')

    def test_check_connection_shows_what_members_can_pay_with(self):
        response = self.check(DEFAULT_CONFIG)
        self.assertContains(response, 'Members can pay by:')
        self.assertContains(response, '<i class="bi bi-check-lg"></i> Apple Pay', html=False)
        self.assertContains(response, 'Bacs Direct Debit: waiting for Stripe')

    def test_the_pay_page_names_the_methods_found(self):
        self.assertContains(self.client.get(self.pay_url), 'Card, Apple Pay or Google Pay, through Stripe.')  # never checked
        self.check({**DEFAULT_CONFIG, 'bacs_debit': method(True, 'on')})
        page = self.client.get(self.pay_url)
        self.assertContains(page, 'Card, Apple Pay, Google Pay or Direct Debit, through Stripe.')
        self.assertContains(page, 'Pay £45.00 online')
        # Saving the payment settings again keeps what was found.
        self.configure(**CARD)
        self.assertContains(self.client.get(self.pay_url), 'Pay £45.00 online')

    def test_without_direct_debit_the_button_still_says_card(self):
        self.check(DEFAULT_CONFIG)   # Bacs still waiting for Stripe
        page = self.client.get(self.pay_url)
        self.assertContains(page, 'Card, Apple Pay or Google Pay, through Stripe.')
        self.assertContains(page, 'Pay £45.00 by card')
