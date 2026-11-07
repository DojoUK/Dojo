from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from organisations.models import Organisation, OrganisationMember


class AppearanceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organisation.objects.create(name='Test Club', slug='test-club')
        cls.ann = User.objects.create_user('ann', password='pw')
        cls.ann_om = OrganisationMember.objects.create(user=cls.ann, organisation=cls.org, role='coach')
        cls.bob = User.objects.create_user('bob', password='pw')
        OrganisationMember.objects.create(user=cls.bob, organisation=cls.org, role='org_admin')

    def setUp(self):
        self.client.force_login(self.ann)

    def url(self, name):
        return reverse(name, kwargs={'org_slug': self.org.slug})

    def save(self, **data):
        return self.client.post(self.url('account_settings'), {'action': 'update_appearance', **data})

    def html_tag(self, user=None):
        if user:
            self.client.force_login(user)
        html = self.client.get(self.url('account_settings')).content.decode()
        return html[html.index('<html'):html.index('>', html.index('<html')) + 1]

    def test_light_by_default(self):
        tag = self.html_tag()
        self.assertIn('data-bs-theme="light"', tag)
        self.assertNotIn('data-accent-theme', tag)

    def test_dark_mode_and_theme_are_personal(self):
        self.save(dark_mode='1', accent_theme='green')
        self.ann_om.refresh_from_db()
        self.assertTrue(self.ann_om.dark_mode)
        self.assertEqual(self.ann_om.accent_theme, 'green')
        tag = self.html_tag()
        self.assertIn('data-bs-theme="dark"', tag)
        self.assertIn('data-accent-theme="green"', tag)
        self.assertIn('data-bs-theme="light"', self.html_tag(self.bob))

    def test_switching_back(self):
        self.save(dark_mode='1', accent_theme='rose')
        self.save(accent_theme='')
        self.ann_om.refresh_from_db()
        self.assertFalse(self.ann_om.dark_mode)
        self.assertEqual(self.ann_om.accent_theme, '')

    def test_unknown_theme_is_refused(self):
        self.save(dark_mode='1', accent_theme='"><script>')
        self.ann_om.refresh_from_db()
        self.assertFalse(self.ann_om.dark_mode)
        self.assertEqual(self.ann_om.accent_theme, '')

    def test_sign_in_page_stays_light(self):
        self.client.logout()
        html = self.client.get(reverse('login')).content.decode()
        self.assertIn('data-bs-theme="light"', html)


class FloatingMessagesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organisation.objects.create(name='Test Club', slug='test-club')
        cls.ann = User.objects.create_user('ann', password='pw')
        OrganisationMember.objects.create(user=cls.ann, organisation=cls.org, role='coach')

    def test_messages_float_and_errors_are_announced(self):
        self.client.force_login(self.ann)
        url = reverse('account_settings', kwargs={'org_slug': self.org.slug})
        response = self.client.post(url, {'action': 'update_appearance', 'accent_theme': 'nope'}, follow=True)
        self.assertContains(response, 'class="dojo-messages" id="dojo-messages"')
        self.assertContains(response, 'role="alert"')
        response = self.client.post(url, {'action': 'update_appearance', 'accent_theme': 'blue'}, follow=True)
        self.assertContains(response, 'role="status"')
