import json
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from billing.emails import send_invoice_email
from billing.models import Invoice
from mailer import blocks, catalogue, compose
from mailer.models import EmailTemplate
from members.emails import send_welcome_email
from members.models import Member
from organisations.models import Organisation, OrganisationMember


@override_settings(SITE_URL='https://club.example', STRIPE_PUBLIC_KEY='', STRIPE_SECRET_KEY='')
class TemplatesTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organisation.objects.create(name='Forest Judo', slug='forest', email='hello@forest.example')
        cls.admin = User.objects.create_user('admin', password='pw', email='admin@forest.example')
        OrganisationMember.objects.create(user=cls.admin, organisation=cls.org, role='org_admin')
        cls.member = Member.objects.create(organisation=cls.org, name='Ann Lee', email='ann@example.com')

    def customise(self, key, **fields):
        return EmailTemplate.objects.create(organisation=self.org, key=key, **{
            'subject': 'Custom subject', 'mode': 'wording', 'body': '', 'html': '', **fields})

    def sent_html(self):
        return mail.outbox[-1].alternatives[0][0]


class WordingTests(TemplatesTestCase):
    def render(self, body, values=None, email_blocks=None):
        return compose.render(self.org, 'welcome', values or {'greeting': 'Hi Ann', 'portal_link': 'https://x.example/p/1'},
                              email_blocks or {}, subject='S', mode='wording', body=body)

    def test_formatting(self):
        _, html, text = self.render('Hello **{greeting}**\nnext line\n\nSee [the site](https://club.example/a).\n\n'
                                    '[button: Open portal]({portal_link})')
        self.assertIn('Hello <strong>Hi Ann</strong><br>next line', html)
        self.assertIn('<a href="https://club.example/a" style="color:', html)
        self.assertIn('href="https://x.example/p/1"', html)
        self.assertIn('>Open portal</a>', html)
        self.assertIn('Hello Hi Ann\nnext line', text)
        self.assertIn('See the site (https://club.example/a).', text)
        self.assertIn('Open portal: https://x.example/p/1', text)
        self.assertTrue(text.endswith('Powered by Dojo'))

    def test_club_wording_cannot_inject_html_or_scripts(self):
        _, html, _ = self.render('<script>alert(1)</script> [click](javascript:alert(1)) <b>x</b>')
        self.assertNotIn('<script>alert', html)
        self.assertNotIn('javascript:', html)
        self.assertNotIn('<b>x</b>', html)
        self.assertIn('&lt;script&gt;', html)

    def test_values_are_escaped_and_unknown_fields_left_visible(self):
        _, html, _ = self.render('{greeting} {nonsense}', {'greeting': '<img src=x onerror=alert(1)>'})
        self.assertIn('&lt;img src=x onerror=alert(1)&gt;', html)
        self.assertIn('{nonsense}', html)

    def test_fields_are_swaps_not_template_code(self):
        _, html, _ = self.render('{{ settings.SECRET_KEY }} {% debug %} {member.organisation.email_settings}')
        self.assertIn('{{ settings.SECRET_KEY }}', html)
        self.assertIn('{% debug %}', html)
        self.assertIn('{member.organisation.email_settings}', html)

    def test_blocks_on_their_own_line(self):
        block = {'portal_button': {'html': '<table id="portal-button"></table>', 'text': 'Portal: link'}}
        _, html, text = self.render('Before\n\n{portal_button}\n\nAfter', email_blocks=block)
        self.assertIn('<table id="portal-button"></table>', html)
        self.assertIn('Portal: link', text)


class SendingTests(TemplatesTestCase):
    def test_defaults_until_customised(self):
        send_welcome_email(self.member)
        self.assertEqual(mail.outbox[-1].subject, 'Welcome to Forest Judo')
        self.assertIn('Welcome to <strong>Forest Judo</strong>', self.sent_html())

    def test_customised_wording_is_used(self):
        self.customise('welcome', subject='Oss {first_name}, welcome to {club_name}',
                       body='{greeting}, see you on the mat!\n\n{portal_button}')
        send_welcome_email(self.member)
        self.assertEqual(mail.outbox[-1].subject, 'Oss Ann, welcome to Forest Judo')
        self.assertIn('Hi Ann, see you on the mat!', self.sent_html())
        self.assertIn('Go to member portal', self.sent_html())
        self.assertIn('Hi Ann, see you on the mat!', mail.outbox[-1].body)

    def test_full_html_is_used_as_written(self):
        self.customise('invoice', subject='{period} invoice', mode='html',
                       html='<html><body><h1>{club_name}</h1><p>{greeting}</p>{pay_options}</body></html>')
        invoice = Invoice.objects.create(organisation=self.org, member=self.member, amount=Decimal(30),
                                         period='June 2026', due_date=date(2026, 6, 28))
        send_invoice_email(invoice)
        html = self.sent_html()
        self.assertTrue(html.startswith('<html><body><h1>Forest Judo</h1><p>Hi Ann</p>'))
        self.assertIn('How to pay', html)  # the block was rendered in place
        self.assertNotIn('Powered by', html)  # nothing added around full HTML
        self.assertIn('Forest Judo', mail.outbox[-1].body)

    def test_one_clubs_template_never_reaches_another_club(self):
        other = Organisation.objects.create(name='Other Club', slug='other')
        EmailTemplate.objects.create(organisation=other, key='welcome', subject='Other club only', body='x')
        send_welcome_email(self.member)
        self.assertEqual(mail.outbox[-1].subject, 'Welcome to Forest Judo')

    def test_layout_options(self):
        self.org.settings = {'email_layout': {'header_colour': '#123456', 'show_logo': False,
                                              'header_text': 'FJ Club News', 'footer_text': 'Forest Judo, Park Lane'}}
        self.org.save()
        send_welcome_email(self.member)
        html = self.sent_html()
        self.assertIn('background:#123456', html)
        self.assertIn('FJ Club News', html)
        self.assertIn('Forest Judo, Park Lane', html)
        self.assertIn('Powered by', html)
        self.assertIn('Forest Judo, Park Lane', mail.outbox[-1].body)

    def test_every_default_renders_with_no_fields_left(self):
        for email_type in catalogue.EMAIL_TYPES:
            with self.subTest(email=email_type.key):
                values, example_blocks = blocks.example(self.org, email_type.key)
                subject, html, text = compose.render(self.org, email_type.key, values, example_blocks)
                self.assertEqual(compose.FIELD.findall(subject + html + text), [])


class EditorTests(TemplatesTestCase):
    def setUp(self):
        self.client.force_login(self.admin)
        self.url = reverse('org_email_template_edit', kwargs={'org_slug': self.org.slug, 'key': 'welcome'})

    def test_list_shows_each_email_and_status(self):
        self.customise('invoice')
        html = self.client.get(reverse('org_email_templates', kwargs={'org_slug': self.org.slug})).content.decode()
        for email_type in catalogue.EMAIL_TYPES:
            self.assertIn(email_type.label, html)
        self.assertIn('Customised', html)

    def test_save_reset_and_warn_about_unknown_fields(self):
        response = self.client.post(self.url, {'action': 'save', 'subject': 'Hi {frist_name}', 'mode': 'wording',
                                               'body': 'Welcome!'}, follow=True)
        self.assertContains(response, 'appear exactly as typed: {frist_name}')
        self.assertEqual(EmailTemplate.objects.get().subject, 'Hi {frist_name}')
        self.client.post(self.url, {'action': 'reset'})
        self.assertFalse(EmailTemplate.objects.exists())

    def test_empty_email_is_refused(self):
        response = self.client.post(self.url, {'action': 'save', 'subject': 'Hi', 'mode': 'wording', 'body': '  '})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(EmailTemplate.objects.exists())

    def test_preview_and_start_from_design(self):
        preview_url = reverse('org_email_template_preview', kwargs={'org_slug': self.org.slug, 'key': 'welcome'})
        data = json.loads(self.client.post(preview_url, {'subject': 'Welcome {first_name}', 'mode': 'wording',
                                                         'body': '{greeting}!\n\n{portal_button}'}).content)
        self.assertEqual(data['subject'], 'Welcome Ann')
        self.assertIn('Hi Ann!', data['html'])
        design = json.loads(self.client.post(preview_url, {'subject': 'x', 'mode': 'html', 'body': '{greeting}!\n\n{portal_button}',
                                                           'start_from_design': '1'}).content)['html']
        self.assertIn('{greeting}!', design)
        self.assertIn('{portal_button}', design)

    def test_send_me_a_test_uses_example_details_and_saves_nothing(self):
        self.client.post(self.url, {'action': 'test', 'subject': 'Hello {first_name}', 'mode': 'wording', 'body': 'Test body'})
        self.assertEqual(mail.outbox[-1].subject, '[Test] Hello Ann')
        self.assertEqual(mail.outbox[-1].to, ['admin@forest.example'])
        self.assertFalse(EmailTemplate.objects.exists())

    def test_layout_form(self):
        url = reverse('org_email_templates', kwargs={'org_slug': self.org.slug})
        self.client.post(url, {'header_colour': '#123456', 'show_logo': '1', 'footer_text': 'Bye'})
        self.org.refresh_from_db()
        self.assertEqual(self.org.settings['email_layout']['header_colour'], '#123456')
        self.client.post(url, {'header_colour': 'red; background:url(x)', 'show_logo': '1'})
        self.org.refresh_from_db()
        self.assertEqual(self.org.settings['email_layout']['header_colour'], '#123456')  # refused, unchanged

    def test_coaches_and_other_clubs_cannot_edit(self):
        coach = User.objects.create_user('coach', password='pw')
        OrganisationMember.objects.create(user=coach, organisation=self.org, role='coach')
        self.client.force_login(coach)
        self.client.post(self.url, {'action': 'save', 'subject': 'x', 'mode': 'wording', 'body': 'x'})
        other = Organisation.objects.create(name='Other', slug='other')
        outsider = User.objects.create_user('outsider', password='pw')
        OrganisationMember.objects.create(user=outsider, organisation=other, role='org_admin')
        self.client.force_login(outsider)
        self.client.post(self.url, {'action': 'save', 'subject': 'x', 'mode': 'wording', 'body': 'x'})
        self.assertFalse(EmailTemplate.objects.exists())

    def test_unknown_email_is_404(self):
        self.assertEqual(self.client.get(reverse('org_email_template_edit',
                                                 kwargs={'org_slug': self.org.slug, 'key': 'nope'})).status_code, 404)
