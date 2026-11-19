"""Past a plan's session limit, a member can still book a session by paying the single-session price."""
from datetime import date, timedelta
from unittest import mock

from billing.models import PaymentSettings
from billing.test_payments import WEBHOOK_SECRET
from classes import booking
from classes.test_plans import PlanTestBase, plan
from classes.test_single_session_payment import CHECKOUT_URL


class ExtraSessionTests(PlanTestBase):
    """On 'Twice a week': two sessions are included, a third in the same week is an extra, paid for now."""

    def setUp(self):
        super().setUp()
        self.org.settings = {**(self.org.settings or {}), 'single_session_price': '8.00'}
        self.org.save()
        payments = PaymentSettings(organisation=self.org, accept_card=True, stripe_publishable_key='pk_test_x')
        payments.set_secrets({'stripe_secret_key': 'sk_test_x', 'stripe_webhook_secret': WEBHOOK_SECRET})
        payments.save()
        self.put_on(plan(self.org))   # 2 sessions a week
        for session in self.week[:2]:
            booking.book_session(self.member, session)

    def test_a_booking_past_the_limit_is_an_extra_held_until_paid(self):
        extra = booking.book_session(self.member, self.week[2])
        self.assertTrue(extra.is_pending)
        self.assertEqual(extra.invoice.amount, 8)
        self.assertIn('Extra session beyond the member\'s plan limit', extra.invoice.notes)
        # Within the limit, bookings are still included in the plan: no payment.
        self.assertFalse(booking.book_session(self.member, self.next_week).is_pending)

    def test_paying_confirms_the_extra_session(self):
        extra = booking.book_session(self.member, self.week[2])
        extra.invoice.status = 'paid'
        extra.invoice.save()
        booking.confirm_paid(extra.invoice)
        extra.refresh_from_db()
        self.assertFalse(extra.is_pending)

    def test_portal_offers_to_book_and_pay(self):
        page = self.client.get(self.portal('portal_bookings')).content.decode()
        self.assertIn('Book and pay £8.00', page)
        self.assertIn('Extra session', page)
        self.assertIn('extra sessions £8.00, paid when you book', page)
        with mock.patch('stripe.checkout.Session.create', return_value=mock.Mock(url=CHECKOUT_URL)):
            response = self.act(action='book', session_id=self.week[2].pk)
        self.assertRedirects(response, CHECKOUT_URL, fetch_redirect_response=False)
        plans = self.client.get(self.portal('portal_plans')).content.decode()
        self.assertIn('Past your limit you can still book extra sessions at £8.00 each, paid when you book.', plans)
        self.assertIn('Weeks run Monday to Sunday.', plans)

    def test_without_a_price_or_stripe_the_limit_still_refuses(self):
        PaymentSettings.objects.update(accept_card=False)   # card payments off
        with self.assertRaisesMessage(booking.BookingError, 'would take you over that'):
            booking.book_session(self.member, self.week[2])
        page = self.client.get(self.portal('portal_bookings')).content.decode()
        self.assertIn('Plan limit reached', page)
        self.assertNotIn('Book and pay', page)

    def test_whole_class_enrolment_past_the_limit_is_still_refused(self):
        with self.assertRaises(booking.BookingError):
            booking.enrol(self.member, self.juniors)


class WeekRunsMondayToSundayTests(PlanTestBase):
    def test_bounds_and_label(self):
        self.put_on(plan(self.org))
        quota = booking.Quota(self.member)
        for day in (date(2026, 11, 16), date(2026, 11, 19), date(2026, 11, 22)):   # Mon, Thu, Sun
            self.assertEqual(quota.bounds(day), (date(2026, 11, 16), date(2026, 11, 22)))
        self.assertEqual(quota.bounds(date(2026, 11, 23))[0], date(2026, 11, 23))   # next Monday starts afresh
        self.assertEqual(quota.label(date(2026, 11, 19)), 'the week Monday 16 to Sunday 22 November')
        self.assertEqual(quota.label(date(2026, 12, 2)), 'the week Monday 30 November to Sunday 6 December')
