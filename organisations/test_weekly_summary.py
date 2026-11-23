from datetime import datetime, timedelta

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from classes.models import Attendance, Class, Session
from members.models import Member
from organisations import weekly_summary
from organisations.features import FEATURES_BY_KEY
from organisations.models import Organisation, OrganisationMember

ALL_ON = {key: True for key in FEATURES_BY_KEY}


class WeeklySummaryTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name='Test Club', slug='test-club', features=dict(ALL_ON),
                                               settings={'two_factor': 'optional'})
        self.admin = User.objects.create_user('admin', email='admin@example.com', password='pw', first_name='Sam')
        self.membership = OrganisationMember.objects.create(user=self.admin, organisation=self.org, role='org_admin',
                                                            weekly_summary=True)
        # A Monday at 8am, and a session last Tuesday with one person there.
        today = timezone.localdate()
        self.monday = today - timedelta(days=today.weekday())
        cls = Class.objects.create(organisation=self.org, name='Adults', is_active=True)
        member = Member.objects.create(organisation=self.org, name='Ann Lee', email='ann@example.com')
        session = Session.objects.create(assigned_class=cls, date=self.monday - timedelta(days=6))
        Attendance.objects.create(session=session, member=member, present=True)
        Member.objects.create(organisation=self.org, name='Gone Away', email='gone@example.com',
                              joined_date=self.monday - timedelta(days=100))

    def at(self, day, hour):
        return timezone.make_aware(datetime.combine(day, datetime.min.time()).replace(hour=hour))

    def test_sent_once_a_week_from_monday_morning(self):
        self.assertEqual(weekly_summary.send_due(self.at(self.monday, 6)), 0)
        self.assertEqual(weekly_summary.send_due(self.at(self.monday, 8)), 1)
        [message] = mail.outbox
        self.assertEqual(message.to, ['admin@example.com'])
        self.assertIn('Your week at Test Club', message.subject)
        self.assertIn('Attendances: 1', message.body)
        self.assertIn('never come: 1', message.body)
        self.assertEqual(message.attachments[0][0], 'not-seen-lately.csv')
        self.assertIn('Never come,Gone Away', message.attachments[0][1])
        self.assertEqual(weekly_summary.send_due(self.at(self.monday + timedelta(days=2), 9)), 0)
        self.assertEqual(weekly_summary.send_due(self.at(self.monday + timedelta(days=7), 8)), 1)

    def test_only_admins_who_switch_it_on(self):
        self.membership.weekly_summary = False
        self.membership.save()
        coach = User.objects.create_user('coach', email='coach@example.com', password='pw')
        OrganisationMember.objects.create(user=coach, organisation=self.org, role='coach', weekly_summary=True)
        self.assertEqual(weekly_summary.send_due(self.at(self.monday, 8)), 0)

    def test_switch_in_my_settings(self):
        self.membership.weekly_summary = False
        self.membership.save()
        self.client.force_login(self.admin)
        url = reverse('account_settings', kwargs={'org_slug': self.org.slug})
        self.assertContains(self.client.get(url), 'Weekly club summary')
        self.client.post(url, {'action': 'update_emails', 'weekly_summary': '1'})
        self.membership.refresh_from_db()
        self.assertTrue(self.membership.weekly_summary)

    def test_scheduled(self):
        from jobs.registry import JOBS
        names = [name for name, *_ in JOBS]
        self.assertIn('send_weekly_summaries', names)
        self.assertIn('send_review_prompts', names)
