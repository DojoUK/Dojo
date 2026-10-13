from datetime import date
from decimal import Decimal

from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone

from billing.emails import send_invoice_email
from billing.models import Invoice, Payment
from members.emails import send_welcome_email
from members.models import Member
from organisations.models import Organisation


@override_settings(SITE_URL='https://club.example')
class EmailLayoutTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organisation.objects.create(
            name='Forest Judo', slug='forest', email='hello@forest.example', website='https://forestjudo.example',
            settings={'sidebar_color': '#1A3C34', 'sidebar_color_dark': '#14302a',
                      'accent_color': '#059669', 'accent_hover': '#047857'},
        )
        cls.member = Member.objects.create(organisation=cls.org, name='Ann Lee', email='ann@example.com')

    def html(self):
        return mail.outbox[-1].alternatives[0][0]

    def test_header_in_the_clubs_colour_and_buttons_in_its_accent(self):
        send_welcome_email(self.member)
        html = self.html()
        self.assertIn('background:#1A3C34;padding:28px 32px', html)
        self.assertIn('display:inline-block;background:#059669;', html)

    def test_footer_says_how_to_reply_and_powered_by_dojo(self):
        send_welcome_email(self.member)
        html = self.html()
        self.assertIn('Questions? Just reply, or email <a href="mailto:hello@forest.example"', html)
        self.assertIn('forestjudo.example</a>', html)
        self.assertIn('Powered by', html)
        self.assertIn('src="https://club.example/static/img/dojo-wordmark.png"', html)
        self.assertNotIn('do not reply', html)

    def test_paid_invoice_says_so_instead_of_offering_to_pay_nothing(self):
        invoice = Invoice.objects.create(organisation=self.org, member=self.member, amount=Decimal('30.00'),
                                         period='June 2026', due_date=date(2026, 6, 28))
        Payment.objects.create(invoice=invoice, amount=Decimal('30.00'), paid_at=timezone.now())
        Invoice.objects.filter(pk=invoice.pk).update(status='paid')
        invoice.refresh_from_db()
        send_invoice_email(invoice)
        html = self.html()
        self.assertIn('Paid, thank you.', html)
        self.assertNotIn('£0.00', html)

    def test_session_cancelled_email_uses_the_same_layout(self):
        from mailer import blocks, compose
        subject, html, text = compose.render(self.org, 'session_cancelled', {
            **compose.member_values(self.member), 'class_name': 'Juniors', 'session_date': 'Tuesday 3 November 2026',
        }, {'session_box': blocks.session_box('Juniors', 'Tuesday 3 November 2026')})
        self.assertEqual(subject, 'Juniors on Tuesday 3 November 2026 is cancelled')
        self.assertIn('background:#1A3C34', html)
        self.assertIn('Powered by', html)
        self.assertIn('Tuesday 3 November 2026', html)
        self.assertTrue(text.endswith('Powered by Dojo'))
