"""A plan can limit how many classes a member is enrolled on at once, instead of sessions a week or month."""
from datetime import timedelta

from django.contrib.messages import get_messages
from django.urls import reverse
from django.utils import timezone

from billing.models import BillingPolicy
from classes import booking
from classes.models import Class, ClassMember, Session, SessionBooking
from classes.test_plans import PlanTestBase
from members.models import FamilyGroup, FamilyGroupMember, Member


def class_plan(org, limit=2, name='Two classes', **kw):
    return BillingPolicy.objects.create(
        organisation=org, name=name, billing_cycle='monthly', amount=30, member_selectable=True,
        limit_type=BillingPolicy.LimitType.CLASSES, class_limit=limit, **kw,
    )


class ClassLimitTests(PlanTestBase):
    def setUp(self):
        super().setUp()
        self.seniors = Class.objects.create(organisation=self.org, name='Seniors', is_active=True)
        self.kata = Class.objects.create(organisation=self.org, name='Kata', is_active=True)
        for cls in (self.seniors, self.kata):
            for week in range(3):
                Session.objects.create(assigned_class=cls, date=self.monday + timedelta(days=7 * week))
        self.put_on(class_plan(self.org))

    def test_describes_itself(self):
        self.assertEqual(self.member.billing_policy.limit_text(), 'Up to 2 classes at a time')
        self.assertEqual(class_plan(self.org, limit=1, name='One').limit_text(), 'Up to 1 class at a time')

    def test_third_class_is_refused_and_leaving_one_frees_a_place(self):
        booking.enrol(self.member, self.juniors)
        booking.enrol(self.member, self.seniors)
        with self.assertRaisesMessage(booking.BookingError, 'allows up to 2 classes at a time and you\'re already enrolled in 2'):
            booking.enrol(self.member, self.kata)
        booking.cancel_enrolment(self.member, self.seniors)
        booking.enrol(self.member, self.kata)
        self.assertEqual(booking.enrolled_class_count(self.member), 2)

    def test_single_session_bookings_dont_count(self):
        booking.enrol(self.member, self.juniors)
        booking.enrol(self.member, self.seniors)
        session = self.kata.sessions.first()
        booking.book_session(self.member, session)
        self.assertTrue(SessionBooking.objects.filter(member=self.member, session=session).exists())

    def test_no_session_limit_applies_to_a_class_plan(self):
        policy = self.member.billing_policy
        policy.session_limit, policy.limit_period = 1, 'week'   # left over from before; ignored
        policy.save()
        booking.enrol(self.member, self.juniors)                  # three sessions this week
        quota = booking.Quota(self.member)
        self.assertFalse(quota.limited)
        booking.book_session(self.member, self.seniors.sessions.order_by('date').first())

    def test_unlimited_when_the_class_limit_is_blank(self):
        policy = self.member.billing_policy
        policy.class_limit = None
        policy.save()
        for cls in (self.juniors, self.seniors, self.kata):
            booking.enrol(self.member, cls)
        self.assertEqual(policy.limit_text(), 'Unlimited sessions')

    def test_each_family_member_has_their_own_allowance(self):
        sibling = Member.objects.create(organisation=self.org, name='Sam Able', billing_policy=self.member.billing_policy)
        family = FamilyGroup.objects.create(organisation=self.org, name='Ables')
        for m in (self.member, sibling):
            FamilyGroupMember.objects.create(family_group=family, member=m)
        policy = self.member.billing_policy
        policy.is_family_plan = True
        policy.save()
        booking.enrol(self.member, self.juniors)
        booking.enrol(self.member, self.seniors)
        booking.enrol(sibling, self.juniors)
        booking.enrol(sibling, self.kata)

    def test_portal_shows_the_allowance_and_why_a_class_cant_be_joined(self):
        booking.enrol(self.member, self.juniors)
        booking.enrol(self.member, self.seniors)
        book = self.client.get(self.portal('portal_bookings')).content.decode()
        self.assertIn('Up to 2 classes at a time · enrolled in 2 of 2', book)
        self.assertIn('Leave a class to join this one', book)
        plans = self.client.get(self.portal('portal_plans')).content.decode()
        self.assertIn('2 of 2 classes', plans)
        self.assertIn('You\'re enrolled in as many classes as your plan allows.', plans)
        self.act(action='enrol', class_id=self.kata.pk)
        self.assertFalse(ClassMember.objects.filter(member=self.member, assigned_class=self.kata).exists())

    def test_choosing_a_class_plan_while_over_it_warns(self):
        for cls in (self.juniors, self.seniors, self.kata):
            ClassMember.objects.create(member=self.member, assigned_class=cls, auto_enrol=True)
        self.put_on(None)
        response = self.act(action='choose_plan', policy_id=class_plan(self.org, name='Picked').pk)
        texts = [str(m) for m in get_messages(response.wsgi_request)]
        self.assertTrue(any('enrolled in 3 classes, more than Picked allows' in t for t in texts), texts)
        self.assertEqual(booking.enrolled_class_count(self.member), 3)   # nobody is taken off a class


class ClassLimitSettingsTests(PlanTestBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)

    def post(self, url_name, data, **kwargs):
        url = reverse(url_name, kwargs={'org_slug': self.org.slug, **kwargs})
        return self.client.post(url, {'name': 'Pro', 'billing_cycle': 'monthly', 'amount': '40', 'is_active': '1', **data})

    def test_staff_choose_classes_or_sessions(self):
        self.post('billing_policy_create', {'session_limit': '2', 'limit_unit': 'classes'})
        policy = BillingPolicy.objects.get(name='Pro')
        self.assertEqual((policy.limit_type, policy.class_limit, policy.session_limit), ('classes', 2, None))
        page = self.client.get(reverse('billing_policies', kwargs={'org_slug': self.org.slug})).content.decode()
        self.assertIn('<option value="classes" selected>Classes at a time</option>', page)
        self.assertIn('Up to 2 classes at a time', page)
        self.post('billing_policy_edit', {'session_limit': '3', 'limit_unit': 'week'}, pk=policy.pk)
        policy.refresh_from_db()
        self.assertEqual((policy.limit_type, policy.session_limit, policy.limit_period, policy.class_limit),
                         ('sessions', 3, 'week', None))

    def test_bad_class_limit_is_refused(self):
        self.post('billing_policy_create', {'session_limit': '150', 'limit_unit': 'classes'})
        self.assertFalse(BillingPolicy.objects.filter(name='Pro').exists())
