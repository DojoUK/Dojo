from datetime import datetime, time, timedelta

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from classes import booking, reminders
from classes.models import Class, ClassMember, Session, SessionAbsence, SessionBooking, SessionReminder
from members.models import Member
from organisations.features import FEATURES_BY_KEY
from organisations.models import Organisation, OrganisationMember

ALL_ON = {key: True for key in FEATURES_BY_KEY}


class ReminderTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name='Test Club', slug='test-club', features=dict(ALL_ON))
        day = timezone.localdate() + timedelta(days=3)
        self.cls = Class.objects.create(organisation=self.org, name='Juniors', is_active=True,
                                        schedule=[{'day': day.weekday(), 'time': '18:00', 'end': '19:00'}])
        self.session = Session.objects.create(assigned_class=self.cls, date=day)
        self.start = timezone.make_aware(datetime.combine(day, time(18, 0)))
        self.alice = Member.objects.create(organisation=self.org, name='Alice Able', email='alice@example.com')
        self.bob = Member.objects.create(organisation=self.org, name='Bob Best', email='bob@example.com')
        self.carol = Member.objects.create(organisation=self.org, name='Carol Cole', email='carol@example.com')

    def book(self, member, hours_before=48, **kwargs):
        made = SessionBooking.objects.create(session=self.session, member=member, **kwargs)
        SessionBooking.objects.filter(pk=made.pk).update(created_at=self.start - timedelta(hours=hours_before))
        return made

    def run_at(self, hours_before):
        mail.outbox.clear()
        return reminders.send_due(now=self.start - timedelta(hours=hours_before))

    def test_booked_members_get_one_reminder_with_the_time_and_a_portal_link(self):
        self.book(self.alice)
        self.assertEqual(self.run_at(30), 0)                 # not yet: reminders go 24 hours before
        self.assertEqual(self.run_at(20), 1)
        [message] = mail.outbox
        self.assertEqual(message.to, ['alice@example.com'])
        self.assertIn('Reminder: Juniors on', message.subject)
        self.assertIn('18:00–19:00', message.body)
        self.assertIn(f'/p/{self.alice.token}/', message.body)
        self.assertEqual(self.run_at(10), 0)                 # only once
        self.assertEqual(SessionReminder.objects.get().sent_to, 'alice@example.com')

    def test_who_is_reminded(self):
        self.book(self.alice)
        self.book(self.bob, hours_before=2)                  # booked inside the window: just had a confirmation
        self.book(self.carol, status=SessionBooking.Status.PENDING)   # not paid for
        enrolled = Member.objects.create(organisation=self.org, name='Dan Dee', email='dan@example.com')
        ClassMember.objects.create(assigned_class=self.cls, member=enrolled, auto_enrol=True)
        self.run_at(1)
        self.assertEqual([m.to for m in mail.outbox], [['alice@example.com']])

    def test_everyone_expected_includes_enrolled_members_who_havent_said_no(self):
        self.org.settings = {'session_reminder_scope': 'everyone'}
        self.org.save()
        enrolled = Member.objects.create(organisation=self.org, name='Dan Dee', email='dan@example.com')
        away = Member.objects.create(organisation=self.org, name='Eve Ell', email='eve@example.com')
        for m in (enrolled, away):
            ClassMember.objects.create(assigned_class=self.cls, member=m, auto_enrol=True)
        SessionAbsence.objects.create(session=self.session, member=away)
        self.run_at(5)
        self.assertEqual(sorted(m.to[0] for m in mail.outbox), ['dan@example.com'])

    def test_nobody_when_switched_off_opted_out_cancelled_or_inactive(self):
        self.book(self.alice)
        self.alice.session_reminders = False
        self.alice.save()
        self.assertEqual(self.run_at(5), 0)
        self.alice.session_reminders = True
        self.alice.save()
        self.org.settings = {'session_reminder_hours': 0}
        self.org.save()
        self.assertEqual(self.run_at(5), 0)
        self.org.settings = {'session_reminder_hours': 4}
        self.org.save()
        self.assertEqual(self.run_at(5), 0)                  # 4 hours before: not yet
        self.session.is_cancelled = True
        self.session.save()
        self.assertEqual(self.run_at(3), 0)
        self.session.is_cancelled = False
        self.session.save()
        self.assertEqual(self.run_at(3), 1)

    def test_guardian_gets_a_childs_reminder(self):
        kid = Member.objects.create(organisation=self.org, name='Kid Able')
        kid.guardians.create(name='Parent Able', email='parent@example.com')
        self.book(kid)
        self.run_at(5)
        self.assertEqual(mail.outbox[0].to, ['parent@example.com'])
        self.assertIn('guardian of Kid Able', mail.outbox[0].body)

    def test_the_job_is_scheduled(self):
        from jobs.registry import JOBS
        self.assertIn('send_session_reminders', [name for name, *_ in JOBS])


class ReminderSettingsTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name='Test Club', slug='test-club', features=dict(ALL_ON))
        self.member = Member.objects.create(organisation=self.org, name='Alice Able', email='alice@example.com')

    def test_club_chooses_when_and_who(self):
        self.assertEqual((self.org.session_reminder_hours, self.org.session_reminder_scope), (24, 'booked'))
        admin = User.objects.create_user('admin', password='pw')
        OrganisationMember.objects.create(user=admin, organisation=self.org, role='org_admin')
        self.org.settings = {**(self.org.settings or {}), 'two_factor': 'optional'}
        self.org.save()
        self.client.force_login(admin)
        self.client.post(reverse('org_settings', kwargs={'org_slug': self.org.slug}), {
            'action': 'features', 'features': list(ALL_ON), 'session_reminder_hours': '48',
            'session_reminder_scope': 'everyone'})
        self.org.refresh_from_db()
        self.assertEqual((self.org.session_reminder_hours, self.org.session_reminder_scope), (48, 'everyone'))

    def test_members_switch_reminders_off_in_their_portal(self):
        url = reverse('portal_my_details', kwargs={'token': self.member.token})
        self.assertContains(self.client.get(url), 'Email me a reminder 24 hours before sessions')
        self.client.post(url, {'reminders_shown': '1', 'email': 'alice@example.com'})
        self.member.refresh_from_db()
        self.assertFalse(self.member.session_reminders)

    def test_booking_confirmations_now_say_when_the_session_starts(self):
        day = timezone.localdate() + timedelta(days=7)
        cls = Class.objects.create(organisation=self.org, name='Adults', is_active=True,
                                   schedule=[{'day': day.weekday(), 'time': '19:30', 'end': '21:00'}])
        session = Session.objects.create(assigned_class=cls, date=day)
        from billing.models import BillingPolicy
        self.member.billing_policy = BillingPolicy.objects.create(organisation=self.org, name='Std', billing_cycle='monthly', amount=30)
        self.member.save()
        booking.book_session(self.member, session)
        self.assertIn('19:30–21:00', mail.outbox[-1].body)
