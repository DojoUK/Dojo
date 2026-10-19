from datetime import date, timedelta
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.contrib.auth.models import User
from django.core import mail
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from billing import payments, schedule
from billing.models import (
    AutoPay,
    BillingPolicy,
    Invoice,
    InvoiceRun,
    InvoiceSchedule,
    OrgTerm,
    PaymentSettings,
)
from billing.payment_forms import apply_post
from billing.test_payments import CARD, form
from classes.models import Class, ClassMember
from members.models import Member
from organisations.models import Organisation, OrganisationMember

TODAY = date(2026, 11, 3)


@override_settings(STRIPE_PUBLIC_KEY='', STRIPE_SECRET_KEY='', STRIPE_WEBHOOK_SECRET='')
class ScheduleTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organisation.objects.create(name='Forest Judo', slug='forest', email='hello@forest.example',
                                              features={'member_portal': True})
        cls.admin = User.objects.create_user('admin', password='pw', email='admin@forest.example')
        OrganisationMember.objects.create(user=cls.admin, organisation=cls.org, role='org_admin')
        cls.monthly = BillingPolicy.objects.create(organisation=cls.org, name='Adults', billing_cycle='monthly', amount=45)
        cls.termly = BillingPolicy.objects.create(organisation=cls.org, name='Kids', billing_cycle='termly', amount=60)
        cls.ann = Member.objects.create(organisation=cls.org, name='Ann Lee', email='ann@example.com', billing_policy=cls.monthly)
        cls.bo = Member.objects.create(organisation=cls.org, name='Bo Day', email='bo@example.com')  # billed via her class
        juniors = Class.objects.create(organisation=cls.org, name='Juniors', billing_policy=cls.monthly)
        ClassMember.objects.create(member=cls.bo, assigned_class=juniors)
        Member.objects.create(organisation=cls.org, name='Left', email='left@example.com', billing_policy=cls.monthly,
                              is_active=False)

    def plan(self, policy, **fields):
        return InvoiceSchedule.objects.create(policy=policy, enabled=True, **{'send_mode': 'send', **fields})

    def make_due(self, today=TODAY):
        with self.captureOnCommitCallbacks(execute=True):
            return schedule.create_due_runs(today)


class PeriodTests(ScheduleTestCase):
    def test_monthly_on_its_day(self):
        plan = self.plan(self.monthly, day_of_month=5)
        self.assertEqual(schedule.due_periods(plan, date(2026, 11, 4)), [])
        self.assertEqual(schedule.due_periods(plan, date(2026, 11, 5))[0][:2], ('November 2026', date(2026, 11, 5)))

    def test_termly_days_before_each_term(self):
        OrgTerm.objects.create(organisation=self.org, name='Spring Term 2027', start_date=date(2027, 1, 6), end_date=date(2027, 3, 28))
        plan = self.plan(self.termly, days_before_term=7)
        self.assertEqual(schedule.due_periods(plan, date(2026, 12, 29)), [])
        self.assertEqual(schedule.due_periods(plan, date(2026, 12, 30))[0][0], 'Spring Term 2027')
        self.assertEqual(schedule.next_run(plan, date(2026, 12, 1)), (date(2026, 12, 30), 'Spring Term 2027'))

    def test_annual(self):
        yearly = BillingPolicy.objects.create(organisation=self.org, name='Licence', billing_cycle='annual', amount=30)
        plan = self.plan(yearly, annual_month=9, annual_day=1)
        self.assertEqual(schedule.due_periods(plan, date(2026, 9, 1))[0][0], 'Licence 2026')
        self.assertEqual(schedule.next_run(plan, date(2026, 10, 1)), (date(2027, 9, 1), 'Licence 2027'))


class RunTests(ScheduleTestCase):
    def test_makes_each_members_invoice_once(self):
        self.plan(self.monthly, due_after_days=14)
        self.assertEqual(self.make_due(), 1)
        invoices = Invoice.objects.order_by('member__name')
        self.assertEqual([i.member.name for i in invoices], ['Ann Lee', 'Bo Day'])  # not the subscriber or the leaver
        self.assertEqual({(i.period, i.amount, i.due_date) for i in invoices},
                         {('November 2026', Decimal('45.00'), date(2026, 11, 15))})
        run = InvoiceRun.objects.get()
        self.assertEqual((run.invoice_count, run.total, run.status), (2, Decimal('90.00'), 'sent'))
        self.assertEqual(self.make_due(), 0)
        self.assertEqual(self.make_due(TODAY + timedelta(days=5)), 0)
        self.assertEqual(Invoice.objects.count(), 2)

    def test_members_already_invoiced_for_the_period_are_skipped(self):
        Invoice.objects.create(organisation=self.org, member=self.ann, amount=45, period='November 2026', due_date=TODAY)
        self.plan(self.monthly)
        self.make_due()
        self.assertEqual(Invoice.objects.filter(member=self.ann).count(), 1)

    def test_switching_on_mid_month_waits_for_the_next_date(self):
        self.plan(self.monthly, day_of_month=1, starts_from=TODAY)
        self.assertEqual(self.make_due(), 0)
        self.assertEqual(self.make_due(date(2026, 12, 1)), 1)
        self.assertEqual(InvoiceRun.objects.get().period, 'December 2026')

    def test_send_mode_emails_members_straight_away(self):
        self.plan(self.monthly)
        self.make_due()
        self.assertEqual(sorted(m.to[0] for m in mail.outbox), ['ann@example.com', 'bo@example.com'])

    def test_switched_off_and_custom_plans_are_left_alone(self):
        InvoiceSchedule.objects.create(policy=self.monthly, enabled=False)
        custom = BillingPolicy.objects.create(organisation=self.org, name='Odd', billing_cycle='custom', amount=5)
        InvoiceSchedule.objects.create(policy=custom, enabled=True)
        self.assertEqual(self.make_due(), 0)


class ReviewTests(ScheduleTestCase):
    def setUp(self):
        payment_settings = PaymentSettings(organisation=self.org)
        apply_post(payment_settings, {**CARD, **form(auto_pay='optional')})
        payment_settings.save()
        AutoPay.objects.create(member=self.ann, payment_method_id='pm_1', method_type='card', label='Visa ending 4242')
        self.plan(self.monthly, send_mode='review')
        self.make_due()
        self.held = InvoiceRun.objects.get()
        self.ann_invoice = Invoice.objects.get(member=self.ann)

    def test_held_invoices_are_invisible_to_members_until_sent(self):
        self.assertEqual(self.held.status, 'awaiting_review')
        self.assertEqual([m.to for m in mail.outbox], [['admin@forest.example']])  # only the admin hears
        self.assertIn('2 invoices for Adults, November 2026', mail.outbox[0].body)
        self.assertEqual(self.ann_invoice.autopay_status, '')  # not charged
        portal = self.client.get(reverse('member_portal', kwargs={'token': self.ann.token}))
        self.assertNotContains(portal, 'November 2026')
        pay_page = self.client.get(reverse('invoice_pay', kwargs={'token': payments.pay_token(self.ann_invoice)}))
        self.assertEqual(pay_page.status_code, 404)

    def test_send_releases_them(self):
        self.client.force_login(self.admin)
        self.client.post(reverse('invoice_run', kwargs={'org_slug': 'forest', 'pk': self.held.pk}), {'action': 'send'})
        self.held.refresh_from_db()
        self.ann_invoice.refresh_from_db()
        self.assertEqual(self.held.status, 'sent')
        self.assertEqual(self.ann_invoice.autopay_status, 'due')  # queued for her saved card
        self.assertEqual(sorted(m.to[0] for m in mail.outbox[1:]), ['ann@example.com', 'bo@example.com'])
        self.assertContains(self.client.get(reverse('member_portal', kwargs={'token': self.ann.token})), 'November 2026')

    def test_cancel_deletes_them_and_they_are_not_made_again(self):
        self.client.force_login(self.admin)
        self.client.post(reverse('invoice_run', kwargs={'org_slug': 'forest', 'pk': self.held.pk}), {'action': 'cancel'})
        self.assertFalse(Invoice.objects.exists())
        self.assertEqual(self.make_due(), 0)
        self.assertEqual(len(mail.outbox), 1)

    def test_a_held_invoice_cannot_be_emailed_on_its_own(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse('invoice_send_email', kwargs={'org_slug': 'forest', 'pk': self.ann_invoice.pk}), follow=True)
        self.assertContains(response, 'waiting to be checked')
        self.assertEqual(len(mail.outbox), 1)


class ReminderTests(ScheduleTestCase):
    def overdue(self, member, days, **fields):
        return Invoice.objects.create(organisation=self.org, member=member, amount=45, period='October 2026',
                                      due_date=TODAY - timedelta(days=days), **fields)

    def switch_on(self, **options):
        self.org.settings = {**self.org.settings, 'overdue_reminders': {'enabled': True, **options}}
        self.org.save()

    def test_off_unless_switched_on(self):
        self.overdue(self.ann, 30)
        self.assertEqual(schedule.send_due_reminders(TODAY), 0)

    def test_after_the_chosen_days_then_every_so_often(self):
        self.switch_on(after_days=7, repeat_days=14)
        self.overdue(self.ann, 3)
        recent = self.overdue(self.bo, 10)
        self.assertEqual(schedule.send_due_reminders(TODAY), 1)
        self.assertEqual(mail.outbox[-1].to, ['bo@example.com'])
        self.assertEqual(schedule.send_due_reminders(TODAY), 0)  # not again so soon
        Invoice.objects.filter(pk=recent.pk).update(reminder_sent_at=timezone.now() - timedelta(days=15))
        self.assertEqual(schedule.send_due_reminders(TODAY), 1)

    def test_held_and_automatically_paid_invoices_are_not_chased(self):
        self.switch_on(after_days=1)
        held_run = InvoiceRun.objects.create(schedule=InvoiceSchedule.objects.create(policy=self.monthly),
                                             period='October 2026', status='awaiting_review')
        self.overdue(self.ann, 10, run=held_run)
        self.overdue(self.bo, 10, autopay_status='processing')
        self.assertEqual(schedule.send_due_reminders(TODAY), 0)


class PageTests(ScheduleTestCase):
    def setUp(self):
        self.client.force_login(self.admin)
        self.url = reverse('billing_automatic', kwargs={'org_slug': 'forest'})

    def test_switching_on_sets_the_start_and_shows_the_next_run(self):
        self.client.post(self.url, {'policy': self.monthly.pk, 'enabled': '1', 'day_of_month': '1',
                                    'due_after_days': '14', 'send_mode': 'review'})
        plan = InvoiceSchedule.objects.get(policy=self.monthly)
        self.assertTrue(plan.enabled)
        self.assertEqual(plan.starts_from, timezone.localdate())
        self.assertContains(self.client.get(self.url), 'About 2 invoices (£90.00)')

    def test_reminder_settings(self):
        self.client.post(self.url, {'action': 'reminders', 'enabled': '1', 'after_days': '5', 'repeat_days': '10'})
        self.org.refresh_from_db()
        self.assertEqual(self.org.settings['overdue_reminders'], {'enabled': True, 'after_days': 5, 'repeat_days': 10})

    def test_another_clubs_plans_and_runs_are_out_of_reach(self):
        other = Organisation.objects.create(name='Other', slug='other')
        their_plan = BillingPolicy.objects.create(organisation=other, name='Theirs', billing_cycle='monthly', amount=1)
        self.assertEqual(self.client.post(self.url, {'policy': their_plan.pk, 'enabled': '1'}).status_code, 404)
        their_run = InvoiceRun.objects.create(schedule=InvoiceSchedule.objects.create(policy=their_plan),
                                              period='x', status='awaiting_review')
        url = reverse('invoice_run', kwargs={'org_slug': 'forest', 'pk': their_run.pk})
        self.assertEqual(self.client.post(url, {'action': 'send'}).status_code, 404)

    def test_coaches_without_billing_permission_are_refused(self):
        coach = User.objects.create_user('coach', password='pw')
        OrganisationMember.objects.create(user=coach, organisation=self.org, role='coach')
        self.client.force_login(coach)
        self.assertIn(self.client.get(self.url).status_code, (302, 403))


class SchedulerJobTests(ScheduleTestCase):
    def test_the_scheduler_makes_due_invoices(self):
        self.plan(self.monthly, day_of_month=1)
        with mock.patch('django.utils.timezone.localdate', return_value=TODAY), self.captureOnCommitCallbacks(execute=True):
            call_command('run_scheduler', '--once', stdout=StringIO())
        self.assertEqual(Invoice.objects.count(), 2)
