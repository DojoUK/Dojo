from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from organisations.models import Organisation, OrganisationMember
from organisations.views import OrgSettingsView


class SettingsTabsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organisation.objects.create(name='Test Club', slug='test-club')
        cls.admin = User.objects.create_user('admin', password='pw')
        OrganisationMember.objects.create(user=cls.admin, organisation=cls.org, role='org_admin')

    def setUp(self):
        self.client.force_login(self.admin)
        self.url = reverse('org_settings', kwargs={'org_slug': self.org.slug})

    def test_each_section_is_a_tab_with_its_form(self):
        html = self.client.get(self.url).content.decode()
        keys = [key for key, _, _, _ in OrgSettingsView.SECTIONS]
        positions = [html.index(f'data-bs-target="#pane-{key}"') for key in keys]
        self.assertEqual(positions, sorted(positions))  # tabs in the declared order
        for key in keys:
            self.assertIn(f'id="pane-{key}"', html)
        for action in ('general', 'theme', 'features', 'fields', 'security'):
            with self.subTest(action=action):
                self.assertIn(f'name="action" value="{action}"', html)
        self.assertIn(reverse('org_test_email', kwargs={'org_slug': self.org.slug}), html)

    def test_saving_returns_to_the_same_tab(self):
        cases = {
            'general': ({'action': 'general', 'name': 'Renamed Club'}, '#general'),
            'theme': ({'action': 'theme', 'sidebar_color': '#123456', 'accent_color': '#654321'}, '#appearance'),
            'features': ({'action': 'features', 'features': ['billing']}, '#features'),
            'fields': ({'action': 'fields', 'optional_fields': ['phone']}, '#fields'),
            'security': ({'action': 'security', 'session_timeout_minutes': '60'}, '#security'),
        }
        for name, (data, anchor) in cases.items():
            with self.subTest(form=name):
                response = self.client.post(self.url, data)
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response['Location'].endswith(anchor), response['Location'])
