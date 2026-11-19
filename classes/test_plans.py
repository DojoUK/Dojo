from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from billing.models import BillingPolicy
from classes import booking
from classes.models import Class, ClassMember, Session, SessionBooking
from classes.test_bookings import BookingTestBase
from organisations.models import Organisation


def plan(org, name='Twice a week', limit=2, period='week', selectable=True, **kw):
    return BillingPolicy.objects.create(
        organisation=org, name=name, billing_cycle='monthly', amount=30, session_limit=limit,
        limit_period=period, member_selectable=selectable, **kw,
    )


class PlanTestBase(BookingTestBase):
    def setUp(self):
        self.member.billing_policy = None
        self.member.save()
        today = timezone.localdate()
        ahead = today + timedelta(days=14)
        self.monday = ahead - timedelta(days=ahead.weekday())
        self.week = [Session.objects.create(assigned_class=self.juniors, date=self.monday + timedelta(days=i))
                     for i in range(3)]
        self.next_week = Session.objects.create(assigned_class=self.juniors, date=self.monday + timedelta(days=7))

    def put_on(self, policy):
        self.member.billing_policy = policy
        self.member.save()


class WeeklyLimitTests(PlanTestBase):
    def test_third_booking_in_a_week_is_refused_but_the_next_week_is_fine(self):
        self.put_on(plan(self.org))
        booking.book_session(self.member, self.week[0])
        booking.book_session(self.member, self.week[1])
        with self.assertRaises(booking.BookingError) as ctx:
            booking.book_session(self.member, self.week[2])
        self.assertIn('2 sessions a week', str(ctx.exception))
        self.assertFalse(SessionBooking.objects.filter(session=self.week[2]).exists())
        booking.book_session(self.member, self.next_week)

    def test_cancelling_frees_a_place(self):
        self.put_on(plan(self.org, limit=1))
        booking.book_session(self.member, self.week[0])
        with self.assertRaises(booking.BookingError):
            booking.book_session(self.member, self.week[1])
        booking.cancel_session_booking(self.member, self.week[0])
        booking.book_session(self.member, self.week[1])

    def test_unlimited_plan_has_no_limit(self):
        self.put_on(plan(self.org, 'Unlimited', limit=None))
        for session in self.week:
            booking.book_session(self.member, session)
        self.assertEqual(SessionBooking.objects.count(), 3)


class MonthlyLimitTests(PlanTestBase):
    def test_month_period_counts_the_whole_calendar_month(self):
        first = (timezone.localdate() + timedelta(days=62)).replace(day=1)
        sessions = [Session.objects.create(assigned_class=self.juniors, date=first + timedelta(days=i * 9))
                    for i in range(3)]
        later = Session.objects.create(assigned_class=self.juniors,
                                       date=(first + timedelta(days=40)).replace(day=1))
        self.put_on(plan(self.org, 'Monthly pass', limit=2, period='month'))
        booking.book_session(self.member, sessions[0])
        booking.book_session(self.member, sessions[1])
        with self.assertRaises(booking.BookingError) as ctx:
            booking.book_session(self.member, sessions[2])
        self.assertIn('2 sessions a month', str(ctx.exception))
        booking.book_session(self.member, later)


class EnrolmentCountsTests(PlanTestBase):
    def test_enrolling_is_refused_if_its_sessions_would_exceed_the_limit(self):
        self.put_on(plan(self.org, limit=2))
        with self.assertRaises(booking.BookingError):
            booking.enrol(self.member, self.juniors)  # 3 sessions in one week
        self.assertFalse(ClassMember.objects.exists())
        self.put_on(plan(self.org, 'Three a week', limit=3))
        booking.enrol(self.member, self.juniors)
        self.assertTrue(ClassMember.objects.filter(member=self.member).exists())

    def test_enrolled_sessions_count_and_skipping_one_frees_it(self):
        self.put_on(plan(self.org, limit=3))
        booking.enrol(self.member, self.juniors)
        seniors = Class.objects.create(organisation=self.org, name='Seniors', is_active=True)
        extra = Session.objects.create(assigned_class=seniors, date=self.monday + timedelta(days=3))
        with self.assertRaises(booking.BookingError):
            booking.book_session(self.member, extra)
        booking.skip_session(self.member, self.week[0])
        booking.book_session(self.member, extra)
        with self.assertRaises(booking.BookingError):
            booking.rejoin_session(self.member, self.week[0])

    def test_enrolling_replaces_bookings_without_double_counting(self):
        self.put_on(plan(self.org, limit=3))
        booking.book_session(self.member, self.week[0])
        booking.enrol(self.member, self.juniors)

    def test_quota_summary(self):
        self.put_on(plan(self.org, limit=3))
        booking.book_session(self.member, self.week[0])
        quota = booking.Quota(self.member)
        self.assertEqual((quota.limit, quota.used(self.monday)), (3, 1))
        self.assertFalse(booking.Quota(self.other).limited)


class PortalPlanTests(PlanTestBase):
    def test_member_chooses_a_selectable_plan(self):
        chosen = plan(self.org, 'Gold')
        plan(self.org, 'Hidden', selectable=False)
        page = self.client.get(self.portal('portal_plans'))
        self.assertContains(page, 'Gold')
        self.assertNotContains(page, 'Hidden')
        response = self.act(action='choose_plan', policy_id=chosen.pk)
        self.assertRedirects(response, self.portal('portal_plans'))
        self.member.refresh_from_db()
        self.assertEqual(self.member.billing_policy, chosen)
        page = self.client.get(self.portal('portal_plans'))
        self.assertContains(page, '0 of 2 used')
        self.assertContains(page, 'Your plan')

    def test_cannot_choose_hidden_inactive_or_other_clubs_plans(self):
        hidden = plan(self.org, 'Hidden', selectable=False)
        inactive = plan(self.org, 'Old', is_active=False)
        other_org = Organisation.objects.create(name='Other', slug='other-club')
        foreign = plan(other_org, 'Foreign')
        for policy in (hidden, inactive, foreign):
            self.assertEqual(self.act(action='choose_plan', policy_id=policy.pk).status_code, 404)
        self.member.refresh_from_db()
        self.assertIsNone(self.member.billing_policy)

    def test_plans_need_the_billing_feature(self):
        policy = plan(self.org)
        self.org.features = {**self.org.features, 'billing': False}
        self.org.save()
        self.assertEqual(self.act(action='choose_plan', policy_id=policy.pk).status_code, 404)

    def test_the_action_reports_the_limit(self):
        self.put_on(plan(self.org, limit=1))
        booking.book_session(self.member, self.week[0])
        response = self.client.post(self.portal('portal_booking_action'),
                                    {'action': 'book', 'session_id': self.week[1].pk}, follow=True)
        self.assertContains(response, 'would take you over')
        self.assertEqual(SessionBooking.objects.filter(member=self.member).count(), 1)


class StaffPolicyFormTests(PlanTestBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)

    def test_staff_set_the_limit_and_selectable_flag(self):
        url = reverse('billing_policy_create', kwargs={'org_slug': self.org.slug})
        self.client.post(url, {'name': 'Pro', 'billing_cycle': 'monthly', 'amount': '40', 'session_limit': '3',
                               'limit_period': 'week', 'member_selectable': '1', 'plan_description': 'Three a week'})
        policy = BillingPolicy.objects.get(name='Pro')
        self.assertEqual((policy.session_limit, policy.limit_period, policy.member_selectable), (3, 'week', True))
        edit = reverse('billing_policy_edit', kwargs={'org_slug': self.org.slug, 'pk': policy.pk})
        self.client.post(edit, {'name': 'Pro', 'billing_cycle': 'monthly', 'amount': '40', 'session_limit': '',
                                'limit_period': 'month', 'is_active': '1'})
        policy.refresh_from_db()
        self.assertEqual((policy.session_limit, policy.member_selectable, policy.limit_text()),
                         (None, False, 'Unlimited sessions'))

    def test_bad_limit_is_refused(self):
        url = reverse('billing_policy_create', kwargs={'org_slug': self.org.slug})
        self.client.post(url, {'name': 'Bad', 'billing_cycle': 'monthly', 'amount': '40', 'session_limit': '-2'})
        self.assertFalse(BillingPolicy.objects.filter(name='Bad').exists())
        page = self.client.get(reverse('billing_policies', kwargs={'org_slug': self.org.slug}))
        self.assertEqual(page.status_code, 200)


class CalendarColourTests(PlanTestBase):
    """Days in the portal calendar: blue booked, green spaces, red full, grey plan limit."""

    def status(self):
        page = self.client.get(self.portal('portal_bookings'))
        return page.context['calendar_status']

    def test_open_day_is_green_and_a_booked_day_is_blue(self):
        self.put_on(self.standard)
        day = self.week[0].date.isoformat()
        self.assertEqual(self.status()[day], 'open')
        booking.book_session(self.member, self.week[0])
        self.assertEqual(self.status()[day], 'booked')

    def test_full_day_is_red_unless_the_member_is_booked_in(self):
        self.put_on(self.standard)
        self.juniors.max_capacity = 1
        self.juniors.save()
        booking.book_session(self.other, self.week[0])
        day = self.week[0].date.isoformat()
        self.assertEqual(self.status()[day], 'full')
        SessionBooking.objects.all().delete()
        booking.book_session(self.member, self.week[0])
        self.assertEqual(self.status()[day], 'booked')

    def test_day_with_one_full_and_one_open_class_is_green(self):
        from classes.models import Class, Session
        self.put_on(self.standard)
        self.juniors.max_capacity = 1
        self.juniors.save()
        booking.book_session(self.other, self.week[0])
        seniors = Class.objects.create(organisation=self.org, name='Seniors', is_active=True)
        Session.objects.create(assigned_class=seniors, date=self.week[0].date)
        self.assertEqual(self.status()[self.week[0].date.isoformat()], 'open')

    def test_day_only_blocked_by_the_plan_limit_is_grey(self):
        self.put_on(plan(self.org, limit=1, period='week'))
        booking.book_session(self.member, self.week[0])
        self.assertEqual(self.status()[self.week[1].date.isoformat()], 'limit')

    def test_enrolled_class_days_are_blue(self):
        self.put_on(self.standard)
        booking.enrol(self.member, self.juniors)
        self.assertEqual(self.status()[self.week[1].date.isoformat()], 'booked')

    def test_the_plan_has_its_own_page_and_nav_link(self):
        page = self.client.get(self.portal('portal_plans'))
        self.assertContains(page, 'My plan')
        self.assertContains(page, self.portal('portal_plans'))
