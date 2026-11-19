from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from organisations.models import Organisation, OrganisationMember


class SidebarGradientTests(TestCase):
    """Settings > Appearance: a two-tone sidebar with both colours chosen by the club."""

    @classmethod
    def setUpTestData(cls):
        cls.org = Organisation.objects.create(name='Test Club', slug='test-club')
        cls.admin = User.objects.create_user('admin', password='pw')
        cls.admin_om = OrganisationMember.objects.create(user=cls.admin, organisation=cls.org, role='org_admin')

    def setUp(self):
        self.client.force_login(self.admin)
        self.url = reverse('org_settings', kwargs={'org_slug': self.org.slug})

    def save(self, **data):
        return self.client.post(self.url, {
            'action': 'theme', 'sidebar_color': '#1E3A5F', 'accent_color': '#2563EB', **data,
        })

    def page(self):
        return self.client.get(self.url).content.decode()

    def test_off_by_default_sidebar_shades_into_its_own_dark_tone(self):
        self.save()
        self.org.refresh_from_db()
        self.assertFalse(self.org.settings['sidebar_gradient'])
        self.assertIsNone(self.org.theme()['sidebar_gradient_end'])
        self.assertNotIn('--dojo-sidebar-end: #', self.page())

    def test_two_tone_uses_the_chosen_bottom_colour(self):
        self.save(sidebar_gradient='1', sidebar_color_end='#7C3AED')
        self.org.refresh_from_db()
        self.assertEqual(self.org.theme()['sidebar_gradient_end'], '#7C3AED')
        html = self.page()
        self.assertIn('--dojo-sidebar-end: #7C3AED;', html)
        self.assertIn('var(--dojo-sidebar-end, var(--dojo-sidebar-dark))', html)

    def test_switching_off_keeps_the_colour_for_next_time(self):
        self.save(sidebar_gradient='1', sidebar_color_end='#7C3AED')
        self.save(sidebar_color_end='#7C3AED')
        self.org.refresh_from_db()
        self.assertIsNone(self.org.theme()['sidebar_gradient_end'])
        self.assertEqual(self.org.settings['sidebar_color_end'], '#7C3AED')
        self.assertIn('value="#7C3AED"', self.page())

    def test_rejects_anything_but_a_hex_colour(self):
        self.save(sidebar_gradient='1', sidebar_color_end='red;}body{display:none')
        self.org.refresh_from_db()
        self.assertFalse(self.org.settings['sidebar_gradient'])
        self.assertNotIn('display:none', self.page())

    def test_personal_theme_and_dark_mode_replace_the_club_gradient(self):
        self.save(sidebar_gradient='1', sidebar_color_end='#7C3AED')
        self.admin_om.accent_theme = 'green'
        self.admin_om.save()
        html = self.page()
        self.assertIn('data-accent-theme="green"', html)
        self.assertIn('--dojo-sidebar-dark:#052e13; --dojo-sidebar-end:var(--dojo-sidebar-dark); }', html)

    def test_buttons_follow_the_accent_colour_not_the_sidebar(self):
        self.save(sidebar_color='#123456', accent_color='#E11D48')
        html = self.page()
        self.assertIn('--dojo-accent: #E11D48;', html)
        self.assertIn('.btn-dark { background: var(--dojo-accent) !important;', html)

    def test_button_text_is_black_or_white_whichever_reads_better(self):
        self.save(accent_color='#FACC15')  # yellow: black text
        html = self.page()
        self.assertIn('--dojo-accent-text: #000000;', html)
        self.assertIn('color: var(--dojo-accent-text) !important;', html)
        self.save(accent_color='#1E3A5F')  # navy: white text
        self.assertIn('--dojo-accent-text: #ffffff;', self.page())


class ReadableTextTests(TestCase):
    def test_picks_the_higher_contrast(self):
        from django.template.loader import render_to_string
        from organisations.branding import readable_text
        self.assertEqual(readable_text('#ffffff'), '#000000')
        self.assertEqual(readable_text('#000000'), '#ffffff')
        self.assertEqual(readable_text('#FACC15'), '#000000')
        self.assertEqual(readable_text('#2563EB'), '#ffffff')
        self.assertEqual(readable_text('not a colour'), '#ffffff')
        # Email buttons too.
        html = render_to_string('emails/blocks/button.html', {'text': 'Go', 'url': 'https://x', 'accent': '#FACC15'})
        self.assertIn('background:#FACC15;color:#000000;', html)
