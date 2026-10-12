import io
import shutil
import tempfile

from django.contrib.auth.models import User
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from members.emails import send_welcome_email
from members.models import Member
from organisations.branding import backdrop_style, email_branding, validate_logo
from organisations.models import Organisation, OrganisationMember

MEDIA = tempfile.mkdtemp()


def image_file(colour, fmt='PNG', size=(40, 20), name='logo.png'):
    buffer = io.BytesIO()
    Image.new('RGBA' if fmt == 'PNG' else 'RGB', size, colour).save(buffer, fmt)
    return SimpleUploadedFile(name, buffer.getvalue(), content_type=f'image/{fmt.lower()}')


class LogoCheckTests(TestCase):
    def test_accepts_png_jpeg_and_webp(self):
        for fmt in ('PNG', 'JPEG', 'WEBP'):
            with self.subTest(fmt=fmt):
                self.assertIsNone(validate_logo(image_file('red', fmt)))

    def test_rejects_svg_text_gif_and_oversize(self):
        svg = SimpleUploadedFile('logo.svg', b'<svg xmlns="http://www.w3.org/2000/svg"></svg>')
        text = SimpleUploadedFile('logo.png', b'not an image')
        self.assertIn('isn\'t an image', validate_logo(svg))
        self.assertIn('isn\'t an image', validate_logo(text))
        self.assertIn('PNG, JPG or WebP', validate_logo(image_file('red', 'GIF', name='logo.gif')))
        self.assertIn('too large', validate_logo(image_file('red', size=(4100, 10))))
        big = SimpleUploadedFile('logo.png', b'0' * (2 * 1024 * 1024 + 1))
        self.assertIn('2MB', validate_logo(big))

    def test_backing_only_when_logo_blends_in(self):
        self.assertEqual(backdrop_style(0.1, '#1E3A5F'), 'background:#ffffff;padding:3px;border-radius:6px;')
        self.assertEqual(backdrop_style(0.9, '#ffffff'), 'background:#1f2937;padding:3px;border-radius:6px;')
        self.assertEqual(backdrop_style(0.95, '#1E3A5F'), '')  # white logo on the dark sidebar
        self.assertEqual(backdrop_style(0.05, '#ffffff'), '')  # black logo on paper
        self.assertEqual(backdrop_style(None, '#000000'), '')


@override_settings(MEDIA_ROOT=MEDIA, SITE_URL='https://club.example')
class LogoUploadTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    @classmethod
    def setUpTestData(cls):
        cls.org = Organisation.objects.create(name='Test Club', slug='test-club')
        cls.admin = User.objects.create_user('admin', password='pw')
        OrganisationMember.objects.create(user=cls.admin, organisation=cls.org, role='org_admin')

    def setUp(self):
        self.client.force_login(self.admin)
        self.url = reverse('org_settings', kwargs={'org_slug': self.org.slug})

    def upload(self, logo):
        return self.client.post(self.url, {
            'action': 'theme', 'sidebar_color': '#1E3A5F', 'accent_color': '#2563EB', 'logo': logo,
        })

    def test_dark_logo_gets_a_light_backing_in_the_sidebar(self):
        response = self.upload(image_file('black'))
        self.assertTrue(response['Location'].endswith('#appearance'))
        self.org.refresh_from_db()
        self.assertEqual(self.org.settings['logo_luminance'], 0.0)
        html = self.client.get(self.url).content.decode()
        self.assertIn(self.org.logo.url, html)
        self.assertIn('object-fit:contain', html)  # shown whole, not cropped
        self.assertIn('background:#ffffff', html)

    def test_light_logo_needs_no_backing_in_the_sidebar(self):
        self.upload(image_file('white'))
        html = self.client.get(self.url).content.decode()
        sidebar_logo = html[html.index('<img'):].split('>', 1)[0]
        self.assertNotIn('background:', sidebar_logo)

    def test_bad_upload_is_refused_and_keeps_the_old_logo(self):
        self.upload(image_file('white'))
        self.org.refresh_from_db()
        old = self.org.logo.name
        response = self.upload(SimpleUploadedFile('logo.svg', b'<svg></svg>'))
        self.assertTrue(response['Location'].endswith('#appearance'))
        self.org.refresh_from_db()
        self.assertEqual(self.org.logo.name, old)

    def test_removing_the_logo_forgets_its_brightness(self):
        self.upload(image_file('white'))
        self.client.post(self.url, {
            'action': 'theme', 'sidebar_color': '#1E3A5F', 'accent_color': '#2563EB', 'remove_logo': '1',
        })
        self.org.refresh_from_db()
        self.assertFalse(self.org.logo)
        self.assertNotIn('logo_luminance', self.org.settings)
        self.assertEqual(email_branding(self.org), {'org_logo_url': '', 'org_logo_backdrop': ''})

    def test_older_logo_is_measured_when_first_shown(self):
        self.upload(image_file('white'))
        self.org.refresh_from_db()
        settings = dict(self.org.settings)
        del settings['logo_luminance']
        Organisation.objects.filter(pk=self.org.pk).update(settings=settings)
        self.org.refresh_from_db()
        self.assertEqual(self.org.logo_luminance, 1.0)
        self.org.refresh_from_db()
        self.assertEqual(self.org.settings['logo_luminance'], 1.0)

    def test_login_page_shows_the_only_club_logo(self):
        self.upload(image_file('white'))
        self.client.logout()
        html = self.client.get(reverse('login')).content.decode()
        self.org.refresh_from_db()
        self.assertIn(self.org.logo.url, html)
        self.assertIn('Test Club', html)

    def test_emails_carry_the_logo_with_a_backing_when_needed(self):
        self.upload(image_file('black'))
        self.org.refresh_from_db()
        member = Member.objects.create(organisation=self.org, name='Ann Lee', email='ann@example.com')
        send_welcome_email(member)
        body = mail.outbox[-1].alternatives[0][0]
        self.assertIn(f'https://club.example{self.org.logo.url}', body)
        self.assertIn('background:#ffffff', body)  # black logo on the dark email header
