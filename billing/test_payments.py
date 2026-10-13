import hashlib
import hmac
import json
import time
from datetime import date
from decimal import Decimal
from unittest import mock

import stripe
from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from billing import payments
from billing.emails import send_invoice_email
from billing.models import Invoice, Payment, PaymentSettings
from billing.payment_forms import apply_post
from members.models import Member
from organisations.models import Organisation, OrganisationMember

WEBHOOK_SECRET = 'whsec_test_secret'


def form(**values):
    return {f'pay_{k}': v for k, v in values.items()}


CARD = form(card='1', stripe_publishable_key='pk_test_abc', stripe_secret_key='sk_test_abc',
            stripe_webhook_secret=WEBHOOK_SECRET)
BANK = form(bank_transfer='1', bank_account_name='Test Club', bank_sort_code='12 34 56',
            bank_account_number='12345678')


def signed(payload, secret=WEBHOOK_SECRET):
    """A Stripe-Signature header for `payload`, as Stripe would send it."""
    timestamp = int(time.time())
    signature = hmac.new(secret.encode(), f'{timestamp}.{payload}'.encode(), hashlib.sha256).hexdigest()
    return f't={timestamp},v1={signature}'


@override_settings(STRIPE_PUBLIC_KEY='', STRIPE_SECRET_KEY='', STRIPE_WEBHOOK_SECRET='')
class PaymentsTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organisation.objects.create(name='Test Club', slug='test-club', email='hello@testclub.example')
        cls.admin = User.objects.create_user('admin', password='pw', email='admin@testclub.example')
        OrganisationMember.objects.create(user=cls.admin, organisation=cls.org, role='org_admin')
        cls.member = Member.objects.create(organisation=cls.org, name='Ann Lee', email='ann@example.com')
        cls.invoice = Invoice.objects.create(organisation=cls.org, member=cls.member, amount=Decimal('45.00'),
                                             period='October 2026', due_date=date(2026, 10, 31))

    def configure(self, **post):
        payment_settings = PaymentSettings.objects.filter(organisation=self.org).first() or PaymentSettings(organisation=self.org)
        self.assertEqual(apply_post(payment_settings, post), [])
        payment_settings.save()
        return payment_settings


class FormTests(PaymentsTestCase):
    def test_bank_details_are_checked_and_tidied(self):
        payment_settings = PaymentSettings(organisation=self.org)
        errors = apply_post(payment_settings, form(bank_transfer='1', bank_sort_code='1234', bank_account_number='12'))
        self.assertEqual(len(errors), 2)
        self.assertEqual(apply_post(payment_settings, BANK), [])
        self.assertEqual((payment_settings.bank_sort_code, payment_settings.bank_account_number), ('12-34-56', '12345678'))

    def test_stripe_keys_are_checked(self):
        payment_settings = PaymentSettings(organisation=self.org)
        self.assertIn('Card payments: the publishable key starts with pk_ (Stripe → Developers → API keys).',
                      apply_post(payment_settings, form(card='1', stripe_publishable_key='x', stripe_secret_key='sk_test_1')))
        self.assertIn('Card payments: one key is a test key and the other is live. Use a matching pair.',
                      apply_post(payment_settings, form(card='1', stripe_publishable_key='pk_test_1', stripe_secret_key='sk_live_1')))
        self.assertIn('The reference prefix can only use letters and numbers (up to 10).',
                      apply_post(payment_settings, form(reference_prefix='IN V!')))

    def test_secrets_are_encrypted_kept_when_blank_and_never_shown(self):
        self.configure(**CARD)
        payment_settings = self.configure(**form(card='1', stripe_publishable_key='pk_test_abc'))
        self.assertEqual(payment_settings.stripe_secret_key, 'sk_test_abc')
        self.assertNotIn('sk_test_abc', payment_settings.secrets)
        self.client.force_login(self.admin)
        html = self.client.get(reverse('org_settings', kwargs={'org_slug': self.org.slug})).content.decode()
        self.assertNotIn('sk_test_abc', html)
        self.assertNotIn(WEBHOOK_SECRET, html)
        self.assertIn('Saved: leave blank to keep', html)


class SettingsPageTests(PaymentsTestCase):
    def setUp(self):
        self.client.force_login(self.admin)
        self.url = reverse('org_settings', kwargs={'org_slug': self.org.slug})

    def test_save_from_settings(self):
        response = self.client.post(self.url, {'action': 'payments', **BANK, 'pay_cash': '1', 'pay_reference_prefix': 'tc'})
        self.assertTrue(response['Location'].endswith('#payments'))
        payment_settings = PaymentSettings.objects.get()
        self.assertTrue(payment_settings.accept_bank_transfer and payment_settings.accept_cash)
        self.assertFalse(payment_settings.accept_card)
        self.assertEqual(payment_settings.reference_prefix, 'TC')

    def test_tab_hidden_when_billing_is_off(self):
        self.assertContains(self.client.get(self.url), 'data-bs-target="#pane-payments"')
        self.org.set_features({'billing': False})
        self.org.save()
        self.assertNotContains(self.client.get(self.url), 'data-bs-target="#pane-payments"')

    def test_webhook_is_set_up_automatically(self):
        self.configure(**form(card='1', stripe_publishable_key='pk_test_abc', stripe_secret_key='sk_test_abc'))
        old = mock.Mock(url='http://localhost:8000/stripe/webhook/test-club/', id='we_old')
        with mock.patch('stripe.WebhookEndpoint.list') as listing, \
                mock.patch('stripe.WebhookEndpoint.delete') as delete, \
                mock.patch('stripe.WebhookEndpoint.create', return_value=mock.Mock(secret='whsec_new')) as create:
            listing.return_value.auto_paging_iter.return_value = [old]
            self.client.post(self.url, {'action': 'stripe_webhook'})
        delete.assert_called_once_with('we_old', api_key='sk_test_abc')
        self.assertEqual(create.call_args.kwargs['url'], 'http://localhost:8000/stripe/webhook/test-club/')
        self.assertEqual(create.call_args.kwargs['api_key'], 'sk_test_abc')
        self.assertEqual(PaymentSettings.objects.get().stripe_webhook_secret, 'whsec_new')

    def test_check_connection(self):
        self.configure(**CARD)
        # A real Stripe object: it has no .get(), unlike a dict.
        account = stripe.StripeObject.construct_from(
            {'id': 'acct_1', 'settings': {'dashboard': {'display_name': 'Test Club Ltd'}}}, 'sk_test_abc')
        with mock.patch('stripe.Account.retrieve', return_value=account) as retrieve:
            response = self.client.get(self.url + '?check_stripe=1')
        retrieve.assert_called_once_with(api_key='sk_test_abc')
        self.assertContains(response, 'Connected to Test Club Ltd (test mode).')

    def test_coaches_cannot_change_payment_settings(self):
        coach = User.objects.create_user('coach', password='pw')
        OrganisationMember.objects.create(user=coach, organisation=self.org, role='coach')
        self.client.force_login(coach)
        self.client.post(self.url, {'action': 'payments', **BANK})
        self.assertFalse(PaymentSettings.objects.exists())


class StripeKeyTests(PaymentsTestCase):
    @override_settings(STRIPE_PUBLIC_KEY='pk_env', STRIPE_SECRET_KEY='sk_env', STRIPE_WEBHOOK_SECRET='whsec_env')
    def test_clubs_without_settings_use_the_environment_and_settings_win(self):
        self.assertEqual(payments.stripe_keys(self.org).secret, 'sk_env')
        self.configure(**BANK)
        self.assertIsNone(payments.stripe_keys(self.org))  # chose not to take cards
        self.configure(**CARD)
        self.assertEqual(payments.stripe_keys(self.org).secret, 'sk_test_abc')


class PayPageTests(PaymentsTestCase):
    def url(self, invoice=None):
        return reverse('invoice_pay', kwargs={'token': payments.pay_token(invoice or self.invoice)})

    def test_shows_amount_reference_and_bank_details(self):
        self.configure(**BANK, **form(instructions='Ask a coach if you need help.'))
        response = self.client.get(self.url())
        self.assertContains(response, '£45.00')
        self.assertContains(response, f'INV{self.invoice.pk}')
        self.assertContains(response, '12-34-56')
        self.assertContains(response, 'Ask a coach if you need help.')
        self.assertNotContains(response, 'by card')
        self.assertNotContains(response, 'Lee')  # only the first name is shown

    def test_part_payments_reduce_the_amount_due(self):
        Payment.objects.create(invoice=self.invoice, amount=Decimal('20.00'), paid_at=timezone.now())
        self.assertContains(self.client.get(self.url()), '£25.00')

    def test_paid_invoice(self):
        Invoice.objects.filter(pk=self.invoice.pk).update(status='paid')
        self.assertContains(self.client.get(self.url()), 'This invoice is paid')

    def test_links_cannot_be_guessed_or_changed(self):
        token = payments.pay_token(self.invoice)
        self.assertEqual(self.client.get(reverse('invoice_pay', kwargs={'token': token[:-2] + 'xx'})).status_code, 404)
        self.assertEqual(self.client.get(reverse('invoice_pay', kwargs={'token': str(self.invoice.pk)})).status_code, 404)

    def test_card_payment_uses_the_club_key_and_the_balance(self):
        self.configure(**CARD)
        Payment.objects.create(invoice=self.invoice, amount=Decimal('5.50'), paid_at=timezone.now())
        self.assertContains(self.client.get(self.url()), 'Pay £39.50 by card')
        token = payments.pay_token(self.invoice)
        with mock.patch('stripe.checkout.Session.create', return_value=mock.Mock(url='https://checkout.stripe.test/s')) as create:
            response = self.client.post(reverse('invoice_pay_card', kwargs={'token': token}))
        self.assertEqual(response['Location'], 'https://checkout.stripe.test/s')
        kwargs = create.call_args.kwargs
        self.assertEqual(kwargs['api_key'], 'sk_test_abc')
        self.assertEqual(kwargs['line_items'][0]['price_data']['unit_amount'], 3950)
        self.assertEqual(kwargs['metadata'], {'invoice_pk': str(self.invoice.pk), 'organisation': 'test-club'})
        self.assertEqual(kwargs['customer_email'], 'ann@example.com')

    def test_no_card_payment_without_keys(self):
        with mock.patch('stripe.checkout.Session.create') as create:
            self.client.post(reverse('invoice_pay_card', kwargs={'token': payments.pay_token(self.invoice)}))
        create.assert_not_called()


class WebhookTests(PaymentsTestCase):
    def post(self, invoice_pk, slug='test-club', secret=WEBHOOK_SECRET, amount_total=4500):
        payload = json.dumps({
            'id': 'evt_1', 'object': 'event', 'type': 'checkout.session.completed',
            'data': {'object': {'id': 'cs_1', 'object': 'checkout.session', 'mode': 'payment',
                                'amount_total': amount_total, 'payment_intent': 'pi_1',
                                'metadata': {'invoice_pk': str(invoice_pk)}}},
        })
        return self.client.post(reverse('stripe_webhook_org', kwargs={'org_slug': slug}), payload,
                                content_type='application/json', HTTP_STRIPE_SIGNATURE=signed(payload, secret))

    def test_card_payment_marks_the_invoice_paid(self):
        self.configure(**CARD)
        self.assertEqual(self.post(self.invoice.pk).status_code, 200)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, 'paid')
        payment = self.invoice.payments.get()
        self.assertEqual((payment.method, payment.amount, payment.stripe_payment_id), ('stripe', Decimal('45.00'), 'pi_1'))
        self.post(self.invoice.pk)  # Stripe retries: no second payment
        self.assertEqual(self.invoice.payments.count(), 1)

    def test_wrong_signature_is_refused(self):
        self.configure(**CARD)
        self.assertEqual(self.post(self.invoice.pk, secret='whsec_wrong').status_code, 400)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, 'unpaid')

    def test_one_clubs_webhook_cannot_pay_another_clubs_invoice(self):
        self.configure(**CARD)
        other = Organisation.objects.create(name='Other Club', slug='other-club')
        other_member = Member.objects.create(organisation=other, name='Bo Day')
        other_invoice = Invoice.objects.create(organisation=other, member=other_member, amount=10,
                                               period='Oct', due_date=date(2026, 10, 31))
        self.assertEqual(self.post(other_invoice.pk).status_code, 200)
        other_invoice.refresh_from_db()
        self.assertEqual(other_invoice.status, 'unpaid')

    def test_club_without_a_webhook_secret_is_refused(self):
        self.configure(**form(card='1', stripe_publishable_key='pk_test_abc', stripe_secret_key='sk_test_abc'))
        self.assertEqual(self.post(self.invoice.pk).status_code, 400)


class InvoiceEmailTests(PaymentsTestCase):
    def test_email_has_a_pay_button_and_bank_details(self):
        self.configure(**CARD, **BANK)
        ok, _ = send_invoice_email(self.invoice)
        self.assertTrue(ok)
        html = mail.outbox[-1].alternatives[0][0]
        self.assertIn('Pay £45.00 now', html)
        self.assertIn(payments.pay_url(self.invoice), html)
        self.assertIn('Or pay by bank transfer', html)
        self.assertIn(f'INV{self.invoice.pk}', html)

    def test_without_card_payments_the_button_explains_how_to_pay(self):
        self.configure(**BANK)
        send_invoice_email(self.invoice)
        html = mail.outbox[-1].alternatives[0][0]
        self.assertIn('How to pay', html)
        self.assertNotIn('now</a>', html)


@override_settings(STRIPE_PUBLIC_KEY='', STRIPE_SECRET_KEY='', STRIPE_WEBHOOK_SECRET='')
class SetupWizardTests(TestCase):
    def test_payment_choices_at_setup(self):
        self.client.post(reverse('setup'), {
            'org_name': 'New Club', 'username': 'founder', 'admin_email': 'f@example.com',
            'password': 'a-long-password', 'password_confirm': 'a-long-password', **BANK, 'pay_cash': '1',
        })
        payment_settings = PaymentSettings.objects.get()
        self.assertEqual(payment_settings.organisation.name, 'New Club')
        self.assertTrue(payment_settings.accept_bank_transfer and payment_settings.accept_cash)

    def test_a_mistake_creates_nothing(self):
        response = self.client.post(reverse('setup'), {
            'org_name': 'New Club', 'username': 'founder', 'admin_email': 'f@example.com',
            'password': 'a-long-password', 'password_confirm': 'a-long-password',
            **form(bank_transfer='1', bank_sort_code='12'),
        })
        self.assertContains(response, 'sort code should be 6 digits')
        self.assertFalse(Organisation.objects.exists())


class PortalPayButtonTests(PaymentsTestCase):
    def setUp(self):
        self.org.set_features({'member_portal': True})
        self.org.save()
        self.url = reverse('member_portal', kwargs={'token': self.member.token})
        self.pay_page = reverse('invoice_pay', kwargs={'token': payments.pay_token(self.invoice)})

    def test_without_cards_the_button_explains_how_to_pay(self):
        self.configure(**BANK)
        response = self.client.get(self.url)
        self.assertContains(response, f'href="{self.pay_page}"')
        self.assertContains(response, 'How to pay')
        self.assertNotContains(response, 'disabled>Pay')

    def test_with_cards_the_button_shows_the_amount(self):
        self.configure(**CARD)
        response = self.client.get(self.url)
        self.assertContains(response, f'href="{self.pay_page}"')
        self.assertRegex(response.content.decode(), r'text-nowrap">\s*Pay\s*</a>')


class CheckoutDetailsTests(PaymentsTestCase):
    """What the member sees on Stripe's payment page."""

    def setUp(self):
        from billing.models import BillingPolicy, InvoiceItem
        from inventory.models import Product, ProductVariant
        policy = BillingPolicy.objects.create(organisation=self.org, name='Kids Termly', billing_cycle='termly', amount=50)
        gi = ProductVariant.objects.create(product=Product.objects.create(organisation=self.org, name='Judo Gi'),
                                           size='150cm', price=Decimal('25.00'))
        # £45 membership after a £5 discount, plus two gis at £25: £95 in all.
        self.kit_invoice = Invoice.objects.create(organisation=self.org, member=self.member, billing_policy=policy,
                                                  amount=Decimal('95.00'), discount_amount=Decimal('5.00'),
                                                  period='Autumn 2026', due_date=date(2026, 9, 30))
        InvoiceItem.objects.create(invoice=self.kit_invoice, variant=gi, quantity=2, unit_price=Decimal('25.00'))

    @staticmethod
    def total(lines):
        return sum(line['price_data']['unit_amount'] * line['quantity'] for line in lines)

    def test_itemised_with_plan_discount_and_kit(self):
        lines = payments.checkout_line_items(self.kit_invoice)
        names = [(line['price_data']['product_data']['name'], line['price_data']['unit_amount'], line['quantity']) for line in lines]
        self.assertEqual(names, [('Kids Termly: Autumn 2026', 4500, 1), ('Judo Gi — 150cm', 2500, 2)])
        self.assertIn('For Ann Lee', lines[0]['price_data']['product_data']['description'])
        self.assertIn(f'reference INV{self.kit_invoice.pk}', lines[0]['price_data']['product_data']['description'])
        self.assertIn('includes a £5.00 discount', lines[0]['price_data']['product_data']['description'])
        self.assertEqual(self.total(lines), 9500)

    def test_part_paid_invoices_show_what_is_left(self):
        Payment.objects.create(invoice=self.kit_invoice, amount=Decimal('30.00'), paid_at=timezone.now())
        lines = payments.checkout_line_items(self.kit_invoice)
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]['price_data']['product_data']['name'], 'Autumn 2026: remaining balance')
        self.assertIn('£30.00 of £95.00 already paid', lines[0]['price_data']['product_data']['description'])
        self.assertEqual(self.total(lines), 6500)

    def test_plain_invoice(self):
        lines = payments.checkout_line_items(self.invoice)
        self.assertEqual([(line['price_data']['product_data']['name'], line['price_data']['unit_amount']) for line in lines],
                         [('October 2026', 4500)])

    @override_settings(SITE_URL='https://club.example')
    def test_logo_shown_only_where_stripe_can_fetch_it(self):
        with mock.patch('organisations.branding.absolute_logo_url', return_value='https://club.example/media/logo.png'):
            lines = payments.checkout_line_items(self.invoice)
        self.assertEqual(lines[0]['price_data']['product_data']['images'], ['https://club.example/media/logo.png'])
        with mock.patch('organisations.branding.absolute_logo_url', return_value='http://localhost:8000/media/logo.png'):
            self.assertNotIn('images', payments.checkout_line_items(self.invoice)[0]['price_data']['product_data'])

    def test_checkout_gets_the_lines_and_a_note(self):
        self.configure(**CARD)
        token = payments.pay_token(self.kit_invoice)
        with mock.patch('stripe.checkout.Session.create', return_value=mock.Mock(url='https://checkout.stripe.test/s')) as create:
            self.client.post(reverse('invoice_pay_card', kwargs={'token': token}))
        kwargs = create.call_args.kwargs
        self.assertEqual(self.total(kwargs['line_items']), 9500)
        self.assertEqual(kwargs['custom_text']['submit']['message'],
                         f'You\'re paying Test Club for Ann: Autumn 2026 (reference INV{self.kit_invoice.pk}). '
                         'Questions? Email hello@testclub.example.')
