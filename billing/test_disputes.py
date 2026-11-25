"""Chargebacks: a member disputes a Stripe payment with their bank (charge.dispute.* webhooks)."""
import json
import time
from decimal import Decimal

from django.core import mail
from django.urls import reverse
from django.utils import timezone

from billing.models import Invoice, Payment, PaymentDispute
from billing.stripe_setup import WEBHOOK_EVENTS
from billing.test_payments import CARD, PaymentsTestCase, signed
from members.models import Member
from organisations.models import Organisation

DUE_BY = int(time.mktime((2026, 12, 9, 23, 59, 0, 0, 0, 0)))


class DisputeCase(PaymentsTestCase):
    def setUp(self):
        self.configure(**CARD)
        Payment.objects.create(invoice=self.invoice, method='stripe', stripe_payment_id='pi_1',
                               amount=Decimal('45.00'), paid_at=timezone.now())
        Invoice.objects.filter(pk=self.invoice.pk).update(status='paid', autopay_status='paid')

    def send(self, event_type, status='needs_response', payment_intent='pi_1', dispute_id='dp_1', **extra):
        dispute = {'id': dispute_id, 'object': 'dispute', 'amount': 4500, 'currency': 'gbp', 'charge': 'ch_1',
                   'payment_intent': payment_intent, 'reason': 'fraudulent', 'status': status, 'livemode': False,
                   'evidence_details': {'due_by': DUE_BY, 'has_evidence': False}, **extra}
        payload = json.dumps({'id': 'evt_1', 'object': 'event', 'type': event_type, 'data': {'object': dispute}})
        response = self.client.post(reverse('stripe_webhook_org', kwargs={'org_slug': 'test-club'}), payload,
                                    content_type='application/json', HTTP_STRIPE_SIGNATURE=signed(payload))
        self.assertEqual(response.status_code, 200)
        self.invoice.refresh_from_db()
        return PaymentDispute.objects.filter(stripe_dispute_id=dispute_id).first()


class DisputeWebhookTests(DisputeCase):
    def test_a_new_dispute_is_recorded_and_the_admins_told(self):
        dispute = self.send('charge.dispute.created')
        self.assertEqual((dispute.invoice, dispute.organisation, dispute.amount), (self.invoice, self.org, Decimal('45.00')))
        self.assertTrue(dispute.needs_response)
        self.assertEqual(dispute.evidence_due_by.date().isoformat(), '2026-12-09')
        self.assertEqual(dispute.stripe_url, 'https://dashboard.stripe.com/test/disputes/dp_1')
        self.assertEqual(self.invoice.status, 'paid')   # nothing changes on the invoice until the bank decides

        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertEqual(email.to, ['admin@testclub.example'])
        self.assertEqual(email.subject, 'Payment disputed: Ann Lee, £45.00')
        self.assertIn("Ann Lee has disputed a £45.00 payment with their bank (says they didn't make the payment)", email.body)
        self.assertIn('Respond by 9 December 2026 on Stripe', email.body)
        self.assertIn('https://dashboard.stripe.com/test/disputes/dp_1', email.body)
        self.assertIn(f'/billing/{self.invoice.pk}/', email.body)

    def test_updates_follow_it_without_more_emails(self):
        self.send('charge.dispute.created')
        dispute = self.send('charge.dispute.updated', status='under_review')
        self.assertEqual(dispute.status_text, 'With the bank')
        self.assertFalse(dispute.needs_response)
        self.assertTrue(dispute.is_open)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(PaymentDispute.objects.count(), 1)

    def test_won_leaves_the_invoice_paid(self):
        self.send('charge.dispute.created')
        dispute = self.send('charge.dispute.closed', status='won')
        self.assertIsNotNone(dispute.closed_at)
        self.assertFalse(dispute.is_open)
        self.assertEqual(self.invoice.status, 'paid')
        self.assertIsNone(dispute.chargeback_payment)

    def test_lost_puts_the_money_back_on_the_invoice(self):
        self.send('charge.dispute.created')
        dispute = self.send('charge.dispute.closed', status='lost')
        self.assertEqual((self.invoice.status, self.invoice.autopay_status), ('unpaid', ''))
        self.assertEqual(self.invoice.balance_due, Decimal('45.00'))
        self.assertEqual(dispute.chargeback_payment.amount, Decimal('-45.00'))
        self.assertIn('Chargeback lost', dispute.chargeback_payment.notes)
        self.send('charge.dispute.closed', status='lost')   # Stripe retries
        self.assertEqual(self.invoice.payments.count(), 2)

    def test_a_bank_enquiry_is_not_yet_a_chargeback(self):
        dispute = self.send('charge.dispute.created', status='warning_needs_response', reason='general')
        self.assertTrue(dispute.is_enquiry)
        self.assertEqual(mail.outbox[0].subject, 'A bank is asking about a £45.00 payment')
        self.assertIn('This is an enquiry, not yet a chargeback', mail.outbox[0].body)

    def test_disputes_on_payments_dojo_doesnt_know_are_still_shown(self):
        dispute = self.send('charge.dispute.created', payment_intent='pi_elsewhere', dispute_id='dp_2')
        self.assertIsNone(dispute.invoice)
        self.assertEqual(dispute.organisation, self.org)
        self.assertIn('A member has disputed', mail.outbox[0].body)

    def test_another_clubs_payment_is_not_matched(self):
        other = Organisation.objects.create(name='Other Club', slug='other-club')
        member = Member.objects.create(organisation=other, name='Bo')
        invoice = Invoice.objects.create(organisation=other, member=member, amount=Decimal('10'), period='x',
                                         due_date=self.invoice.due_date, status='paid')
        Payment.objects.create(invoice=invoice, method='stripe', stripe_payment_id='pi_other', amount=Decimal('10'),
                               paid_at=timezone.now())
        dispute = self.send('charge.dispute.created', payment_intent='pi_other', dispute_id='dp_3')
        self.assertIsNone(dispute.invoice)

    def test_the_webhook_asks_for_dispute_events(self):
        for event in ('charge.dispute.created', 'charge.dispute.updated', 'charge.dispute.closed'):
            self.assertIn(event, WEBHOOK_EVENTS)


class DisputePagesTests(DisputeCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)

    def test_open_disputes_are_on_the_dashboard(self):
        self.send('charge.dispute.created')
        self.org.settings = {**(self.org.settings or {}), 'dashboard': {'version': 2, 'widgets': [
            {'key': 'payments_attention', 'x': 0, 'y': 0, 'w': 12, 'h': 7},
            {'key': 'needs_attention', 'x': 12, 'y': 0, 'w': 12, 'h': 7}]}}
        self.org.save()
        page = self.client.get(reverse('org_dashboard', kwargs={'org_slug': self.org.slug}))
        self.assertEqual(len(page.context['disputes']), 1)
        self.assertEqual(page.context['disputes_to_answer'], 1)
        self.assertContains(page, 'Disputed')
        self.assertContains(page, 'Respond by 9 Dec')
        self.assertContains(page, '1 disputed payment</strong> needs a response')

        self.send('charge.dispute.closed', status='won')
        page = self.client.get(reverse('org_dashboard', kwargs={'org_slug': self.org.slug}))
        self.assertEqual(page.context['disputes'], [])
        self.assertEqual(page.context['disputes_to_answer'], 0)

    def test_the_invoice_shows_the_dispute(self):
        self.send('charge.dispute.created')
        url = reverse('invoice_detail', kwargs={'org_slug': self.org.slug, 'pk': self.invoice.pk})
        page = self.client.get(url)
        self.assertContains(page, 'Needs a response')
        self.assertContains(page, 'Respond on Stripe')
        self.send('charge.dispute.closed', status='lost')
        page = self.client.get(url)
        self.assertContains(page, 'this invoice is due again')
        self.assertContains(page, 'See it on Stripe')
