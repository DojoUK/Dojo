"""
Overpayment credit, the Partial badge and the billing list order. Every
expected figure is worked out by hand in the test, not by the code.
"""
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from billing import schedule
from billing.credit import apply_member_credit, member_credit
from billing.models import AutoPay, BillingPolicy, Invoice, InvoiceSchedule, Payment, PaymentSettings
from billing.payment_forms import apply_post
from billing.test_payments import CARD, form
from members.models import Member
from organisations.features import FEATURES_BY_KEY
from organisations.models import Organisation, OrganisationMember

ALL_ON = {key: True for key in FEATURES_BY_KEY}


class CreditTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organisation.objects.create(name='Test Club', slug='test-club', features=dict(ALL_ON))
        cls.admin = User.objects.create_user('admin', password='pw')
        OrganisationMember.objects.create(user=cls.admin, organisation=cls.org, role='org_admin')
        cls.ann = Member.objects.create(organisation=cls.org, name='Ann Able', email='ann@example.com')
        cls.bob = Member.objects.create(organisation=cls.org, name='Bob Best', email='bob@example.com')

    def setUp(self):
        self.client.force_login(self.admin)

    def invoice(self, member, amount, due=date(2026, 1, 31), paid=None, status='unpaid', **extra):
        invoice = Invoice.objects.create(
            organisation=member.organisation, member=member, amount=Decimal(amount), period='Jan',
            due_date=due, status=status, **extra,
        )
        if paid is not None:
            Payment.objects.create(invoice=invoice, amount=Decimal(paid), paid_at=timezone.now())
            invoice.status = 'paid' if Decimal(paid) >= Decimal(amount) else invoice.status
            invoice.save(update_fields=['status'])
        return invoice

    def url(self, name, **kwargs):
        return reverse(name, kwargs={'org_slug': self.org.slug, **kwargs})


class CreditMathsTests(CreditTestBase):
    def test_overpaid_amount_and_available_credit(self):
        source = self.invoice(self.ann, '40.00', paid='50.00')
        self.assertEqual(source.overpaid_amount, Decimal('10.00'))
        self.assertEqual(source.available_credit, Decimal('10.00'))
        self.assertFalse(source.is_partial)

    def test_credit_comes_off_the_next_invoice(self):
        source = self.invoice(self.ann, '40.00', paid='50.00')
        new = self.invoice(self.ann, '30.00', due=date(2026, 2, 28))
        self.assertEqual(apply_member_credit(new), Decimal('10.00'))
        new.refresh_from_db()
        source.refresh_from_db()
        self.assertEqual(new.amount, Decimal('20.00'))          # 30 - 10
        self.assertEqual(new.discount_amount, Decimal('10.00'))
        self.assertEqual(new.status, 'unpaid')
        self.assertIn('£10.00 credit', new.notes)
        self.assertEqual(source.overpayment_credited, Decimal('10.00'))
        self.assertEqual(source.available_credit, Decimal('0'))

    def test_credit_is_used_only_once(self):
        self.invoice(self.ann, '40.00', paid='50.00')
        first = self.invoice(self.ann, '30.00', due=date(2026, 2, 28))
        apply_member_credit(first)
        second = self.invoice(self.ann, '30.00', due=date(2026, 3, 31))
        self.assertEqual(apply_member_credit(second), Decimal('0'))
        second.refresh_from_db()
        self.assertEqual(second.amount, Decimal('30.00'))
        self.assertEqual(second.notes, '')

    def test_credit_bigger_than_the_invoice_pays_it_and_carries_the_rest(self):
        source = self.invoice(self.ann, '40.00', paid='100.00')     # 60 credit
        covered = self.invoice(self.ann, '45.00', due=date(2026, 2, 28))
        apply_member_credit(covered)
        covered.refresh_from_db()
        self.assertEqual(covered.amount, Decimal('0.00'))
        self.assertEqual(covered.status, 'paid')
        self.assertEqual(covered.balance_due, Decimal('0'))
        source.refresh_from_db()
        self.assertEqual(source.available_credit, Decimal('15.00'))   # 60 - 45
        later = self.invoice(self.ann, '30.00', due=date(2026, 3, 31))
        apply_member_credit(later)
        later.refresh_from_db()
        self.assertEqual(later.amount, Decimal('15.00'))              # 30 - 15

    def test_oldest_overpayment_is_used_first(self):
        january = self.invoice(self.ann, '20.00', due=date(2026, 1, 31), paid='25.00')   # 5 credit
        february = self.invoice(self.ann, '20.00', due=date(2026, 2, 28), paid='27.00')  # 7 credit
        new = self.invoice(self.ann, '10.00', due=date(2026, 3, 31))
        self.assertEqual(apply_member_credit(new), Decimal('10.00'))
        january.refresh_from_db()
        february.refresh_from_db()
        self.assertEqual(january.overpayment_credited, Decimal('5.00'))
        self.assertEqual(february.overpayment_credited, Decimal('5.00'))
        self.assertEqual(february.available_credit, Decimal('2.00'))
        self.assertEqual(member_credit(self.ann), Decimal('2.00'))

    def test_stops_taking_once_the_invoice_is_covered(self):
        january = self.invoice(self.ann, '20.00', due=date(2026, 1, 31), paid='30.00')   # 10 credit
        february = self.invoice(self.ann, '20.00', due=date(2026, 2, 28), paid='30.00')  # 10 credit
        new = self.invoice(self.ann, '6.00', due=date(2026, 3, 31))
        self.assertEqual(apply_member_credit(new), Decimal('6.00'))
        january.refresh_from_db()
        february.refresh_from_db()
        self.assertEqual(january.overpayment_credited, Decimal('6.00'))
        self.assertEqual(february.overpayment_credited, Decimal('0.00'))

    def test_nothing_owed_takes_no_credit(self):
        self.invoice(self.ann, '40.00', paid='50.00')
        new = self.invoice(self.ann, '0.00', due=date(2026, 3, 31))
        self.assertEqual(apply_member_credit(new), Decimal('0'))
        self.assertEqual(member_credit(self.ann), Decimal('10.00'))

    def test_another_members_credit_is_never_used(self):
        self.invoice(self.bob, '40.00', paid='50.00')
        new = self.invoice(self.ann, '30.00')
        self.assertEqual(apply_member_credit(new), Decimal('0'))

    def test_mark_paid_without_money_is_not_credit(self):
        self.invoice(self.ann, '40.00', status='paid')
        self.assertEqual(member_credit(self.ann), Decimal('0'))

    def test_partial_payment(self):
        invoice = self.invoice(self.ann, '40.00', paid='10.00')
        self.assertTrue(invoice.is_partial)
        self.assertEqual(invoice.overpaid_amount, Decimal('0'))
        self.assertEqual(invoice.balance_due, Decimal('30.00'))

    def test_form_string_amounts_are_handled(self):
        """The member list bulk action passes the amount as a raw form string."""
        self.invoice(self.ann, '40.00', paid='42.50')
        new = Invoice.objects.create(organisation=self.org, member=self.ann, amount='12.5', period='Feb',
                                     due_date=date(2026, 2, 28))
        apply_member_credit(new)
        new.refresh_from_db()
        self.assertEqual(new.amount, Decimal('10.00'))


class CreditOnEveryCreatePathTests(CreditTestBase):
    def setUp(self):
        super().setUp()
        self.source = self.invoice(self.ann, '40.00', paid='50.00')   # £10 credit

    def latest(self):
        return Invoice.objects.filter(member=self.ann).exclude(pk=self.source.pk).get()

    def test_single_invoice_form(self):
        self.client.post(self.url('invoice_create'), {
            'target': 'one', 'member_id': self.ann.pk, 'amount': '30.00', 'period': 'Feb',
            'due_date': '2026-02-28',
        })
        self.assertEqual(self.latest().amount, Decimal('20.00'))

    def test_bulk_invoices(self):
        self.ann.monthly_fee = Decimal('30.00')
        self.ann.save()
        self.client.post(self.url('invoice_bulk'), {
            'period': 'February 2026', 'due_date': '2026-02-28', 'member_ids': [self.ann.pk],
        })
        self.assertEqual(self.latest().amount, Decimal('20.00'))

    def test_member_list_bulk_action(self):
        self.client.post(self.url('member_bulk_action'), {
            'bulk_action': 'invoice_create', 'member_ids': [self.ann.pk], 'amount': '30.00', 'period': 'Feb',
            'due_date': '2026-02-28',
        })
        self.assertEqual(self.latest().amount, Decimal('20.00'))


@override_settings(STRIPE_PUBLIC_KEY='', STRIPE_SECRET_KEY='', STRIPE_WEBHOOK_SECRET='')
class CreditOnAutomaticInvoicesTests(CreditTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.policy = BillingPolicy.objects.create(organisation=cls.org, name='Adults', billing_cycle='monthly', amount=45)
        cls.ann.billing_policy = cls.policy
        cls.ann.save()

    def run_plan(self):
        InvoiceSchedule.objects.create(policy=self.policy, enabled=True, send_mode='send', day_of_month=5)
        with self.captureOnCommitCallbacks(execute=True):
            schedule.create_due_runs(date(2026, 11, 5))
        return Invoice.objects.filter(member=self.ann, run__isnull=False).get()

    def test_run_takes_the_credit_off_and_totals_what_is_owed(self):
        self.invoice(self.ann, '40.00', paid='50.00')
        invoice = self.run_plan()
        self.assertEqual(invoice.amount, Decimal('35.00'))       # 45 - 10
        self.assertEqual(invoice.run.total, Decimal('35.00'))

    def held_run(self):
        InvoiceSchedule.objects.create(policy=self.policy, enabled=True, send_mode='review', day_of_month=5)
        with self.captureOnCommitCallbacks(execute=True):
            schedule.create_due_runs(date(2026, 11, 5))
        invoice = Invoice.objects.filter(member=self.ann, run__isnull=False).get()
        return invoice.run, invoice

    def test_held_run_takes_the_credit_only_when_sent(self):
        self.invoice(self.ann, '40.00', paid='50.00')            # 10 credit
        run, invoice = self.held_run()
        self.assertEqual(invoice.amount, Decimal('45.00'))
        self.assertEqual(member_credit(self.ann), Decimal('10.00'))
        with self.captureOnCommitCallbacks(execute=True):
            schedule.release(run)
        invoice.refresh_from_db()
        run.refresh_from_db()
        self.assertEqual(invoice.amount, Decimal('35.00'))
        self.assertEqual(run.total, Decimal('35.00'))
        self.assertEqual(member_credit(self.ann), Decimal('0'))

    def test_cancelling_a_held_run_keeps_the_credit(self):
        self.invoice(self.ann, '40.00', paid='100.00')           # 60 credit would cover 45
        run, invoice = self.held_run()
        schedule.cancel(run)
        self.assertFalse(Invoice.objects.filter(pk=invoice.pk).exists())
        self.assertEqual(member_credit(self.ann), Decimal('60.00'))
        self.assertFalse(run.invoices.exists())

    def test_held_invoice_covered_by_credit_is_paid_and_not_collected_when_sent(self):
        payment_settings = PaymentSettings(organisation=self.org)
        apply_post(payment_settings, {**CARD, **form(auto_pay='optional')})
        payment_settings.save()
        AutoPay.objects.create(member=self.ann, payment_method_id='pm_1', method_type='card', label='Visa')
        self.invoice(self.ann, '40.00', paid='100.00')
        run, invoice = self.held_run()
        with self.captureOnCommitCallbacks(execute=True):
            schedule.release(run)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, 'paid')
        self.assertEqual(invoice.autopay_status, '')
        self.assertEqual(member_credit(self.ann), Decimal('15.00'))

    def test_invoice_covered_by_credit_is_not_collected_automatically(self):
        payment_settings = PaymentSettings(organisation=self.org)
        apply_post(payment_settings, {**CARD, **form(auto_pay='optional')})
        payment_settings.save()
        AutoPay.objects.create(member=self.ann, payment_method_id='pm_1', method_type='card', label='Visa')
        self.invoice(self.ann, '40.00', paid='100.00')            # 60 credit covers 45
        invoice = self.run_plan()
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, 'paid')
        self.assertEqual(invoice.autopay_status, '')


class BillingListTests(CreditTestBase):
    def test_partial_badge_only_for_part_paid_invoices(self):
        self.invoice(self.ann, '40.00', paid='10.00')
        self.invoice(self.bob, '40.00', paid='40.00')
        html = self.client.get(self.url('invoice_list')).content.decode()
        self.assertEqual(html.count('>Partial</span>'), 1)
        self.assertIn('£10.00 of £40.00 received', html)

    def test_sorted_by_due_date_newest_first_then_name(self):
        self.invoice(self.bob, '10', due=date(2026, 3, 1))
        self.invoice(self.ann, '10', due=date(2026, 3, 1))
        self.invoice(self.ann, '10', due=date(2026, 5, 1))
        rows = [(i.due_date, i.member.name) for i in self.client.get(self.url('invoice_list')).context['invoices']]
        self.assertEqual(rows, [
            (date(2026, 5, 1), 'Ann Able'), (date(2026, 3, 1), 'Ann Able'), (date(2026, 3, 1), 'Bob Best'),
        ])

    def test_payment_form_starts_at_what_is_left_to_pay(self):
        invoice = self.invoice(self.ann, '30.00', paid='10.00')
        html = self.client.get(self.url('invoice_detail', pk=invoice.pk)).content.decode()
        self.assertRegex(html, r'name="amount"[^>]*value="20\.00"')

    def test_member_page_shows_unused_credit(self):
        self.invoice(self.ann, '40.00', paid='52.50')
        response = self.client.get(reverse('member_detail', kwargs={'org_slug': self.org.slug, 'pk': self.ann.pk}))
        self.assertContains(response, 'Overpaid £12.50')
        response = self.client.get(reverse('member_detail', kwargs={'org_slug': self.org.slug, 'pk': self.bob.pk}))
        self.assertNotContains(response, 'Overpaid')

    def test_due_dates_far_apart_do_not_matter_for_credit_order(self):
        """Credit order follows due date even when the later invoice was created first."""
        later = self.invoice(self.ann, '20.00', due=date.today() + timedelta(days=60), paid='23.00')
        earlier = self.invoice(self.ann, '20.00', due=date.today() - timedelta(days=60), paid='21.00')
        new = self.invoice(self.ann, '2.00')
        apply_member_credit(new)
        earlier.refresh_from_db()
        later.refresh_from_db()
        self.assertEqual(earlier.overpayment_credited, Decimal('1.00'))
        self.assertEqual(later.overpayment_credited, Decimal('1.00'))
