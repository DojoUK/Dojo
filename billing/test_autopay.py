import json
from datetime import date
from decimal import Decimal
from io import StringIO
from unittest import mock

import stripe
from django.core import mail
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from billing import autopay, payments
from billing.models import AutoPay, Invoice, PaymentSettings
from billing.payment_forms import apply_post
from billing.test_payments import CARD, WEBHOOK_SECRET, form, signed
from jobs.models import JobRun
from jobs.registry import is_running
from members.models import Member
from organisations.models import Organisation


def stripe_object(values):
    """A real Stripe object (no .get(), like the library returns), not a plain dict."""
    return stripe.StripeObject.construct_from(values, 'sk_test_abc')


def setup_intent(member, method='card', status='succeeded', invoice=None):
    payment_method = {'id': 'pm_1', 'type': method}
    if method == 'card':
        payment_method['card'] = {'brand': 'visa', 'last4': '4242'}
    else:
        payment_method['bacs_debit'] = {'last4': '6789'}
    return stripe_object({
        'id': 'seti_1', 'object': 'setup_intent', 'status': status, 'mandate': 'mandate_1' if method == 'bacs_debit' else None,
        'metadata': {'member_pk': str(member.pk), 'organisation': member.organisation.slug, 'purpose': 'autopay',
                     **({'invoice_pk': str(invoice.pk)} if invoice else {})},
        'payment_method': payment_method,
    })


def intent(status='succeeded', amount=4500, **extra):
    return stripe_object({'id': 'pi_1', 'object': 'payment_intent', 'status': status, 'amount_received': amount, **extra})


@override_settings(STRIPE_PUBLIC_KEY='', STRIPE_SECRET_KEY='', STRIPE_WEBHOOK_SECRET='')
class AutoPayTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organisation.objects.create(name='Test Club', slug='test-club', email='hello@testclub.example')
        cls.member = Member.objects.create(organisation=cls.org, name='Ann Lee', email='ann@example.com',
                                           stripe_customer_id='cus_1')

    def setUp(self):
        self.configure(auto_pay='optional')

    def configure(self, **values):
        payment_settings = PaymentSettings.objects.filter(organisation=self.org).first() or PaymentSettings(organisation=self.org)
        self.assertEqual(apply_post(payment_settings, {**CARD, **form(**values)}), [])
        payment_settings.save()

    def save_card(self, method='card'):
        return autopay.save_from_setup_intent(self.org, setup_intent(self.member, method))

    def invoice(self, amount='45.00'):
        with self.captureOnCommitCallbacks(execute=True):
            return Invoice.objects.create(organisation=self.org, member=self.member, amount=Decimal(amount),
                                          period='October 2026', due_date=date(2026, 10, 31))

    def refresh(self, invoice):
        invoice.refresh_from_db()
        return invoice


class SettingsTests(AutoPayTestCase):
    def test_auto_pay_needs_card_payments(self):
        payment_settings = PaymentSettings.objects.get()
        self.assertIn('Paying automatically needs card payments (Stripe) switched on.',
                      apply_post(payment_settings, form(auto_pay='optional')))

    def test_on_and_off(self):
        self.assertTrue(autopay.enabled(self.org))
        self.configure(auto_pay='off')
        self.assertFalse(autopay.enabled(self.org))


class SetupTests(AutoPayTestCase):
    def test_saving_a_card_or_direct_debit(self):
        saved = self.save_card()
        self.assertEqual((saved.payment_method_id, saved.method_type, saved.label), ('pm_1', 'card', 'Visa ending 4242'))
        saved = self.save_card('bacs_debit')
        self.assertEqual((saved.label, saved.mandate_id), ('Direct Debit, account ending 6789', 'mandate_1'))
        self.assertEqual(AutoPay.objects.count(), 1)

    def test_another_clubs_member_cannot_be_set_up(self):
        other = Organisation.objects.create(name='Other', slug='other')
        self.assertIsNone(autopay.save_from_setup_intent(other, setup_intent(self.member)))
        self.assertIsNone(autopay.save_from_setup_intent(self.org, setup_intent(self.member, status='requires_action')))

    def test_stripe_setup_page_is_for_this_member_and_these_methods(self):
        with mock.patch('stripe.checkout.Session.create', return_value=mock.Mock(url='https://stripe.test/setup')) as create:
            url = autopay.start_setup(self.member, 'https://club.example/p/abc/')
        self.assertEqual(url, 'https://stripe.test/setup')
        kwargs = create.call_args.kwargs
        self.assertEqual((kwargs['mode'], kwargs['customer'], kwargs['api_key']), ('setup', 'cus_1', 'sk_test_abc'))
        # Current Stripe API versions refuse a method list; the club's dashboard decides.
        self.assertNotIn('payment_method_types', kwargs)
        self.assertEqual(kwargs['success_url'], 'https://club.example/p/abc/?autopay_session={CHECKOUT_SESSION_ID}')

    def test_returning_from_the_pay_page_takes_that_invoice_at_once(self):
        invoice = self.invoice()
        older = self.invoice()
        session = stripe_object({'id': 'cs_1', 'mode': 'setup', 'setup_intent': setup_intent(self.member, invoice=invoice).to_dict()})
        token = payments.pay_token(invoice)
        with mock.patch('stripe.checkout.Session.retrieve', return_value=session), \
                mock.patch('stripe.PaymentIntent.create', return_value=intent()) as charge:
            response = self.client.get(reverse('invoice_pay', kwargs={'token': token}) + '?autopay_session=cs_1')
        self.assertContains(response, 'Automatic payments are set up with your Visa ending 4242')
        self.assertContains(response, 'This invoice is paid')
        charge.assert_called_once()
        self.assertEqual(self.refresh(invoice).status, 'paid')
        self.assertEqual(self.refresh(older).autopay_status, '')  # only the invoice they were paying

    def test_setting_up_does_not_charge_invoices_already_owed(self):
        owed = self.invoice()
        self.save_card()
        self.assertEqual(self.refresh(owed).autopay_status, '')
        self.assertEqual(self.refresh(self.invoice()).autopay_status, 'due')  # new ones are

    def test_portal_turn_off(self):
        self.save_card()
        invoice = self.invoice()
        self.client.post(reverse('portal_autopay_off', kwargs={'token': self.member.token}))
        self.assertFalse(AutoPay.objects.get().active)
        self.assertEqual(self.refresh(invoice).autopay_status, '')


class CollectionTests(AutoPayTestCase):
    def test_new_invoices_are_queued_only_for_members_who_pay_automatically(self):
        self.assertEqual(self.refresh(self.invoice()).autopay_status, '')
        self.save_card()
        self.assertEqual(self.refresh(self.invoice()).autopay_status, 'due')

    def test_card_is_charged_for_the_balance_once(self):
        self.save_card()
        invoice = self.invoice()
        with mock.patch('stripe.PaymentIntent.create', return_value=intent()) as charge:
            self.assertEqual(autopay.collect_due(), 1)
            self.assertEqual(autopay.collect_due(), 0)  # nothing left waiting
        kwargs = charge.call_args.kwargs
        self.assertEqual((kwargs['amount'], kwargs['payment_method'], kwargs['customer']), (4500, 'pm_1', 'cus_1'))
        self.assertTrue(kwargs['off_session'] and kwargs['confirm'])
        self.assertNotIn('payment_method_types', kwargs)
        self.assertEqual(kwargs['automatic_payment_methods'], {'enabled': True, 'allow_redirects': 'never'})
        self.assertEqual(kwargs['idempotency_key'], f'dojo-test-club-invoice-{invoice.pk}-attempt-1')
        invoice = self.refresh(invoice)
        self.assertEqual((invoice.status, invoice.autopay_status), ('paid', 'paid'))
        self.assertEqual(invoice.payments.get().amount, Decimal('45.00'))

    def test_direct_debit_waits_for_stripe_to_say_it_cleared(self):
        self.save_card('bacs_debit')
        invoice = self.invoice()
        with mock.patch('stripe.PaymentIntent.create', return_value=intent(status='processing')) as charge:
            autopay.collect_due()
        self.assertEqual(charge.call_args.kwargs['mandate'], 'mandate_1')
        invoice = self.refresh(invoice)
        self.assertEqual((invoice.status, invoice.autopay_status), ('unpaid', 'processing'))

    def test_declined_card_emails_the_member_a_way_to_pay(self):
        self.save_card()
        invoice = self.invoice()
        declined = stripe.error.CardError('Your card was declined.', None, 'card_declined')
        with mock.patch('stripe.PaymentIntent.create', side_effect=declined):
            autopay.collect_due()
        invoice = self.refresh(invoice)
        self.assertEqual((invoice.status, invoice.autopay_status), ('unpaid', 'failed'))
        self.assertEqual(invoice.autopay_error, 'Your card was declined.')
        html = mail.outbox[-1].alternatives[0][0]
        self.assertIn("We couldn't take this payment automatically: Your card was declined.", html)
        self.assertIn(payments.pay_url(invoice), html)

    def test_bank_wants_approval(self):
        self.save_card()
        invoice = self.invoice()
        with mock.patch('stripe.PaymentIntent.create', return_value=intent(status='requires_action')):
            autopay.collect_due()
        self.assertEqual(self.refresh(invoice).autopay_status, 'failed')

    def test_nothing_is_charged_once_auto_pay_is_switched_off_for_the_club(self):
        self.save_card()
        invoice = self.invoice()
        self.configure(auto_pay='off')
        with mock.patch('stripe.PaymentIntent.create') as charge:
            autopay.collect_due()
        charge.assert_not_called()
        self.assertEqual(self.refresh(invoice).autopay_status, '')

    def test_invoice_email_says_nothing_to_do(self):
        from billing.emails import send_invoice_email
        self.save_card()
        send_invoice_email(self.refresh(self.invoice()))
        self.assertIn('This will be collected automatically from your Visa ending 4242', mail.outbox[-1].alternatives[0][0])


class WebhookTests(AutoPayTestCase):
    def post(self, event_type, obj):
        payload = json.dumps({'id': 'evt_1', 'object': 'event', 'type': event_type, 'data': {'object': obj}})
        return self.client.post(reverse('stripe_webhook_org', kwargs={'org_slug': 'test-club'}), payload,
                                content_type='application/json', HTTP_STRIPE_SIGNATURE=signed(payload, WEBHOOK_SECRET))

    def processing_invoice(self):
        self.save_card('bacs_debit')
        invoice = self.invoice()
        Invoice.objects.filter(pk=invoice.pk).update(autopay_status='processing')
        return invoice

    def metadata(self, invoice):
        return {'invoice_pk': str(invoice.pk), 'organisation': 'test-club', 'purpose': 'autopay'}

    def test_direct_debit_cleared(self):
        invoice = self.processing_invoice()
        self.post('payment_intent.succeeded', {'id': 'pi_dd', 'object': 'payment_intent', 'amount_received': 4500,
                                               'metadata': self.metadata(invoice)})
        invoice = self.refresh(invoice)
        self.assertEqual((invoice.status, invoice.autopay_status), ('paid', 'paid'))
        self.post('payment_intent.succeeded', {'id': 'pi_dd', 'object': 'payment_intent', 'amount_received': 4500,
                                               'metadata': self.metadata(invoice)})
        self.assertEqual(invoice.payments.count(), 1)

    def test_direct_debit_failed(self):
        invoice = self.processing_invoice()
        self.post('payment_intent.payment_failed', {
            'id': 'pi_dd', 'object': 'payment_intent', 'metadata': self.metadata(invoice),
            'last_payment_error': {'message': 'The account has insufficient funds.'}})
        invoice = self.refresh(invoice)
        self.assertEqual((invoice.status, invoice.autopay_error), ('unpaid', 'The account has insufficient funds.'))
        self.assertEqual(len(mail.outbox), 1)

    def test_a_declined_card_is_only_reported_once(self):
        self.save_card()
        invoice = self.invoice()
        declined = stripe.error.CardError('Your card was declined.', None, 'card_declined')
        with mock.patch('stripe.PaymentIntent.create', side_effect=declined):
            autopay.collect_due()
        self.post('payment_intent.payment_failed', {
            'id': 'pi_card', 'object': 'payment_intent', 'metadata': self.metadata(invoice),
            'last_payment_error': {'message': 'Your card was declined.'}})
        self.assertEqual(len(mail.outbox), 1)

    def test_one_clubs_webhook_cannot_touch_another_clubs_invoice(self):
        other = Organisation.objects.create(name='Other', slug='other')
        other_member = Member.objects.create(organisation=other, name='Bo')
        other_invoice = Invoice.objects.create(organisation=other, member=other_member, amount=10, period='Oct',
                                               due_date=date(2026, 10, 31))
        self.post('payment_intent.succeeded', {'id': 'pi_x', 'object': 'payment_intent', 'amount_received': 1000,
                                               'metadata': self.metadata(other_invoice)})
        self.assertEqual(self.refresh(other_invoice).status, 'unpaid')

    def test_saved_card_arrives_by_webhook(self):
        session = {'id': 'cs_1', 'object': 'checkout.session', 'mode': 'setup', 'setup_intent': 'seti_1'}
        with mock.patch('stripe.SetupIntent.retrieve', return_value=setup_intent(self.member)) as retrieve:
            self.post('checkout.session.completed', session)
        retrieve.assert_called_once_with('seti_1', api_key='sk_test_abc', expand=['payment_method'])
        self.assertEqual(AutoPay.objects.get().label, 'Visa ending 4242')


class SchedulerTests(AutoPayTestCase):
    def test_runs_jobs_and_records_when(self):
        self.assertFalse(is_running())
        self.save_card()
        invoice = self.invoice()
        with mock.patch('stripe.PaymentIntent.create', return_value=intent()):
            call_command('run_scheduler', '--once', stdout=StringIO())
        self.assertEqual(self.refresh(invoice).status, 'paid')
        self.assertTrue(is_running())
        self.assertEqual(JobRun.objects.get().last_error, '')

    def test_a_failing_job_is_recorded_not_fatal(self):
        with mock.patch('billing.autopay.collect_due', side_effect=RuntimeError('boom')):
            call_command('run_scheduler', '--once', stdout=StringIO())
        self.assertIn('RuntimeError: boom', JobRun.objects.get().last_error)


class PortalTests(AutoPayTestCase):
    def test_portal_offers_set_up_then_shows_the_saved_method(self):
        self.org.set_features({'member_portal': True})
        self.org.save()
        url = reverse('member_portal', kwargs={'token': self.member.token})
        self.assertContains(self.client.get(url), 'Set up automatic payments')
        self.save_card()
        response = self.client.get(url)
        self.assertContains(response, 'Paying automatically')
        self.assertContains(response, 'Visa ending 4242')


class StripeDownTests(AutoPayTestCase):
    def test_a_stripe_error_sends_the_member_back_with_a_message(self):
        invoice = self.invoice()
        token = payments.pay_token(invoice)
        down = stripe.error.APIConnectionError('Could not connect')
        with mock.patch('stripe.checkout.Session.create', side_effect=down):
            response = self.client.post(reverse('invoice_pay_card', kwargs={'token': token}), follow=True)
            self.assertContains(response, "Card payments aren't working right now")
            response = self.client.post(reverse('invoice_pay_autopay', kwargs={'token': token}), follow=True)
            self.assertContains(response, "Card payments aren't working right now")
