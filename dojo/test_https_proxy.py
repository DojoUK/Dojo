import os
import subprocess
import sys

from django.test import SimpleTestCase, override_settings
from django.urls import reverse


def settings_value(name, **env):
    """Read a setting from a fresh interpreter, so the environment is applied at import."""
    script = f'import django; django.setup(); from django.conf import settings; print(repr(getattr(settings, {name!r}, None)))'
    result = subprocess.run(
        [sys.executable, '-c', script],
        env={**os.environ, 'DJANGO_SETTINGS_MODULE': 'dojo.settings', **env},
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


class TrustProxyHttpsSettingTests(SimpleTestCase):
    def test_off_unless_switched_on(self):
        self.assertEqual(settings_value('SECURE_PROXY_SSL_HEADER', TRUST_PROXY_HTTPS=''), 'None')
        self.assertEqual(settings_value('SECURE_PROXY_SSL_HEADER', TRUST_PROXY_HTTPS='no'), 'None')

    def test_switched_on_trusts_forwarded_proto(self):
        self.assertEqual(
            settings_value('SECURE_PROXY_SSL_HEADER', TRUST_PROXY_HTTPS='true'),
            "('HTTP_X_FORWARDED_PROTO', 'https')",
        )

    def test_https_site_url_trusts_its_own_origin_for_csrf(self):
        self.assertEqual(
            settings_value('CSRF_TRUSTED_ORIGINS', SITE_URL='https://dojo.example.org/'),
            "['https://dojo.example.org']",
        )


@override_settings(SECURE_SSL_REDIRECT=True, ALLOWED_HOSTS=['testserver'])
class BehindProxyRequestTests(SimpleTestCase):
    def test_without_trust_the_https_redirect_loops(self):
        response = self.client.get(reverse('login'), HTTP_X_FORWARDED_PROTO='https')
        self.assertEqual(response.status_code, 301)

    @override_settings(SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO', 'https'))
    def test_with_trust_a_proxied_https_request_is_served(self):
        response = self.client.get(reverse('login'), HTTP_X_FORWARDED_PROTO='https')
        self.assertEqual(response.status_code, 200)
