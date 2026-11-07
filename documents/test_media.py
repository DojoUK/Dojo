"""
The media door: only club logos and waiver forms are served straight from
/media/. Members' documents and signed waivers are reached only through the
signed-in download views and the member's own portal.
"""
import shutil
import tempfile
from pathlib import Path
from types import ModuleType

from django.test import TestCase, override_settings

from dojo.urls import public_media_patterns


class MediaDoorTests(TestCase):
    def setUp(self):
        self.media = Path(tempfile.mkdtemp())
        for path in ('logos/club.png', 'waiver_templates/rules.pdf',
                     'documents/2026/11/GP letter.pdf', 'signed_waivers/2026/11/signed.pdf'):
            (self.media / path).parent.mkdir(parents=True, exist_ok=True)
            (self.media / path).write_bytes(b'x')
        with override_settings(MEDIA_ROOT=self.media):
            # The routes the dev server adds when DEBUG is on, pointed at this test's files.
            urlconf = ModuleType('media_urls')
            urlconf.urlpatterns = public_media_patterns()
        self.settings_override = override_settings(ROOT_URLCONF=urlconf, MEDIA_ROOT=self.media)
        self.settings_override.enable()

    def tearDown(self):
        self.settings_override.disable()
        shutil.rmtree(self.media, ignore_errors=True)

    def test_logos_and_waiver_forms_are_public(self):
        self.assertEqual(self.client.get('/media/logos/club.png').status_code, 200)
        self.assertEqual(self.client.get('/media/waiver_templates/rules.pdf').status_code, 200)

    def test_documents_and_signed_waivers_are_not(self):
        for path in ('/media/documents/2026/11/GP%20letter.pdf', '/media/signed_waivers/2026/11/signed.pdf'):
            self.assertEqual(self.client.get(path).status_code, 404, path)
        # Climbing out of a public folder is refused too (Django answers 400 for the attempt).
        response = self.client.get('/media/logos/../documents/2026/11/GP%20letter.pdf')
        self.assertIn(response.status_code, (400, 404))
