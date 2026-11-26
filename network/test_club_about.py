"""A club's description and logo: set in Settings → Location, shown on its Dojo club finder page."""
import shutil
import tempfile

from django.contrib.auth.models import User
from django.test import override_settings
from django.urls import reverse

from network.tests import NetworkTestBase
from organisations.models import Organisation, OrganisationMember
from organisations.test_logo import image_file

MEDIA = tempfile.mkdtemp()


@override_settings(MEDIA_ROOT=MEDIA)
class ClubAboutTests(NetworkTestBase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def setUp(self):
        self.admin = User.objects.create_user('owner', password='pw')
        OrganisationMember.objects.create(user=self.admin, organisation=self.cardiff, role='org_admin')
        self.settings_url = reverse('org_settings', kwargs={'org_slug': self.cardiff.slug})
        self.page_url = reverse('network_club', kwargs={'slug': self.cardiff.slug})

    def save(self, **post):
        self.client.force_login(self.admin)
        response = self.client.post(self.settings_url, {'action': 'club_page', **post})
        self.assertRedirects(response, self.settings_url + '#location', fetch_redirect_response=False)
        self.cardiff.refresh_from_db()
        self.client.logout()
        return response

    def test_the_settings_box_is_in_location(self):
        self.client.force_login(self.admin)
        page = self.client.get(self.settings_url)
        self.assertContains(page, 'Your club page')
        self.assertContains(page, 'name="about"')
        self.assertContains(page, 'value="club_page"')
        self.assertContains(page, 'enctype="multipart/form-data" id="clubPageForm"')

    def test_description_and_logo_show_under_the_contact_details(self):
        self.save(about='Friendly judo for all ages.\r\n\r\nFirst session free.', logo=image_file('red'))
        self.assertEqual(self.cardiff.about, 'Friendly judo for all ages.\n\nFirst session free.')
        self.assertTrue(self.cardiff.logo)
        html = self.client.get(self.page_url).content.decode()
        self.assertIn('About Cardiff Judo', html)
        self.assertIn('<p>Friendly judo for all ages.</p>\n\n<p>First session free.</p>', html)
        self.assertIn(self.cardiff.logo.url, html)
        self.assertLess(html.index('Where they train'), html.index('id="about"'))
        self.assertLess(html.index('id="about"'), html.index('Apply to join'))   # About, then the join card
        self.assertLess(html.index('id="about"'), html.index('id="reviews"'))
        self.assertEqual(html.count(self.cardiff.logo.url), 1)   # no longer also at the top of the contact card

    def test_text_is_shown_as_text(self):
        self.save(about='<script>alert(1)</script> & co')
        page = self.client.get(self.page_url)
        self.assertContains(page, '&lt;script&gt;alert(1)&lt;/script&gt; &amp; co')
        self.assertNotContains(page, '<script>alert(1)</script>')

    def test_nothing_to_show_means_no_box(self):
        self.assertNotContains(self.client.get(self.page_url), 'id="about"')

    def test_too_long_is_refused(self):
        self.save(about='x' * (Organisation.ABOUT_MAX_LENGTH + 1))
        self.assertEqual(self.cardiff.about, '')

    def test_a_bad_logo_is_refused_but_the_text_kept(self):
        self.save(about='Kept.', logo=image_file('red', name='logo.gif', fmt='GIF'))
        self.assertEqual(self.cardiff.about, 'Kept.')
        self.assertFalse(self.cardiff.logo)

    def test_the_logo_can_be_removed(self):
        self.save(about='', logo=image_file('red'))
        self.save(about='', remove_logo='1')
        self.assertFalse(self.cardiff.logo)
        self.assertNotIn('logo_luminance', self.cardiff.settings or {})

    def test_only_the_clubs_admins_can_change_it(self):
        other = User.objects.create_user('someone', password='pw')
        self.client.force_login(other)
        self.client.post(self.settings_url, {'action': 'club_page', 'about': 'Hacked'})
        self.cardiff.refresh_from_db()
        self.assertEqual(self.cardiff.about, '')
