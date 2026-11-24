"""Expiring cards: Dojo keeps each saved card's expiry, warns members once before it runs out, follows
Stripe's card updater, and shows failed payments and expiring cards to the club."""
from datetime import date, datetime
from decimal import Decimal
from unittest import mock
from zoneinfo import ZoneInfo

from django.contrib.auth.models import User
from django.core import mail
from django.urls import reverse

from billing import autopay
from billing.models import AutoPay, Invoice
from billing.stripe_setup import WEBHOOK_EVENTS
from billing.test_autopay import AutoPayTestCase, setup_intent, stripe_object
from organisations.models import OrganisationMember

LONDON = ZoneInfo('Europe/London')


def at(day, hour=10):
    return datetime(day.year, day.month, day.day, hour, tzinfo=LONDON)


def card(exp_month, exp_year, pm_id='pm_1', last4='4242'):
    return stripe_object({'id': pm_id, 'object': 'payment_method', 'type': 'card',
                          'card': {'brand': 'visa', 'last4': last4, 'exp_month': exp_month, 'exp_year': exp_year}})


class CardExpiryTestCase(AutoPayTestCase):
    def saved_card(self, expires_on, **extra):
        return AutoPay.objects.create(member=self.member, payment_method_id='pm_1', method_type='card',
                                      label='Visa ending 4242', expires_on=expires_on, **extra)

    def warn(self, day, hour=10):
        return autopay.warn_expiring_cards(now=at(day, hour))


class ExpiryDateTests(CardExpiryTestCase):
    def test_a_card_works_to_the_end_of_its_expiry_month(self):
        self.assertEqual(autopay._expiry(card(2, 2028)), date(2028, 2, 29))
        self.assertEqual(autopay._expiry(card(1, 2027)), date(2027, 1, 31))

    def test_direct_debits_and_broken_details_have_no_expiry(self):
        self.assertIsNone(autopay._expiry(stripe_object({'id': 'pm_2', 'type': 'bacs_debit', 'bacs_debit': {'last4': '6789'}})))
        self.assertIsNone(autopay._expiry(stripe_object({'id': 'pm_3', 'type': 'card', 'card': {'last4': '1111'}})))

    def test_saving_a_card_keeps_its_expiry(self):
        intent = setup_intent(self.member)
        intent.payment_method.card.exp_month = 3
        intent.payment_method.card.exp_year = 2029
        saved = autopay.save_from_setup_intent(self.org, intent)
        self.assertEqual(saved.expires_on, date(2029, 3, 31))
        self.assertIsNone(saved.expiry_warned_for)

    def test_expiry_state(self):
        today = date(2026, 12, 5)
        self.assertEqual(self.saved_card(date(2026, 10, 31)).expiry_state(today), 'expired')
        AutoPay.objects.all().delete()
        self.assertEqual(self.saved_card(date(2026, 12, 31)).expiry_state(today), 'soon')
        AutoPay.objects.all().delete()
        self.assertEqual(self.saved_card(date(2027, 6, 30)).expiry_state(today), '')


class WarningTests(CardExpiryTestCase):
    def test_members_are_warned_once_a_month_before_their_card_expires(self):
        self.saved_card(date(2026, 12, 31))
        self.assertEqual(self.warn(date(2026, 11, 30)), 0)          # 31 days to go: not yet
        self.assertEqual(self.warn(date(2026, 12, 1)), 1)
        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertEqual(email.to, ['ann@example.com'])
        self.assertIn('expires soon', email.subject)
        self.assertIn('Visa ending 4242', email.body)
        self.assertIn('expires at the end of December 2026', email.body)
        link = reverse('member_portal', kwargs={'token': self.member.token}) + '#paying'
        self.assertIn(link, email.alternatives[0][0])
        self.assertEqual(self.warn(date(2026, 12, 2)), 0)            # once per card
        self.assertEqual(AutoPay.objects.get().expiry_warned_for, date(2026, 12, 31))

    def test_a_card_that_has_already_expired_says_so(self):
        self.saved_card(date(2026, 10, 31))
        self.assertEqual(self.warn(date(2026, 11, 24)), 1)
        self.assertIn('has expired', mail.outbox[0].subject)
        self.assertIn('expired at the end of October 2026', mail.outbox[0].body)

    def test_nothing_before_nine_in_the_morning(self):
        self.saved_card(date(2026, 12, 31))
        self.assertEqual(self.warn(date(2026, 12, 10), hour=8), 0)
        self.assertEqual(self.warn(date(2026, 12, 10), hour=9), 1)

    def test_no_warning_for_cards_with_time_left_direct_debits_or_switched_off_autopay(self):
        self.saved_card(date(2027, 6, 30))
        self.assertEqual(self.warn(date(2026, 11, 24)), 0)
        AutoPay.objects.update(method_type='bacs_debit', expires_on=None)
        self.assertEqual(self.warn(date(2026, 11, 24)), 0)
        AutoPay.objects.update(method_type='card', expires_on=date(2026, 11, 30), active=False)
        self.assertEqual(self.warn(date(2026, 11, 24)), 0)
        AutoPay.objects.update(active=True)
        self.configure(auto_pay='off')
        self.assertEqual(self.warn(date(2026, 11, 24)), 0)
        self.assertEqual(mail.outbox, [])

    def test_a_guardian_is_warned_about_a_childs_card(self):
        self.member.guardians.create(name='Pat Lee', email='pat@example.com')
        self.saved_card(date(2026, 11, 30))
        self.warn(date(2026, 11, 24))
        self.assertEqual(mail.outbox[0].to, ['pat@example.com'])
        self.assertIn('Dear guardian of Ann Lee', mail.outbox[0].body)

    def test_a_failed_email_is_tried_again_next_time(self):
        self.saved_card(date(2026, 11, 30))
        with mock.patch('mailer.compose.send', side_effect=OSError('mail server down')):
            self.assertEqual(self.warn(date(2026, 11, 24)), 0)
        self.assertIsNone(AutoPay.objects.get().expiry_warned_for)
        self.assertEqual(self.warn(date(2026, 11, 24)), 1)

    def test_cards_saved_before_expiry_dates_were_kept_are_looked_up_in_stripe(self):
        self.saved_card(None)
        with mock.patch('stripe.PaymentMethod.retrieve', return_value=card(12, 2026)) as retrieve:
            self.assertEqual(self.warn(date(2026, 12, 5)), 1)
        retrieve.assert_called_once()
        self.assertEqual(AutoPay.objects.get().expires_on, date(2026, 12, 31))


class CardUpdaterTests(CardExpiryTestCase):
    def test_a_replaced_card_gets_its_new_details_and_can_be_warned_again(self):
        self.saved_card(date(2026, 11, 30), expiry_warned_for=date(2026, 11, 30))
        autopay.payment_method_updated(self.org, card(11, 2030, last4='9999'))
        saved = AutoPay.objects.get()
        self.assertEqual((saved.label, saved.expires_on, saved.expiry_warned_for),
                         ('Visa ending 9999', date(2030, 11, 30), None))

    def test_another_clubs_card_is_left_alone(self):
        from organisations.models import Organisation
        other = Organisation.objects.create(name='Other', slug='other')
        self.saved_card(date(2026, 11, 30))
        autopay.payment_method_updated(other, card(11, 2030))
        self.assertEqual(AutoPay.objects.get().expires_on, date(2026, 11, 30))

    def test_the_webhook_listens_for_the_card_updater(self):
        self.assertIn('payment_method.automatically_updated', WEBHOOK_EVENTS)

    def test_the_webhook_passes_card_updates_on(self):
        import json

        from billing.test_payments import signed
        self.saved_card(date(2026, 11, 30))
        payload = json.dumps({'id': 'evt_1', 'object': 'event', 'type': 'payment_method.automatically_updated',
                              'data': {'object': {'id': 'pm_1', 'object': 'payment_method', 'type': 'card',
                                                  'card': {'brand': 'visa', 'last4': '9999',
                                                           'exp_month': 11, 'exp_year': 2030}}}})
        signature = signed(payload)
        response = self.client.post(reverse('stripe_webhook_org', kwargs={'org_slug': self.org.slug}), payload,
                                    content_type='application/json', HTTP_STRIPE_SIGNATURE=signature)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(AutoPay.objects.get().expires_on, date(2030, 11, 30))


class ClubViewTests(CardExpiryTestCase):
    def setUp(self):
        super().setUp()
        self.org.features = {**(self.org.features or {}), 'billing': True}
        self.org.save()
        admin = User.objects.create_user('admin', password='pw')
        OrganisationMember.objects.create(user=admin, organisation=self.org, role='org_admin')
        self.client.force_login(admin)
        self.failed = Invoice.objects.create(organisation=self.org, member=self.member, amount=Decimal('45'),
                                             period='December', due_date=date(2026, 12, 1),
                                             autopay_status='failed', autopay_error='Your card was declined.')
        Invoice.objects.create(organisation=self.org, member=self.member, amount=Decimal('45'), period='November',
                               due_date=date(2026, 11, 1))

    def test_failed_payments_and_expiring_cards_are_on_the_dashboard(self):
        self.saved_card(date(2026, 1, 31))       # long expired
        self.org.settings = {**(self.org.settings or {}), 'dashboard': {'version': 2, 'widgets': [
            {'key': 'payments_attention', 'x': 0, 'y': 0, 'w': 12, 'h': 7},
            {'key': 'needs_attention', 'x': 12, 'y': 0, 'w': 12, 'h': 7}]}}
        self.org.save()
        page = self.client.get(reverse('org_dashboard', kwargs={'org_slug': self.org.slug}))
        self.assertEqual(page.context['failed_count'], 1)
        self.assertEqual(page.context['payments_failed'], 1)
        self.assertEqual(page.context['cards_expiring'], 1)
        self.assertContains(page, 'Your card was declined.')
        self.assertContains(page, 'Expired')
        self.assertContains(page, '1 automatic payment</strong> failed')

    def test_the_invoice_list_can_show_just_failed_automatic_payments(self):
        page = self.client.get(reverse('invoice_list', kwargs={'org_slug': self.org.slug}) + '?status=autopay_failed')
        self.assertEqual([invoice.pk for invoice in page.context['invoices']], [self.failed.pk])

    def test_the_member_sees_the_warning_in_their_portal(self):
        self.saved_card(date(2026, 1, 31))
        page = self.client.get(reverse('member_portal', kwargs={'token': self.member.token}))
        self.assertContains(page, 'id="paying"')
        self.assertContains(page, 'This card expired at the end of January 2026')
