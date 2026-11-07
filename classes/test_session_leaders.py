from datetime import date, timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from classes.models import Class, ClassCoach, Session
from organisations.features import FEATURES_BY_KEY
from organisations.models import Organisation, OrganisationMember

ALL_ON = {key: True for key in FEATURES_BY_KEY}
WEEKLY = [{'day': 0, 'time': '18:00', 'end_time': '19:00'}]


class LeaderTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organisation.objects.create(name='Test Club', slug='test-club', features=dict(ALL_ON))
        cls.admin = User.objects.create_user('admin', password='pw')
        OrganisationMember.objects.create(user=cls.admin, organisation=cls.org, role='org_admin')
        cls.ann = User.objects.create_user('ann', password='pw', first_name='Ann', last_name='Lead')
        cls.ann_om = OrganisationMember.objects.create(
            user=cls.ann, organisation=cls.org, role='coach', calendar_colour='#2563EB',
        )
        cls.bob = User.objects.create_user('bob', password='pw', first_name='Bob', last_name='Help')
        OrganisationMember.objects.create(user=cls.bob, organisation=cls.org, role='coach')
        cls.outsider = User.objects.create_user('outsider', password='pw', first_name='Out')
        OrganisationMember.objects.create(user=cls.outsider, organisation=cls.org, role='coach')

        cls.cls = Class.objects.create(organisation=cls.org, name='Juniors', schedule=WEEKLY)
        cls.other = Class.objects.create(organisation=cls.org, name='Seniors', schedule=WEEKLY)
        ClassCoach.objects.create(assigned_class=cls.cls, user=cls.ann)
        ClassCoach.objects.create(assigned_class=cls.cls, user=cls.bob)
        ClassCoach.objects.create(assigned_class=cls.other, user=cls.outsider)
        cls.session = Session.objects.create(assigned_class=cls.cls, date=date.today())

    def setUp(self):
        self.client.force_login(self.ann)

    def url(self, name, **kwargs):
        return reverse(name, kwargs={'org_slug': self.org.slug, **kwargs})

    def register_url(self, session=None):
        return self.url('session_register', pk=self.cls.pk, session_pk=(session or self.session).pk)

    def save_register(self, **data):
        return self.client.post(self.register_url(), {'notes': '', **data})


class RegisterLeaderTests(LeaderTestBase):
    def test_leader_saved_when_marked_present(self):
        self.save_register(coach_present=[self.ann.pk, self.bob.pk], leader=self.bob.pk)
        self.session.refresh_from_db()
        self.assertEqual(self.session.leader, self.bob)

    def test_leader_must_be_marked_present(self):
        response = self.save_register(coach_present=[self.ann.pk], leader=self.bob.pk)
        self.assertContains(response, 'The session leader must be one of the coaches marked present.')
        self.session.refresh_from_db()
        self.assertIsNone(self.session.leader)

    def test_someone_who_does_not_coach_the_class_cannot_be_leader(self):
        self.save_register(coach_present=[self.ann.pk, self.outsider.pk], leader=self.outsider.pk)
        self.session.refresh_from_db()
        self.assertIsNone(self.session.leader)

    def test_no_leader_clears_it(self):
        self.session.leader = self.ann
        self.session.save()
        self.save_register(coach_present=[self.ann.pk])
        self.session.refresh_from_db()
        self.assertIsNone(self.session.leader)

    def test_register_suggests_the_usual_leader(self):
        self.cls.default_leader = self.bob
        self.cls.save()
        response = self.client.get(self.register_url())
        self.assertEqual(response.context['leader_id'], self.bob.pk)
        self.assertContains(response, 'Usually Bob Help')

    def test_sessions_own_leader_wins(self):
        self.cls.default_leader = self.bob
        self.cls.save()
        self.session.leader = self.ann
        self.session.save()
        self.assertEqual(self.client.get(self.register_url()).context['leader_id'], self.ann.pk)


class UsualLeaderTests(LeaderTestBase):
    def set_leader(self, user, cls=None):
        return self.client.post(
            self.url('class_set_default_leader', pk=(cls or self.cls).pk),
            {'default_leader': user.pk if user else ''},
        )

    def test_coach_of_the_class_sets_the_usual_leader(self):
        response = self.set_leader(self.bob)
        self.assertRedirects(response, self.url('coach_class_detail', pk=self.cls.pk), fetch_redirect_response=False)
        self.cls.refresh_from_db()
        self.assertEqual(self.cls.default_leader, self.bob)

    def test_only_a_coach_of_the_class_can_be_the_usual_leader(self):
        self.set_leader(self.outsider)
        self.cls.refresh_from_db()
        self.assertIsNone(self.cls.default_leader)

    def test_coach_of_another_class_cannot_set_it(self):
        self.client.force_login(self.outsider)
        self.assertEqual(self.set_leader(self.outsider).status_code, 403)

    def test_another_clubs_admin_and_anonymous_cannot_set_it(self):
        other_org = Organisation.objects.create(name='Other Club', slug='other-club', features=dict(ALL_ON))
        other_admin = User.objects.create_user('other-admin', password='pw')
        OrganisationMember.objects.create(user=other_admin, organisation=other_org, role='org_admin')
        self.client.force_login(other_admin)
        self.assertIn(self.set_leader(self.ann).status_code, (403, 404))
        self.client.logout()
        response = self.set_leader(self.ann)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response['Location'])
        self.cls.refresh_from_db()
        self.assertIsNone(self.cls.default_leader)

    def test_clearing(self):
        self.cls.default_leader = self.ann
        self.cls.save()
        self.set_leader(None)
        self.cls.refresh_from_db()
        self.assertIsNone(self.cls.default_leader)

    def test_generated_sessions_start_with_the_usual_leader(self):
        self.cls.default_leader = self.bob
        self.cls.save()
        self.client.force_login(self.admin)
        self.client.post(self.url('class_generate_sessions', pk=self.cls.pk), {'weeks': 3})
        new_sessions = self.cls.sessions.exclude(pk=self.session.pk)
        self.assertTrue(new_sessions.exists())
        self.assertFalse(new_sessions.exclude(leader=self.bob).exists())
        # The existing session keeps what it had.
        self.session.refresh_from_db()
        self.assertIsNone(self.session.leader)

    def test_removing_the_usual_leader_as_coach_clears_it(self):
        self.cls.default_leader = self.bob
        self.cls.save()
        self.client.force_login(self.admin)
        coach = ClassCoach.objects.get(assigned_class=self.cls, user=self.bob)
        self.client.post(self.url('class_coach_remove', pk=self.cls.pk, coach_pk=coach.pk))
        self.cls.refresh_from_db()
        self.assertIsNone(self.cls.default_leader)

    def test_class_pages_show_who_leads(self):
        self.session.leader = self.ann
        self.session.save()
        self.assertContains(self.client.get(self.url('coach_class_detail', pk=self.cls.pk)), 'Led by Ann Lead')
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(self.url('class_detail', pk=self.cls.pk)), 'Led by Ann Lead')


class CalendarColourTests(LeaderTestBase):
    def events(self):
        today = date.today()
        response = self.client.get(self.url('org_calendar_events'), {
            'start': (today - timedelta(days=1)).isoformat(), 'end': (today + timedelta(days=1)).isoformat(),
        })
        return [e for e in response.json() if not str(e['id']).startswith('holiday')]

    def test_session_shows_its_leader_in_their_colour(self):
        self.session.leader = self.ann
        self.session.save()
        [event] = self.events()
        self.assertEqual(event['title'], 'Juniors — Ann Lead')
        self.assertEqual(event['color'], '#2563EB')

    def test_falls_back_to_the_usual_leader(self):
        self.cls.default_leader = self.ann
        self.cls.save()
        [event] = self.events()
        self.assertEqual(event['title'], 'Juniors — Ann Lead')

    def test_leader_without_a_colour_gets_the_default(self):
        self.session.leader = self.bob
        self.session.save()
        [event] = self.events()
        self.assertEqual(event['color'], '#212529')

    def test_cancelled_stays_red(self):
        self.session.leader = self.ann
        self.session.is_cancelled = True
        self.session.save()
        [event] = self.events()
        self.assertEqual(event['color'], '#dc3545')
        self.assertIn('(cancelled)', event['title'])

    def test_coach_only_sees_their_own_classes(self):
        Session.objects.create(assigned_class=self.other, date=date.today())
        self.assertEqual([e['title'].split(' —')[0] for e in self.events()], ['Juniors'])


class AccountColourTests(LeaderTestBase):
    def post(self, **data):
        return self.client.post(self.url('account_settings'), {'action': 'update_calendar_colour', **data})

    def test_set_own_colour(self):
        self.client.force_login(self.bob)
        self.post(calendar_colour='#16a34a')
        self.assertEqual(OrganisationMember.objects.get(user=self.bob).calendar_colour, '#16A34A')

    def test_bad_colour_is_refused(self):
        self.post(calendar_colour='red; background:url(x)')
        self.ann_om.refresh_from_db()
        self.assertEqual(self.ann_om.calendar_colour, '#2563EB')

    def test_clear_back_to_default(self):
        self.post(calendar_colour='#2563EB', clear='1')
        self.ann_om.refresh_from_db()
        self.assertEqual(self.ann_om.calendar_colour, '')
