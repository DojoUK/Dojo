"""Birthday and "we miss you" emails (members/occasional.py)."""
from datetime import date, datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase
from django.urls import reverse

from classes.models import Attendance, Class, Session
from members import occasional
from members.models import Member, MemberLeavingRequest, OccasionalEmail
from organisations.features import FEATURES_BY_KEY
from organisations.models import Organisation, OrganisationMember

LONDON = ZoneInfo('Europe/London')
ALL_ON = {key: True for key in FEATURES_BY_KEY}


def at(day, hour=10):
    return datetime(day.year, day.month, day.day, hour, tzinfo=LONDON)


class OccasionalTestCase(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name='Test Club', slug='test-club', features=dict(ALL_ON),
                                               settings={'two_factor': 'optional', 'birthday_emails': True,
                                                         'win_back_days': 30})
        self.cls = Class.objects.create(organisation=self.org, name='Adults')

    def member(self, name='Ann Lee', **extra):
        return Member.objects.create(organisation=self.org, name=name,
                                     email=extra.pop('email', name.split()[0].lower() + '@example.com'), **extra)

    def attend(self, member, day):
        session, _ = Session.objects.get_or_create(assigned_class=self.cls, date=day)
        Attendance.objects.create(session=session, member=member, present=True)

    def send(self, day, hour=10):
        return occasional.send_due(now=at(day, hour))


class BirthdayTests(OccasionalTestCase):
    def test_a_member_gets_one_email_on_their_birthday(self):
        ann = self.member(date_of_birth=date(1990, 11, 24))
        self.member('Ben Cole', date_of_birth=date(1990, 11, 25))
        self.assertEqual(self.send(date(2026, 11, 24)), 1)
        self.assertEqual(self.send(date(2026, 11, 24), hour=15), 0)        # once a year
        email = mail.outbox[0]
        self.assertEqual(email.to, ['ann@example.com'])
        self.assertEqual(email.subject, 'Happy birthday, Ann!')
        self.assertIn('Happy birthday to Ann from everyone at', email.body)
        self.assertIn(reverse('portal_my_details', kwargs={'token': ann.token}) + '#club-emails',
                      email.alternatives[0][0])
        self.assertTrue(OccasionalEmail.objects.filter(member=ann, kind='birthday', occasion='2026').exists())
        self.assertEqual(self.send(date(2027, 11, 24)), 1)                  # and again next year

    def test_nothing_before_nine_in_the_morning(self):
        self.member(date_of_birth=date(1990, 11, 24))
        self.assertEqual(self.send(date(2026, 11, 24), hour=8), 0)
        self.assertEqual(self.send(date(2026, 11, 24), hour=9), 1)

    def test_29_february_birthdays_are_on_the_28th_in_other_years(self):
        self.member(date_of_birth=date(2008, 2, 29))
        self.assertEqual(self.send(date(2027, 2, 28)), 1)
        self.assertEqual(self.send(date(2028, 2, 28)), 0)                    # a leap year has the 29th
        self.assertEqual(self.send(date(2028, 2, 29)), 1)

    def test_a_child_without_an_email_has_their_guardian_emailed(self):
        child = self.member('Cara Dunn', email='', date_of_birth=date(2016, 11, 24))
        child.guardians.create(name='Dan Dunn', email='dan@example.com')
        self.send(date(2026, 11, 24))
        self.assertEqual(mail.outbox[0].to, ['dan@example.com'])
        self.assertIn('Dear guardian of Cara Dunn', mail.outbox[0].body)

    def test_not_when_the_club_or_member_has_switched_them_off_or_the_member_has_left(self):
        self.member(date_of_birth=date(1990, 11, 24), occasional_emails=False)
        self.member('Ben Cole', date_of_birth=date(1990, 11, 24), is_active=False)
        self.member('Cy Doe', date_of_birth=date(1990, 11, 24), email='')       # nowhere to send it
        self.assertEqual(self.send(date(2026, 11, 24)), 0)
        self.member('Dee Fox', date_of_birth=date(1990, 11, 24))
        self.org.settings['birthday_emails'] = False
        self.org.save()
        self.assertEqual(self.send(date(2026, 11, 24)), 0)
        self.assertEqual(mail.outbox, [])

    def test_a_failed_email_is_tried_again_at_the_next_run(self):
        self.member(date_of_birth=date(1990, 11, 24))
        with mock.patch('mailer.compose.send', side_effect=OSError('mail server down')):
            self.assertEqual(self.send(date(2026, 11, 24)), 0)
        self.assertFalse(OccasionalEmail.objects.exists())
        self.assertEqual(self.send(date(2026, 11, 24), hour=11), 1)


class WinBackTests(OccasionalTestCase):
    today = date(2026, 11, 24)

    def setUp(self):
        super().setUp()
        self.org.settings['birthday_emails'] = False
        self.org.save()

    def test_a_member_away_for_the_chosen_number_of_days_gets_one_email(self):
        ann = self.member()
        self.attend(ann, self.today - timedelta(days=45))
        self.attend(ann, self.today - timedelta(days=30))
        self.assertEqual(self.send(self.today), 1)
        self.assertEqual(self.send(self.today + timedelta(days=1)), 0)      # once per absence
        email = mail.outbox[0]
        self.assertEqual(email.subject, 'We miss you at Test Club')
        self.assertIn('since Sunday 25 October', email.body)
        self.assertIn(reverse('member_portal', kwargs={'token': ann.token}), email.alternatives[0][0])

    def test_coming_back_and_stopping_again_is_a_new_absence(self):
        ann = self.member()
        self.attend(ann, self.today - timedelta(days=30))
        self.send(self.today)
        self.attend(ann, self.today + timedelta(days=5))
        self.assertEqual(self.send(self.today + timedelta(days=35)), 1)
        self.assertEqual(OccasionalEmail.objects.filter(member=ann, kind='win_back').count(), 2)

    def test_not_too_soon_and_not_for_members_who_stopped_long_ago(self):
        recent, long_gone = self.member(), self.member('Ben Cole')
        self.attend(recent, self.today - timedelta(days=29))
        self.attend(long_gone, self.today - timedelta(days=30 + occasional.WIN_BACK_WINDOW_DAYS))
        self.assertEqual(self.send(self.today), 0)

    def test_not_for_members_leaving_who_never_trained_or_who_switched_them_off(self):
        leaving, never = self.member(), self.member('Ben Cole')
        quiet = self.member('Cy Doe', occasional_emails=False)
        self.attend(leaving, self.today - timedelta(days=31))
        self.attend(quiet, self.today - timedelta(days=31))
        MemberLeavingRequest.objects.create(member=leaving, reason='Moving away')
        self.assertEqual(self.send(self.today), 0)
        self.assertFalse(OccasionalEmail.objects.filter(member=never).exists())

    def test_off_when_the_club_has_not_chosen_a_number_of_days(self):
        ann = self.member()
        self.attend(ann, self.today - timedelta(days=30))
        self.org.settings['win_back_days'] = 0
        self.org.save()
        self.assertEqual(self.send(self.today), 0)

    def test_the_email_names_the_class_they_last_came_to(self):
        ann = self.member()
        self.attend(ann, self.today - timedelta(days=30))
        values = {}
        with mock.patch('mailer.compose.send', side_effect=lambda org, key, to, v, b=None: values.update(v)):
            self.send(self.today)
        self.assertEqual((values['class_name'], values['days_away']), ('Adults', 30))


class SettingsTests(OccasionalTestCase):
    def setUp(self):
        super().setUp()
        self.org.settings = {'two_factor': 'optional'}
        self.org.save()
        admin = User.objects.create_user('admin', password='pw')
        OrganisationMember.objects.create(user=admin, organisation=self.org, role='org_admin')
        self.client.force_login(admin)

    def save(self, **values):
        features = [key for key, on in ALL_ON.items() if on]
        self.client.post(reverse('org_settings', kwargs={'org_slug': self.org.slug}),
                         {'action': 'features', 'features': features, 'occasional_emails_shown': '1', **values})
        self.org.refresh_from_db()

    def test_both_are_off_until_a_club_switches_them_on(self):
        self.assertFalse(self.org.birthday_emails)
        self.assertEqual(self.org.win_back_days, 0)
        self.assertFalse(self.org.sends_occasional_emails)

    def test_clubs_switch_them_on_and_off_in_features(self):
        self.save(birthday_emails='1', win_back_days='45')
        self.assertTrue(self.org.birthday_emails)
        self.assertEqual(self.org.win_back_days, 45)
        self.save(win_back_days='13')                     # not a choice, and birthdays unticked
        self.assertFalse(self.org.birthday_emails)
        self.assertEqual(self.org.win_back_days, 0)


class PortalTests(OccasionalTestCase):
    def test_members_switch_them_off_in_my_details(self):
        ann = self.member()
        url = reverse('portal_my_details', kwargs={'token': ann.token})
        page = self.client.get(url)
        self.assertContains(page, 'id="club-emails"')
        self.assertContains(page, 'Birthday wishes, and a note from Test Club')
        self.client.post(url, {'occasional_shown': '1'})
        ann.refresh_from_db()
        self.assertFalse(ann.occasional_emails)
        self.client.post(url, {'occasional_shown': '1', 'occasional_emails': '1'})
        ann.refresh_from_db()
        self.assertTrue(ann.occasional_emails)

    def test_the_switch_is_hidden_when_the_club_sends_neither(self):
        self.org.settings = {}
        self.org.save()
        page = self.client.get(reverse('portal_my_details', kwargs={'token': self.member().token}))
        self.assertNotContains(page, 'id="club-emails"')
