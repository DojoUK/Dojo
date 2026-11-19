import json
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.core import mail
from django.urls import reverse
from django.utils import timezone

from billing.models import BillingPolicy, Invoice, PaymentSettings
from billing.test_payments import WEBHOOK_SECRET, signed
from classes import booking
from classes.models import ClassMember, SessionBooking
from classes.register import register_enrolments
from classes.test_bookings import BookingTestBase

CHECKOUT_URL = 'https://checkout.stripe.test/pay'


class PayPerSessionBase(BookingTestBase):
    """A member with no plan, a club that charges £8 for a single session and has Stripe connected."""

    def setUp(self):
        self.member.billing_policy = None
        self.member.save()
        self.org.settings = {**(self.org.settings or {}), 'single_session_price': '8.00'}
        self.org.save()
        payments = PaymentSettings(organisation=self.org, accept_card=True, stripe_publishable_key='pk_test_x')
        payments.set_secrets({'stripe_secret_key': 'sk_test_x', 'stripe_webhook_secret': WEBHOOK_SECRET})
        payments.save()

    def book(self):
        return booking.book_session(self.member, self.session)

    def webhook(self, event_type, invoice, amount_total=800):
        payload = json.dumps({
            'id': 'evt_1', 'object': 'event', 'type': event_type,
            'data': {'object': {'id': 'cs_1', 'object': 'checkout.session', 'mode': 'payment',
                                'amount_total': amount_total, 'payment_intent': 'pi_1',
                                'metadata': {'invoice_pk': str(invoice.pk)}}},
        })
        return self.client.post(reverse('stripe_webhook_org', kwargs={'org_slug': self.org.slug}), payload,
                                content_type='application/json', HTTP_STRIPE_SIGNATURE=signed(payload))


class WholeClassNeedsAPlanTests(PayPerSessionBase):
    def test_member_without_a_plan_cannot_enrol(self):
        with self.assertRaises(booking.BookingError) as ctx:
            booking.enrol(self.member, self.juniors)
        self.assertIn('Choose a plan', str(ctx.exception))
        self.assertFalse(ClassMember.objects.exists())

    def test_portal_refuses_and_shows_why(self):
        response = self.act(action='enrol', class_id=self.juniors.pk)
        self.assertFalse(ClassMember.objects.exists())
        page = self.client.get(self.portal('portal_bookings'))
        self.assertContains(page, 'Choose a plan first to enrol on this class')

    def test_member_on_a_plan_can_enrol(self):
        self.member.billing_policy = self.standard
        self.member.save()
        booking.enrol(self.member, self.juniors)
        self.assertTrue(ClassMember.objects.exists())

    def test_clubs_without_billing_are_not_affected(self):
        self.org.features = {**self.org.features, 'billing': False}
        self.org.save()
        booking.enrol(self.member, self.juniors)
        self.assertTrue(ClassMember.objects.exists())
        booking.cancel_enrolment(self.member, self.juniors)
        self.assertFalse(booking.book_session(self.member, self.session).is_pending)


class SingleSessionPaymentTests(PayPerSessionBase):
    def test_booking_without_a_plan_is_held_until_paid(self):
        pending = self.book()
        self.assertTrue(pending.is_pending)
        invoice = pending.invoice
        self.assertEqual((invoice.amount, invoice.status, invoice.member), (Decimal('8.00'), 'unpaid', self.member))
        self.assertEqual(invoice.autopay_status, '')
        self.assertEqual(len(mail.outbox), 0)  # nothing is confirmed yet
        self.assertEqual(booking.session_headcount(self.session), 1)  # but the place is held
        self.assertEqual([cm.member for cm in register_enrolments(self.juniors, self.session)], [])

    def test_portal_sends_the_member_to_stripe_checkout(self):
        with mock.patch('stripe.checkout.Session.create', return_value=mock.Mock(url=CHECKOUT_URL)) as create:
            response = self.act(action='book', session_id=self.session.pk)
        self.assertRedirects(response, CHECKOUT_URL, fetch_redirect_response=False)
        kwargs = create.call_args.kwargs
        invoice = Invoice.objects.get()
        self.assertEqual(kwargs['metadata']['invoice_pk'], str(invoice.pk))
        self.assertEqual(kwargs['line_items'][0]['price_data']['unit_amount'], 800)
        self.assertEqual(kwargs['mode'], 'payment')
        self.assertTrue(SessionBooking.objects.get().is_pending)

    def test_webhook_confirms_the_booking_and_emails(self):
        pending = self.book()
        self.assertEqual(self.webhook('checkout.session.completed', pending.invoice).status_code, 200)
        pending.refresh_from_db()
        pending.invoice.refresh_from_db()
        self.assertFalse(pending.is_pending)
        self.assertEqual(pending.invoice.status, 'paid')
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual([cm.member for cm in register_enrolments(self.juniors, self.session)], [self.member])
        self.webhook('checkout.session.completed', pending.invoice)  # Stripe retries: no second email
        self.assertEqual(len(mail.outbox), 1)

    def test_booking_is_not_confirmed_without_a_valid_webhook(self):
        pending = self.book()
        payload = json.dumps({'id': 'evt_1', 'object': 'event', 'type': 'checkout.session.completed',
                              'data': {'object': {'id': 'cs_1', 'object': 'checkout.session', 'mode': 'payment',
                                                  'metadata': {'invoice_pk': str(pending.invoice.pk)}}}})
        response = self.client.post(reverse('stripe_webhook_org', kwargs={'org_slug': self.org.slug}), payload,
                                    content_type='application/json',
                                    HTTP_STRIPE_SIGNATURE=signed(payload, 'whsec_wrong'))
        self.assertEqual(response.status_code, 400)
        pending.refresh_from_db()
        self.assertTrue(pending.is_pending)

    def test_expired_checkout_releases_the_place_and_cancels_the_invoice(self):
        pending = self.book()
        self.webhook('checkout.session.expired', pending.invoice)
        self.assertFalse(SessionBooking.objects.exists())
        pending.invoice.refresh_from_db()
        self.assertEqual(pending.invoice.status, 'cancelled')

    def test_unpaid_holds_are_released_after_35_minutes(self):
        pending = self.book()
        SessionBooking.objects.filter(pk=pending.pk).update(created_at=timezone.now() - timedelta(minutes=36))
        booking.release_expired()
        self.assertFalse(SessionBooking.objects.exists())
        self.assertEqual(Invoice.objects.get().status, 'cancelled')

    def test_a_recent_hold_is_kept_and_blocks_a_second_booking(self):
        self.book()
        booking.release_expired()
        self.assertEqual(SessionBooking.objects.count(), 1)
        with self.assertRaises(booking.BookingError):
            self.book()

    def test_a_held_place_counts_towards_capacity(self):
        self.juniors.max_capacity = 1
        self.juniors.save()
        self.book()
        with self.assertRaises(booking.BookingError):
            booking.book_session(self.other, self.session)  # on a plan, but the class is full

    def test_member_can_cancel_a_booking_awaiting_payment(self):
        pending = self.book()
        self.act(action='cancel_booking', session_id=self.session.pk)
        self.assertFalse(SessionBooking.objects.exists())
        pending.invoice.refresh_from_db()
        self.assertEqual(pending.invoice.status, 'cancelled')

    def test_stripe_failure_books_nothing(self):
        import stripe
        with mock.patch('stripe.checkout.Session.create', side_effect=stripe.error.APIConnectionError('down')):
            response = self.act(action='book', session_id=self.session.pk)
        self.assertFalse(SessionBooking.objects.exists())
        self.assertEqual(Invoice.objects.get().status, 'cancelled')
        self.assertRedirects(response, self.portal('portal_bookings'))

    def test_paid_booking_cancelled_by_the_member_is_not_refunded_automatically(self):
        pending = self.book()
        self.webhook('checkout.session.completed', pending.invoice)
        paid = booking.cancel_session_booking(self.member, self.session)
        self.assertEqual(paid.pk, pending.invoice.pk)

    def test_members_on_a_plan_book_free_and_instantly(self):
        self.member.billing_policy = self.standard
        self.member.save()
        made = self.book()
        self.assertFalse(made.is_pending)
        self.assertFalse(Invoice.objects.exists())
        self.assertEqual(len(mail.outbox), 1)


class NotAvailableTests(PayPerSessionBase):
    def test_no_price_means_single_sessions_are_booked_free(self):
        self.org.settings = {}
        self.org.save()
        made = self.book()
        self.assertFalse(made.is_pending)
        self.assertFalse(Invoice.objects.exists())
        self.assertEqual(len(mail.outbox), 1)

    def test_no_stripe_means_single_sessions_are_booked_free(self):
        PaymentSettings.objects.all().delete()
        made = self.book()
        self.assertFalse(made.is_pending)
        self.assertFalse(Invoice.objects.exists())

    def test_portal_offers_a_plain_book_button_when_not_charging(self):
        self.org.settings = {}
        self.org.save()
        page = self.client.get(self.portal('portal_bookings'))
        self.assertContains(page, 'You can still book single sessions without a plan.')
        self.assertNotContains(page, 'Book and pay')
        response = self.act(action='book', session_id=self.session.pk)
        self.assertRedirects(response, self.portal('portal_bookings'))
        self.assertFalse(SessionBooking.objects.get().is_pending)

    def test_portal_shows_the_price_on_the_book_button(self):
        page = self.client.get(self.portal('portal_bookings'))
        self.assertContains(page, 'Book and pay £8.00')


class SetupChecklistTests(PayPerSessionBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)
        self.dashboard = reverse('org_dashboard', kwargs={'org_slug': self.org.slug})

    def test_dashboard_flags_a_missing_price_and_missing_stripe(self):
        self.org.settings = {}
        self.org.save()
        PaymentSettings.objects.all().delete()
        response = self.client.get(self.dashboard)
        self.assertEqual(response.context['single_session_setup'], ['price', 'stripe'])
        self.assertContains(response, 'Set your single session price')

    def test_nothing_to_do_once_both_are_set(self):
        self.assertEqual(self.client.get(self.dashboard).context['single_session_setup'], [])

    def test_not_asked_for_when_no_class_is_bookable_or_billing_is_off(self):
        self.org.settings = {}
        self.org.save()
        self.juniors.is_active = False
        self.juniors.save()
        self.assertEqual(self.client.get(self.dashboard).context['single_session_setup'], [])
        self.juniors.is_active = True
        self.juniors.save()
        self.org.features = {**self.org.features, 'billing': False}
        self.org.save()
        self.assertEqual(self.client.get(self.dashboard).context['single_session_setup'], [])

    def test_the_banner_shows_even_after_the_checklist_is_dismissed(self):
        self.org.settings = {'onboarding_dismissed': True}
        self.org.save()
        self.assertEqual(self.client.get(self.dashboard).context['single_session_setup'], ['price'])


class SinglePriceSettingTests(PayPerSessionBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)
        self.url = reverse('org_settings', kwargs={'org_slug': self.org.slug})

    def save(self, price):
        self.client.post(self.url, {'action': 'features', 'single_session_price': price, 'features': ['billing']})
        self.org.refresh_from_db()

    def test_price_is_saved_and_can_be_cleared(self):
        self.save('12.5')
        self.assertEqual(self.org.single_session_price, Decimal('12.50'))
        self.save('')
        self.assertIsNone(self.org.single_session_price)

    def test_bad_prices_are_refused_and_leave_the_old_price(self):
        for bad in ('0', '-3', 'abc', '5000'):
            self.save(bad)
            self.assertEqual(self.org.single_session_price, Decimal('8.00'), bad)
