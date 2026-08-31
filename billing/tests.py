from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from members.models import Member
from organisations.models import Organisation

from .models import Invoice, Payment
from .stripe_views import StripeWebhookView


def stripe_invoice(invoice_id='in_test123', amount_paid=2500, subscription='sub_test123',
                   shape='current'):
    """Build a stand-in for a Stripe Invoice object.

    `shape='current'` nests the subscription under parent.subscription_details, which is where
    Stripe moved it. `shape='legacy'` uses the flat field that endpoints pinned to an older API
    version still send. Both must work.
    """
    obj = SimpleNamespace(
        id=invoice_id,
        amount_paid=amount_paid,
        period_end=None,
        payment_intent='pi_test123',
        parent=None,
        subscription=None,
    )
    if shape == 'current':
        obj.parent = SimpleNamespace(
            subscription_details=SimpleNamespace(subscription=subscription)
        )
    else:
        obj.subscription = subscription
    return obj


class SubscriptionInvoiceWebhookTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name='Test Club', slug='test-club')
        self.member = Member.objects.create(
            organisation=self.org, name='A Member',
            joined_date=timezone.localdate(), stripe_subscription_id='sub_test123',
        )
        self.view = StripeWebhookView()

    def test_records_payment_from_current_payload_shape(self):
        self.view._handle_subscription_invoice_paid(stripe_invoice())

        invoice = Invoice.objects.get()
        self.assertEqual(invoice.member, self.member)
        self.assertEqual(invoice.amount, 25)
        self.assertEqual(invoice.status, Invoice.Status.PAID)
        self.assertEqual(invoice.stripe_invoice_id, 'in_test123')
        self.assertEqual(Payment.objects.count(), 1)

    def test_records_payment_from_legacy_payload_shape(self):
        self.view._handle_subscription_invoice_paid(stripe_invoice(shape='legacy'))
        self.assertEqual(Invoice.objects.count(), 1)

    def test_payment_method_is_a_valid_choice(self):
        """'Stripe (subscription)' overflowed Payment.method (max_length=10) and raised
        DataError, so the payment was never recorded and Stripe retried forever."""
        self.view._handle_subscription_invoice_paid(stripe_invoice())

        payment = Payment.objects.get()
        self.assertEqual(payment.method, Payment.Method.STRIPE)
        self.assertEqual(payment.get_method_display(), 'Stripe')
        self.assertIn('in_test123', payment.notes)

    def test_redelivery_does_not_duplicate(self):
        """Stripe redelivers events; each delivery used to create another invoice."""
        for _ in range(3):
            self.view._handle_subscription_invoice_paid(stripe_invoice())

        self.assertEqual(Invoice.objects.count(), 1)
        self.assertEqual(Payment.objects.count(), 1)

    def test_ignores_non_subscription_invoice(self):
        self.view._handle_subscription_invoice_paid(stripe_invoice(subscription=None))
        self.assertEqual(Invoice.objects.count(), 0)

    def test_ignores_unknown_subscription(self):
        self.view._handle_subscription_invoice_paid(stripe_invoice(subscription='sub_nobody'))
        self.assertEqual(Invoice.objects.count(), 0)

    def test_accepts_expanded_subscription_object(self):
        expanded = stripe_invoice(subscription=SimpleNamespace(id='sub_test123'))
        self.view._handle_subscription_invoice_paid(expanded)
        self.assertEqual(Invoice.objects.count(), 1)


class CheckoutCompletedWebhookTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name='Test Club', slug='test-club')
        self.member = Member.objects.create(
            organisation=self.org, name='A Member', joined_date=timezone.localdate(),
        )
        self.invoice = Invoice.objects.create(
            member=self.member, organisation=self.org, period='September 2026',
            amount=25, due_date=timezone.localdate(),
        )
        self.view = StripeWebhookView()

    def _session(self):
        return SimpleNamespace(
            id='cs_test123',
            metadata={'invoice_pk': str(self.invoice.pk)},
            payment_intent='pi_test123',
            mode='payment',
            subscription=None,
        )

    def test_marks_invoice_paid_with_valid_method(self):
        self.view._handle_checkout_completed(self._session())

        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, Invoice.Status.PAID)

        payment = Payment.objects.get()
        self.assertEqual(payment.method, Payment.Method.STRIPE)
        self.assertEqual(payment.get_method_display(), 'Stripe')

    def test_redelivery_does_not_duplicate_payment(self):
        for _ in range(3):
            self.view._handle_checkout_completed(self._session())
        self.assertEqual(Payment.objects.count(), 1)


@override_settings(STRIPE_SECRET_KEY='sk_test_x', STRIPE_WEBHOOK_SECRET='whsec_x')
class WebhookEndpointTests(TestCase):
    def test_rejects_bad_signature(self):
        with patch('stripe.Webhook.construct_event', side_effect=ValueError):
            response = self.client.post(
                reverse('stripe_webhook'), data='{}', content_type='application/json',
            )
        self.assertEqual(response.status_code, 400)

    @override_settings(STRIPE_SECRET_KEY='')
    def test_rejects_when_stripe_not_configured(self):
        response = self.client.post(
            reverse('stripe_webhook'), data='{}', content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
