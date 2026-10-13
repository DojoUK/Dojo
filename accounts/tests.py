from io import StringIO
from unittest import mock

from django.contrib.auth.models import User
from django.core import mail
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from accounts import totp
from accounts.models import RecoveryCode, TwoFactorDevice
from organisations.models import Organisation, OrganisationMember

NOW = 1_700_000_000  # a fixed clock, so codes don't roll over mid-test
SECRET = totp.generate_secret()


def code(secret=SECRET, steps_from_now=0):
    return totp.code_at(secret, totp.current_step(NOW) + steps_from_now)


@mock.patch('accounts.totp.time.time', return_value=NOW)
class TotpTests(TestCase):
    RFC_SECRET = 'GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ'  # ASCII "12345678901234567890"

    def test_rfc_6238_reference_codes(self, _clock):
        # RFC 6238 appendix B (SHA-1), last six digits.
        for seconds, expected in ((59, '287082'), (1111111109, '081804'), (1234567890, '005924')):
            with self.subTest(seconds=seconds):
                self.assertEqual(totp.code_at(self.RFC_SECRET, seconds // 30), expected)

    def test_accepts_one_step_of_clock_drift_but_no_more(self, _clock):
        step = totp.current_step()
        self.assertEqual(totp.matching_step(SECRET, code(steps_from_now=-1)), step - 1)
        self.assertEqual(totp.matching_step(SECRET, code(steps_from_now=1)), step + 1)
        self.assertIsNone(totp.matching_step(SECRET, code(steps_from_now=2)))
        self.assertIsNone(totp.matching_step(SECRET, 'abcdef'))

    def test_a_used_code_cannot_be_replayed(self, _clock):
        step = totp.matching_step(SECRET, code())
        self.assertIsNone(totp.matching_step(SECRET, code(), after_step=step))

    def test_setup_link_names_the_club(self, _clock):
        uri = totp.provisioning_uri('ABC', 'coach', 'Dojo (Test Club)')
        self.assertEqual(uri, 'otpauth://totp/Dojo%20%28Test%20Club%29%3Acoach?secret=ABC&issuer=Dojo+%28Test+Club%29')


class TwoFactorTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organisation.objects.create(name='Test Club', slug='test-club')
        cls.admin = User.objects.create_user('admin', password='pw', email='admin@example.com')
        cls.coach = User.objects.create_user('coach', password='pw', email='coach@example.com')
        cls.admin_om = OrganisationMember.objects.create(user=cls.admin, organisation=cls.org, role='org_admin')
        cls.coach_om = OrganisationMember.objects.create(user=cls.coach, organisation=cls.org, role='coach')

    def setUp(self):
        patcher = mock.patch('accounts.totp.time.time', return_value=NOW)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.dashboard = reverse('org_dashboard', kwargs={'org_slug': self.org.slug})

    def set_policy(self, policy):
        self.org.settings = {**self.org.settings, 'two_factor': policy}
        self.org.save()

    def enrol(self, user):
        device = TwoFactorDevice.objects.create(user=user, secret=SECRET)
        return device, device.new_recovery_codes()

    def sign_in(self, user):
        self.client.force_login(user)


class SignInTests(TwoFactorTestCase):
    def test_without_an_app_and_not_required_nothing_changes(self):
        self.sign_in(self.coach)
        self.assertEqual(self.client.get(self.dashboard).status_code, 200)

    def test_with_an_app_every_page_waits_for_a_code(self):
        self.enrol(self.coach)
        self.sign_in(self.coach)
        response = self.client.get(self.dashboard)
        self.assertRedirects(response, f"{reverse('two_factor_verify')}?next={self.dashboard}", fetch_redirect_response=False)
        response = self.client.post(reverse('two_factor_verify'), {'code': code(), 'next': self.dashboard})
        self.assertRedirects(response, self.dashboard, fetch_redirect_response=False)
        self.assertEqual(self.client.get(self.dashboard).status_code, 200)

    def test_htmx_requests_are_told_where_to_go(self):
        self.enrol(self.coach)
        self.sign_in(self.coach)
        response = self.client.get(self.dashboard, HTTP_HX_REQUEST='true')
        self.assertTrue(response['HX-Redirect'].startswith(reverse('two_factor_verify')))

    def test_wrong_codes_lock_the_app_for_a_while(self):
        self.enrol(self.coach)
        self.sign_in(self.coach)
        for _ in range(5):
            response = self.client.post(reverse('two_factor_verify'), {'code': '000000'})
            self.assertEqual(response.status_code, 400)
        response = self.client.post(reverse('two_factor_verify'), {'code': code()})
        self.assertContains(response, 'Too many wrong codes', status_code=400)
        self.assertEqual(self.client.get(self.dashboard).status_code, 302)

    def test_a_recovery_code_works_once(self):
        _, codes = self.enrol(self.coach)
        self.assertEqual(len(codes), 10)
        self.sign_in(self.coach)
        response = self.client.post(reverse('two_factor_verify'), {'code': codes[0].lower()}, follow=True)
        self.assertContains(response, '9 left')
        self.client.logout()
        self.sign_in(self.coach)
        response = self.client.post(reverse('two_factor_verify'), {'code': codes[0]})
        self.assertEqual(response.status_code, 400)

    def test_recovery_codes_are_stored_hashed(self):
        _, codes = self.enrol(self.coach)
        stored = set(RecoveryCode.objects.values_list('code_hash', flat=True))
        self.assertFalse(stored & {c.replace('-', '') for c in codes})

    def test_unsafe_next_urls_are_ignored(self):
        self.enrol(self.coach)
        self.sign_in(self.coach)
        response = self.client.post(reverse('two_factor_verify'), {'code': code(), 'next': 'https://evil.example/'})
        self.assertEqual(response['Location'], reverse('root'))


class PolicyTests(TwoFactorTestCase):
    def assert_sent_to_setup(self, user, expected):
        self.client.logout()
        self.sign_in(user)
        response = self.client.get(self.dashboard)
        if expected:
            self.assertTrue(response['Location'].startswith(reverse('two_factor_setup')), user)
        else:
            self.assertEqual(response.status_code, 200, user)

    def test_required_for_all_staff(self):
        self.set_policy('all')
        self.assert_sent_to_setup(self.admin, True)
        self.assert_sent_to_setup(self.coach, True)

    def test_required_for_admins_only(self):
        self.set_policy('admins')
        self.assert_sent_to_setup(self.admin, True)
        self.assert_sent_to_setup(self.coach, False)

    def test_optional(self):
        self.set_policy('optional')
        self.assert_sent_to_setup(self.admin, False)

    def test_clubs_from_before_default_to_optional_and_bad_values_too(self):
        self.assertEqual(self.org.two_factor_policy, 'optional')
        self.set_policy('sometimes')
        self.assertEqual(self.org.two_factor_policy, 'optional')

    def test_superusers_follow_any_club_that_requires_it(self):
        root = User.objects.create_superuser('root', password='pw')
        self.set_policy('admins')
        self.assert_sent_to_setup(root, True)

    def test_logging_out_is_always_possible(self):
        self.set_policy('all')
        self.sign_in(self.coach)
        response = self.client.post(reverse('logout'))
        self.assertRedirects(response, reverse('login'), fetch_redirect_response=False)

    def test_new_clubs_start_with_it_required(self):
        Organisation.objects.all().delete()
        User.objects.all().delete()
        self.client.post(reverse('setup'), {
            'org_name': 'New Club', 'username': 'founder', 'admin_email': 'f@example.com',
            'password': 'a-long-password', 'password_confirm': 'a-long-password',
        })
        self.assertEqual(Organisation.objects.get().two_factor_policy, 'all')

    def test_admins_choose_the_policy_in_settings(self):
        self.sign_in(self.admin)
        url = reverse('org_settings', kwargs={'org_slug': self.org.slug})
        self.client.post(url, {'action': 'security', 'session_timeout_minutes': '60', 'two_factor': 'admins'})
        self.org.refresh_from_db()
        self.assertEqual(self.org.two_factor_policy, 'admins')
        self.client.post(url, {'action': 'security', 'session_timeout_minutes': '60', 'two_factor': 'nobody'})
        self.org.refresh_from_db()
        self.assertEqual(self.org.two_factor_policy, 'admins')


class SetupTests(TwoFactorTestCase):
    def test_scan_confirm_and_get_recovery_codes(self):
        self.set_policy('all')
        self.sign_in(self.coach)
        page = self.client.get(reverse('two_factor_setup'))
        self.assertContains(page, '<svg')
        self.assertContains(page, 'Your club asks staff')
        secret = self.client.session['two_factor_setup_secret']
        response = self.client.post(reverse('two_factor_setup'), {'code': code(secret), 'next': self.dashboard})
        self.assertContains(response, 'Two-step sign-in is on')
        self.assertEqual(response.content.decode().count('font-monospace'), 1)
        self.assertEqual(RecoveryCode.objects.filter(user=self.coach).count(), 10)
        # Signed in already: no second prompt straight after setting it up.
        self.assertEqual(self.client.get(self.dashboard).status_code, 200)
        # The code used to confirm can't be used again.
        self.assertIsNone(self.coach.two_factor.verify(code(secret)))

    def test_a_wrong_code_does_not_turn_it_on(self):
        self.sign_in(self.coach)
        self.client.get(reverse('two_factor_setup'))
        response = self.client.post(reverse('two_factor_setup'), {'code': '123456'})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(TwoFactorDevice.objects.exists())


class SelfServiceTests(TwoFactorTestCase):
    def setUp(self):
        super().setUp()
        self.device, _ = self.enrol(self.coach)
        self.sign_in(self.coach)
        self.client.post(reverse('two_factor_verify'), {'code': code()})

    def test_account_page_shows_status(self):
        response = self.client.get(reverse('account_settings', kwargs={'org_slug': self.org.slug}))
        self.assertContains(response, '10 recovery codes left')
        self.assertContains(response, reverse('two_factor_disable'))

    def test_new_recovery_codes_need_a_fresh_code(self):
        old = set(RecoveryCode.objects.values_list('code_hash', flat=True))
        response = self.client.post(reverse('two_factor_recovery_codes'), {'code': code(steps_from_now=1)})
        self.assertContains(response, 'Your recovery codes')
        self.assertFalse(old & set(RecoveryCode.objects.values_list('code_hash', flat=True)))

    def test_turning_off_when_optional(self):
        self.client.post(reverse('two_factor_disable'), {'code': code(steps_from_now=1)})
        self.assertFalse(TwoFactorDevice.objects.filter(user=self.coach).exists())
        self.assertFalse(RecoveryCode.objects.filter(user=self.coach).exists())

    def test_cannot_turn_off_when_required(self):
        self.set_policy('all')
        self.assertContains(self.client.get(reverse('two_factor_disable')), 'requires two-step sign-in')
        self.client.post(reverse('two_factor_disable'), {'code': code(steps_from_now=1)})
        self.assertTrue(TwoFactorDevice.objects.filter(user=self.coach).exists())


class StaffAdminTests(TwoFactorTestCase):
    def setUp(self):
        super().setUp()
        self.sign_in(self.admin)
        self.url = reverse('org_staff', kwargs={'org_slug': self.org.slug})

    def act(self, action, om):
        return self.client.post(self.url, {'action': action, 'member_pk': om.pk}, follow=True)

    def test_staff_list_shows_sign_in_status(self):
        self.enrol(self.coach)
        response = self.client.get(self.url)
        self.assertContains(response, '2-step')
        self.assertContains(response, 'Reset two-step sign-in')

    def test_reset_two_factor_signs_them_out_of_it(self):
        self.enrol(self.coach)
        coach_client = self.client_class()
        coach_client.force_login(self.coach)
        coach_client.post(reverse('two_factor_verify'), {'code': code()})
        self.assertEqual(coach_client.get(self.dashboard).status_code, 200)
        self.set_policy('all')
        self.enrol(self.admin)
        self.client.post(reverse('two_factor_verify'), {'code': code(steps_from_now=1)})
        self.act('reset_two_factor', self.coach_om)
        self.assertFalse(TwoFactorDevice.objects.filter(user=self.coach).exists())
        # Their open session no longer counts as verified: they must set the app up again.
        self.assertTrue(coach_client.get(self.dashboard)['Location'].startswith(reverse('two_factor_setup')))

    def test_lock_out_ends_their_session_and_blocks_sign_in(self):
        coach_client = self.client_class()
        coach_client.force_login(self.coach)
        self.act('lock_account', self.coach_om)
        self.coach.refresh_from_db()
        self.assertFalse(self.coach.is_active)
        self.assertTrue(coach_client.get(self.dashboard)['Location'].startswith(reverse('login')))
        self.assertFalse(self.client_class().login(username='coach', password='pw'))
        self.assertContains(self.client.get(self.url), 'Locked out')
        self.act('unlock_account', self.coach_om)
        self.assertTrue(self.client_class().login(username='coach', password='pw'))

    def test_password_reset_link_is_emailed(self):
        self.act('send_password_reset', self.coach_om)
        self.assertEqual(mail.outbox[-1].to, ['coach@example.com'])
        self.assertIn('/password-reset/', mail.outbox[-1].body)

    def test_no_email_address_no_link(self):
        User.objects.filter(pk=self.coach.pk).update(email='')
        response = self.act('send_password_reset', self.coach_om)
        self.assertContains(response, 'has no email address')
        self.assertEqual(mail.outbox, [])

    def test_cannot_lock_yourself_out(self):
        response = self.act('lock_account', self.admin_om)
        self.assertContains(response, 'your own account page')
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)

    def test_people_in_another_club_are_left_to_a_superuser(self):
        other = Organisation.objects.create(name='Other Club', slug='other-club')
        OrganisationMember.objects.create(user=self.coach, organisation=other, role='coach')
        response = self.act('lock_account', self.coach_om)
        self.assertContains(response, 'also works for another club')
        self.coach.refresh_from_db()
        self.assertTrue(self.coach.is_active)

    def test_superusers_are_left_to_superusers(self):
        root = User.objects.create_superuser('root', password='pw')
        root_om = OrganisationMember.objects.create(user=root, organisation=self.org, role='org_admin')
        response = self.act('lock_account', root_om)
        self.assertContains(response, 'only another superuser')

    def test_another_clubs_staff_are_out_of_reach(self):
        other = Organisation.objects.create(name='Other Club', slug='other-club')
        outsider = User.objects.create_user('outsider', password='pw')
        outsider_om = OrganisationMember.objects.create(user=outsider, organisation=other, role='coach')
        self.assertEqual(self.client.post(self.url, {'action': 'lock_account', 'member_pk': outsider_om.pk}).status_code, 404)

    def test_coaches_cannot_use_these_actions(self):
        self.client.force_login(self.coach)
        response = self.client.post(self.url, {'action': 'lock_account', 'member_pk': self.admin_om.pk})
        self.assertIn(response.status_code, (302, 403))
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)


class ResetCommandTests(TwoFactorTestCase):
    def test_resets_by_username(self):
        self.enrol(self.admin)
        call_command('reset_two_factor', 'admin', stdout=StringIO())
        self.assertFalse(TwoFactorDevice.objects.exists())
        self.assertFalse(RecoveryCode.objects.exists())
