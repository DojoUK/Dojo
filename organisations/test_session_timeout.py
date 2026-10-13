from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from organisations.models import Organisation, OrganisationMember


class SessionTimeoutTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organisation.objects.create(name='Test Club', slug='test-club')
        cls.admin = User.objects.create_user('admin', password='pw')
        OrganisationMember.objects.create(user=cls.admin, organisation=cls.org, role='org_admin')

    def setUp(self):
        self.client.force_login(self.admin)

    def url(self, name, org=None):
        return reverse(name, kwargs={'org_slug': (org or self.org).slug})

    def set_timeout(self, minutes):
        self.org.settings = {**self.org.settings, 'session_timeout_minutes': minutes}
        self.org.save(update_fields=['settings'])


class TimeoutAppliedTests(SessionTimeoutTestBase):
    def test_default_is_two_hours(self):
        self.client.get(self.url('org_dashboard'))
        self.assertEqual(self.client.session.get_expiry_age(), 2 * 60 * 60)

    def test_club_setting_is_used(self):
        self.set_timeout(15)
        self.client.get(self.url('org_dashboard'))
        self.assertEqual(self.client.session.get_expiry_age(), 15 * 60)

    def test_never_falls_back_to_the_normal_session_length(self):
        self.set_timeout(0)
        self.client.get(self.url('org_dashboard'))
        self.assertEqual(self.client.session.get_expiry_age(), settings.SESSION_COOKIE_AGE)

    def test_unknown_stored_value_uses_the_default(self):
        self.set_timeout(7)
        self.assertEqual(Organisation.objects.get(pk=self.org.pk).session_timeout_minutes, 120)

    def test_pages_outside_an_organisation_leave_the_session_alone(self):
        self.set_timeout(15)
        self.client.get(reverse('login'))
        self.assertEqual(self.client.session.get_expiry_age(), settings.SESSION_COOKIE_AGE)

    def test_page_includes_the_warning_only_when_a_timeout_is_set(self):
        self.set_timeout(30)
        response = self.client.get(self.url('org_dashboard'))
        self.assertContains(response, 'id="idle-warning"')
        self.assertContains(response, 'var limit = 30 * 60 * 1000;')
        self.set_timeout(0)
        self.assertNotContains(self.client.get(self.url('org_dashboard')), 'id="idle-warning"')


class KeepAliveTests(SessionTimeoutTestBase):
    def test_keep_alive_restarts_the_clock(self):
        self.set_timeout(30)
        response = self.client.post(self.url('org_keep_alive'))
        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.client.session.get_expiry_age(), 30 * 60)

    def test_get_is_not_allowed(self):
        self.assertEqual(self.client.get(self.url('org_keep_alive')).status_code, 405)

    def test_other_org_and_anonymous_are_refused(self):
        other = Organisation.objects.create(name='Other', slug='other')
        self.assertEqual(self.client.post(self.url('org_keep_alive', other)).status_code, 403)
        self.client.logout()
        response = self.client.post(self.url('org_keep_alive'))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response['Location'])


class SecuritySettingsTests(SessionTimeoutTestBase):
    def save(self, value, org=None):
        return self.client.post(self.url('org_settings', org), {
            'action': 'security', 'session_timeout_minutes': value,
        })

    def stored(self):
        return Organisation.objects.get(pk=self.org.pk).settings.get('session_timeout_minutes')

    def test_admin_sets_the_timeout(self):
        self.org.settings = {'sidebar_color': '#123456'}
        self.org.save(update_fields=['settings'])
        self.save('30')
        self.assertEqual(self.stored(), 30)
        self.assertEqual(Organisation.objects.get(pk=self.org.pk).settings['sidebar_color'], '#123456')

    def test_only_listed_values_are_accepted(self):
        for value in ('7', 'forever', ''):
            with self.subTest(value=value):
                self.save(value)
                self.assertIsNone(self.stored())

    def test_coach_other_org_and_anonymous_cannot_change_it(self):
        coach = User.objects.create_user('coach', password='pw')
        OrganisationMember.objects.create(user=coach, organisation=self.org, role='coach')
        self.client.force_login(coach)
        self.assertEqual(self.save('15').status_code, 403)

        other = Organisation.objects.create(name='Other', slug='other')
        admin_b = User.objects.create_user('admin-b', password='pw')
        OrganisationMember.objects.create(user=admin_b, organisation=other, role='org_admin')
        self.client.force_login(admin_b)
        self.assertEqual(self.save('15').status_code, 403)

        self.client.logout()
        self.assertEqual(self.save('15').status_code, 302)
        self.assertIsNone(self.stored())
