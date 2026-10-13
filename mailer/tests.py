import io
import json
from unittest import mock

import dkim
from django.contrib.auth.models import User
from django.core import mail
from django.core.mail import EmailMultiAlternatives, get_connection
from django.core.mail.backends.smtp import EmailBackend as SmtpBackend
from django.test import TestCase, override_settings
from django.urls import reverse

from mailer import connection_for, crypto
from mailer.backends import DkimSmtpBackend, SendError, backend_for
from mailer.dkim_keys import dns_record_value
from mailer.forms import apply_post
from mailer.models import EmailSettings
from members.emails import send_welcome_email
from members.models import Member
from organisations.models import Organisation, OrganisationMember

CLUB_BACKEND = 'mailer.backends.ClubBackend'
LOCMEM = 'django.core.mail.backends.locmem.EmailBackend'


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def capture_api_calls():
    """Patch urlopen; returns the list the requests are recorded in."""
    calls = []

    def fake_urlopen(request, timeout):
        calls.append(request)
        return FakeResponse(b'{}')
    patcher = mock.patch('urllib.request.urlopen', side_effect=fake_urlopen)
    return patcher, calls


class MailerTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organisation.objects.create(name='Test Club', slug='test-club', email='hello@testclub.example')
        cls.admin = User.objects.create_user('admin', password='pw', email='admin@testclub.example')
        OrganisationMember.objects.create(user=cls.admin, organisation=cls.org, role='org_admin')

    def configure(self, provider, from_email='noreply@testclub.example', **values):
        email_settings = EmailSettings.objects.filter(organisation=self.org).first() or EmailSettings(organisation=self.org)
        post = {'email_provider': provider, 'email_from_email': from_email,
                **{f'email_{provider}_{k}': v for k, v in values.items()}}
        errors = apply_post(email_settings, post)
        self.assertEqual(errors, [])
        email_settings.save()
        return email_settings

    def message(self, **kwargs):
        msg = EmailMultiAlternatives('Hello', 'Plain body', to=['member@example.com'],
                                     connection=connection_for(self.org), **kwargs)
        msg.attach_alternative('<p>HTML body</p>', 'text/html')
        return msg


class SecretTests(MailerTestCase):
    def test_round_trip_and_unreadable_after_secret_key_change(self):
        token = crypto.encrypt_dict({'api_key': 'xkeysib-123'})
        self.assertNotIn('xkeysib', token)
        self.assertEqual(crypto.decrypt_dict(token), {'api_key': 'xkeysib-123'})
        with override_settings(SECRET_KEY='a-different-key'):
            self.assertEqual(crypto.decrypt_dict(token), {})

    def test_saved_keys_are_never_sent_back_to_the_browser(self):
        self.configure('brevo', api_key='xkeysib-secret-value')
        self.client.force_login(self.admin)
        html = self.client.get(reverse('org_settings', kwargs={'org_slug': self.org.slug})).content.decode()
        self.assertNotIn('xkeysib-secret-value', html)
        self.assertIn('Saved: leave blank to keep', html)
        self.assertNotIn('xkeysib', EmailSettings.objects.get().secrets)


class FormTests(MailerTestCase):
    def test_each_provider_needs_its_settings(self):
        email_settings = EmailSettings(organisation=self.org)
        errors = apply_post(email_settings, {'email_provider': 'brevo', 'email_from_email': 'a@b.example'})
        self.assertEqual(errors, ['Brevo: enter the api key.'])
        errors = apply_post(email_settings, {'email_provider': 'smtp', 'email_from_email': 'nope'})
        self.assertIn('From address isn\'t a valid email address.', errors)
        self.assertIn('Another SMTP server: enter the server.', errors)
        self.assertEqual(apply_post(email_settings, {'email_provider': 'carrier-pigeon'}), ['Choose how Dojo should send email.'])
        self.assertEqual(apply_post(email_settings, {'email_provider': 'log'}), [])

    def test_blank_password_keeps_the_saved_one_until_the_provider_changes(self):
        email_settings = self.configure('brevo', api_key='key-1')
        self.configure('brevo')
        self.assertEqual(email_settings.value('api_key'), 'key-1')
        email_settings.refresh_from_db()
        errors = apply_post(email_settings, {'email_provider': 'resend', 'email_from_email': 'a@b.example'})
        self.assertEqual(errors, ['Resend: enter the api key.'])

    def test_dkim_key_survives_changing_provider(self):
        email_settings = self.configure('dojo_mail')
        key = email_settings.dkim_private_key(create=True)
        self.configure('brevo', api_key='k')
        self.configure('dojo_mail')
        email_settings.refresh_from_db()
        self.assertEqual(email_settings.dkim_private_key(), key)


class SettingsPageTests(MailerTestCase):
    def setUp(self):
        self.client.force_login(self.admin)
        self.url = reverse('org_settings', kwargs={'org_slug': self.org.slug})

    def test_save_from_settings(self):
        response = self.client.post(self.url, {
            'action': 'email', 'email_provider': 'smtp', 'email_from_email': 'noreply@testclub.example',
            'email_smtp_host': 'smtp.example.net', 'email_smtp_port': '465', 'email_smtp_security': 'ssl',
            'email_smtp_username': 'user', 'email_smtp_password': 'pass',
        })
        self.assertTrue(response['Location'].endswith('#email'))
        email_settings = EmailSettings.objects.get()
        self.assertEqual((email_settings.provider, email_settings.options['host']), ('smtp', 'smtp.example.net'))
        self.assertEqual(email_settings.value('password'), 'pass')

    def test_errors_change_nothing(self):
        self.configure('log')
        self.client.post(self.url, {'action': 'email', 'email_provider': 'brevo', 'email_from_email': 'x@y.example'})
        self.assertEqual(EmailSettings.objects.get().provider, 'log')

    def test_own_mail_service_makes_a_dkim_key_and_shows_dns_records(self):
        self.client.post(self.url, {'action': 'email', 'email_provider': 'dojo_mail',
                                    'email_from_email': 'noreply@testclub.example'})
        email_settings = EmailSettings.objects.get()
        self.assertTrue(email_settings.dkim_private_key())
        html = self.client.get(self.url).content.decode()
        self.assertIn('dojo._domainkey.testclub.example', html)
        self.assertIn(dns_record_value(email_settings.dkim_private_key()), html)
        self.assertIn('_dmarc.testclub.example', html)

    def test_dns_check_reports_each_record(self):
        email_settings = self.configure('dojo_mail')
        value = dns_record_value(email_settings.dkim_private_key(create=True))
        published = {'dojo._domainkey.testclub.example': [value]}
        with mock.patch('mailer.dns_check.txt_records', side_effect=lambda name: published.get(name, [])):
            response = self.client.get(self.url + '?check_dns=1')
        self.assertEqual(response.context['email_dns_check'], {
            'dojo._domainkey.testclub.example': True, 'testclub.example': False, '_dmarc.testclub.example': False,
        })

    def test_coaches_cannot_change_email_settings(self):
        coach = User.objects.create_user('coach', password='pw')
        OrganisationMember.objects.create(user=coach, organisation=self.org, role='coach')
        self.client.force_login(coach)
        self.client.post(self.url, {'action': 'email', 'email_provider': 'log'})
        self.assertFalse(EmailSettings.objects.exists())


@override_settings(EMAIL_BACKEND=CLUB_BACKEND, DOJO_FALLBACK_EMAIL_BACKEND=LOCMEM)
class SendingTests(MailerTestCase):
    def test_no_choice_yet_uses_the_server_fallback(self):
        self.message().send()
        self.assertEqual(len(mail.outbox), 1)

    def test_not_yet_writes_to_the_log_instead_of_sending(self):
        self.configure('log', from_email='')
        with mock.patch('sys.stdout', new_callable=io.StringIO) as stdout:
            self.message().send()
        self.assertIn('Subject: Hello', stdout.getvalue())
        self.assertEqual(mail.outbox, [])

    def test_brevo(self):
        self.configure('brevo', api_key='brevo-key')
        patcher, calls = capture_api_calls()
        with patcher:
            self.message().send()
        request = calls[0]
        self.assertEqual(request.full_url, 'https://api.brevo.com/v3/smtp/email')
        self.assertEqual(request.headers['Api-key'], 'brevo-key')
        body = json.loads(request.data)
        self.assertEqual(body['sender'], {'name': 'Test Club', 'email': 'noreply@testclub.example'})
        self.assertEqual(body['to'], [{'email': 'member@example.com'}])
        self.assertEqual((body['textContent'], body['htmlContent']), ('Plain body', '<p>HTML body</p>'))
        self.assertEqual(body['replyTo'], {'email': 'hello@testclub.example'})

    def test_postmark_resend_and_mailgun(self):
        cases = {
            'postmark': ('https://api.postmarkapp.com/email', 'X-postmark-server-token', 'pm-key'),
            'resend': ('https://api.resend.com/emails', 'Authorization', 'Bearer rs-key'),
        }
        for provider, (url, header, expected) in cases.items():
            with self.subTest(provider=provider):
                self.configure(provider, api_key=expected.replace('Bearer ', ''))
                patcher, calls = capture_api_calls()
                with patcher:
                    self.message().send()
                self.assertEqual(calls[0].full_url, url)
                self.assertEqual(calls[0].headers[header], expected)
                self.assertIn('HTML body', calls[0].data.decode())
        self.configure('mailgun', api_key='mg-key', domain='mg.testclub.example', region='eu')
        patcher, calls = capture_api_calls()
        with patcher:
            self.message().send()
        self.assertEqual(calls[0].full_url, 'https://api.eu.mailgun.net/v3/mg.testclub.example/messages')
        self.assertIn('subject=Hello', calls[0].data.decode())

    def test_a_refused_email_raises_a_readable_error(self):
        import urllib.error
        self.configure('brevo', api_key='bad')
        error = urllib.error.HTTPError('u', 401, 'Unauthorized', {}, io.BytesIO(b'{"message":"Key not found"}'))
        with mock.patch('mailer.backends.urllib.request.urlopen', side_effect=error):
            with self.assertRaisesMessage(SendError, 'Brevo refused the email (401): {"message":"Key not found"}'):
                self.message().send()
            quiet = EmailMultiAlternatives('Hello', 'Body', to=['member@example.com'],
                                           connection=get_connection(organisation=self.org, fail_silently=True))
            self.assertEqual(quiet.send(), 0)

    def test_smtp_based_providers(self):
        cases = {
            'ses': ({'region': 'eu-west-1', 'username': 'u', 'password': 'p'}, 'email-smtp.eu-west-1.amazonaws.com', 587, True, False),
            'gmail': ({'username': 'club@gmail.com', 'password': 'p'}, 'smtp.gmail.com', 587, True, False),
            'microsoft365': ({'username': 'club@x.example', 'password': 'p'}, 'smtp.office365.com', 587, True, False),
            'smtp': ({'host': 'mail.x.example', 'port': '465', 'security': 'ssl'}, 'mail.x.example', 465, False, True),
        }
        for provider, (values, host, port, tls, ssl) in cases.items():
            with self.subTest(provider=provider):
                backend = backend_for(self.configure(provider, **values))
                self.assertIsInstance(backend, SmtpBackend)
                self.assertEqual((backend.host, backend.port, backend.use_tls, backend.use_ssl), (host, port, tls, ssl))

    def test_club_emails_use_the_club_choice(self):
        self.configure('brevo', api_key='k')
        member = Member.objects.create(organisation=self.org, name='Ann Lee', email='ann@example.com')
        patcher, calls = capture_api_calls()
        with patcher:
            send_welcome_email(member)
        self.assertEqual(json.loads(calls[0].data)['to'], [{'email': 'ann@example.com'}])

    def test_test_email_button_uses_the_club_choice(self):
        self.configure('brevo', api_key='k')
        self.client.force_login(self.admin)
        patcher, calls = capture_api_calls()
        with patcher:
            response = self.client.post(reverse('org_test_email', kwargs={'org_slug': self.org.slug}), follow=True)
        self.assertContains(response, 'Test email sent to admin@testclub.example')
        self.assertEqual(len(calls), 1)

    def test_each_club_on_a_shared_server_uses_its_own_settings(self):
        other = Organisation.objects.create(name='Other Club', slug='other-club')
        EmailSettings.objects.create(organisation=other, provider='log')
        self.configure('brevo', api_key='k')
        patcher, calls = capture_api_calls()
        with patcher:
            self.message().send()
        self.assertEqual(len(calls), 1)


class DkimTests(MailerTestCase):
    def test_own_mail_service_signs_each_email(self):
        email_settings = self.configure('dojo_mail')
        email_settings.dkim_private_key(create=True)
        backend = backend_for(email_settings)
        self.assertIsInstance(backend, DkimSmtpBackend)
        self.assertEqual((backend.host, backend.port), ('mail', 25))
        backend.connection = mock.Mock()
        msg = EmailMultiAlternatives('Hi', 'Body', 'Test Club <noreply@testclub.example>', ['m@example.com'])
        self.assertTrue(backend._send(msg))
        sent = backend.connection.sendmail.call_args.args[2]
        self.assertTrue(sent.startswith(b'DKIM-Signature:'))
        record = dns_record_value(email_settings.dkim_private_key()).encode()
        self.assertTrue(dkim.verify(sent, dnsfunc=lambda name, timeout=5: record))


class SetupWizardTests(TestCase):
    def post(self, **extra):
        return self.client.post(reverse('setup'), {
            'org_name': 'New Club', 'username': 'founder', 'admin_email': 'f@example.com',
            'password': 'a-long-password', 'password_confirm': 'a-long-password', **extra,
        })

    def test_wizard_offers_the_choice(self):
        self.assertContains(self.client.get(reverse('setup')), 'How will Dojo send email?')

    def test_choosing_a_provider_at_setup(self):
        self.post(email_provider='brevo', email_from_email='noreply@newclub.example', email_brevo_api_key='k')
        email_settings = EmailSettings.objects.get()
        self.assertEqual((email_settings.organisation.name, email_settings.provider), ('New Club', 'brevo'))
        self.assertEqual(email_settings.value('api_key'), 'k')

    def test_a_mistake_creates_nothing_and_keeps_the_answers(self):
        response = self.post(email_provider='smtp', email_from_email='noreply@newclub.example',
                             email_smtp_host='')
        self.assertContains(response, 'Another SMTP server: enter the server.')
        self.assertContains(response, 'value="noreply@newclub.example"')
        self.assertFalse(Organisation.objects.exists())

    def test_not_yet_leaves_the_server_settings_in_charge(self):
        self.post()
        self.assertTrue(Organisation.objects.exists())
        self.assertFalse(EmailSettings.objects.exists())
        self.client.force_login(User.objects.get(username='founder'))
        org = Organisation.objects.get()
        org.settings = {**org.settings, 'two_factor': 'optional'}  # new clubs require it; not under test here
        org.save()
        response = self.client.get(reverse('org_settings', kwargs={'org_slug': org.slug}))
        self.assertContains(response, "hasn't chosen yet, so Dojo is printing emails")


class ConnectionCheckTests(MailerTestCase):
    def results(self, **kwargs):
        from mailer import connectivity
        return connectivity.check(EmailSettings.objects.get())

    def test_send_from_this_server_checks_the_container_and_port_25(self):
        self.configure('dojo_mail')
        with mock.patch('mailer.connectivity._first_mx', return_value='mx.example'), \
                mock.patch('mailer.connectivity._read_banner', side_effect=['220 mail ESMTP', OSError('timed out')]) as banner:
            results = self.results()
        self.assertEqual([ok for _, ok, _ in results], [True, False])
        self.assertIn('Port 25 looks blocked', results[1][2])
        self.assertEqual(banner.call_args_list[1].args, ('mx.example', 25))

    def test_smtp_login_is_tried_with_the_saved_details(self):
        import smtplib
        self.configure('smtp', host='smtp.x.example', port='587', security='starttls', username='u', password='p')
        with mock.patch('mailer.connectivity.smtplib.SMTP') as smtp:
            self.assertTrue(self.results()[0][1])
            smtp.return_value.login.assert_called_once_with('u', 'p')
            smtp.return_value.login.side_effect = smtplib.SMTPAuthenticationError(535, b'bad')
            _label, ok, detail = self.results()[0]
        self.assertFalse(ok)
        self.assertIn('didn\'t accept the username and password', detail)

    def test_api_key_is_checked_without_sending(self):
        import urllib.error
        self.configure('brevo', api_key='k')
        patcher, calls = capture_api_calls()
        with patcher:
            self.assertTrue(self.results()[0][1])
        self.assertEqual(calls[0].full_url, 'https://api.brevo.com/v3/account')
        self.assertEqual(calls[0].get_method(), 'GET')
        refused = urllib.error.HTTPError('u', 401, 'no', {}, io.BytesIO(b''))
        with mock.patch('mailer.connectivity.urllib.request.urlopen', side_effect=refused):
            self.assertIn('didn\'t accept the key', self.results()[0][2])

    def test_button_on_the_email_tab(self):
        self.configure('brevo', api_key='k')
        self.client.force_login(self.admin)
        url = reverse('org_settings', kwargs={'org_slug': self.org.slug})
        self.assertContains(self.client.get(url), 'Check connection')
        patcher, _ = capture_api_calls()
        with patcher:
            response = self.client.get(url + '?check_connection=1')
        self.assertContains(response, 'Connected, and the key was accepted.')
