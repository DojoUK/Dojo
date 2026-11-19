from datetime import date
from decimal import Decimal

from django.urls import reverse

from billing.calculator import calculate_invoice_amount
from billing.models import BillingPolicy, Invoice
from classes import booking
from classes.models import ClassMember, SessionBooking
from classes.test_plans import PlanTestBase, plan
from members import family
from members.models import FamilyGroup, Member


def family_policy(org, name='Family', amount=50, **kw):
    return BillingPolicy.objects.create(organisation=org, name=name, billing_cycle='monthly', amount=amount,
                                        is_family_plan=True, **kw)


class FamilyBase(PlanTestBase):
    """Alice (self.member) and Bob (self.other) are siblings; both start with no plan."""

    def setUp(self):
        super().setUp()
        self.other.billing_policy = None
        self.other.save()
        family.link(self.member, self.other, name='Able family')

    def portal_for(self, member, name, **kwargs):
        return reverse(name, kwargs={'token': member.token, **kwargs})


class FamilyHelperTests(FamilyBase):
    def test_link_makes_one_group_with_both_members(self):
        self.assertEqual({m.pk for m in family.family_members(self.member)}, {self.member.pk, self.other.pk})
        self.assertEqual(FamilyGroup.objects.count(), 1)
        self.assertEqual(family.siblings(self.member), [self.other])

    def test_unlinking_the_second_to_last_member_removes_the_group(self):
        family.unlink(self.other)
        self.assertFalse(FamilyGroup.objects.exists())
        self.assertEqual(family.family_members(self.member), [self.member])

    def test_archived_siblings_are_left_out_of_the_family_view(self):
        self.other.is_active = False
        self.other.save()
        self.assertEqual(family.family_members(self.member), [self.member])
        self.assertEqual(len(family.family_members(self.member, active_only=False)), 2)

    def test_a_family_plan_goes_on_everyone_but_other_plans_only_on_one(self):
        shared = family_policy(self.org)
        family.assign_plan(self.member, shared)
        self.member.refresh_from_db(), self.other.refresh_from_db()
        self.assertEqual((self.member.billing_policy, self.other.billing_policy), (shared, shared))
        own = plan(self.org, 'Solo', selectable=True)
        family.assign_plan(self.member, own)
        self.member.refresh_from_db(), self.other.refresh_from_db()
        self.assertEqual((self.member.billing_policy, self.other.billing_policy), (own, shared))


class StaffFamilyTests(FamilyBase):
    def setUp(self):
        super().setUp()
        family.unlink(self.other)  # these tests link them again themselves
        self.client.force_login(self.admin)
        self.url = reverse('member_family', kwargs={'org_slug': self.org.slug, 'pk': self.member.pk})

    def test_staff_link_and_unlink_siblings(self):
        self.client.post(self.url, {'action': 'add', 'member_id': self.other.pk})
        self.assertEqual(len(family.family_members(self.member)), 2)
        self.client.post(self.url, {'action': 'remove', 'member_id': self.other.pk})
        self.assertFalse(FamilyGroup.objects.exists())

    def test_cannot_link_someone_already_in_another_family(self):
        third = Member.objects.create(organisation=self.org, name='Cat Cole')
        family.link(self.other, third)
        self.client.post(self.url, {'action': 'add', 'member_id': self.other.pk})
        self.assertEqual(family.family_members(self.member), [self.member])

    def test_cannot_link_members_from_another_club(self):
        from organisations.models import Organisation
        elsewhere = Member.objects.create(organisation=Organisation.objects.create(name='Other', slug='other'), name='Zed')
        self.client.post(self.url, {'action': 'add', 'member_id': elsewhere.pk})
        self.assertFalse(FamilyGroup.objects.exists())

    def test_family_name_and_discount_are_validated(self):
        self.client.post(self.url, {'action': 'add', 'member_id': self.other.pk})
        self.client.post(self.url, {'action': 'update', 'name': 'The Ables', 'discount_percentage': '10'})
        group = FamilyGroup.objects.get()
        self.assertEqual((group.name, group.discount_percentage), ('The Ables', Decimal('10.00')))
        self.client.post(self.url, {'action': 'update', 'name': 'Bad', 'discount_percentage': '150'})
        group.refresh_from_db()
        self.assertEqual(group.name, 'The Ables')

    def test_member_page_shows_the_family_card(self):
        self.client.post(self.url, {'action': 'add', 'member_id': self.other.pk})
        page = self.client.get(reverse('member_detail', kwargs={'org_slug': self.org.slug, 'pk': self.member.pk}))
        self.assertContains(page, 'Bob Best')

    def test_choosing_a_family_plan_for_one_member_puts_it_on_all(self):
        shared = family_policy(self.org)
        self.client.post(self.url, {'action': 'add', 'member_id': self.other.pk})
        self.client.post(reverse('member_billing_policy_set', kwargs={'org_slug': self.org.slug, 'pk': self.member.pk}),
                         {'billing_policy': shared.pk})
        self.other.refresh_from_db()
        self.assertEqual(self.other.billing_policy, shared)

    def test_policy_form_saves_the_family_flag(self):
        create = reverse('billing_policy_create', kwargs={'org_slug': self.org.slug})
        self.client.post(create, {'name': 'Fam', 'billing_cycle': 'monthly', 'amount': '60', 'is_family_plan': '1'})
        self.assertTrue(BillingPolicy.objects.get(name='Fam').is_family_plan)


class PortalSwitcherTests(FamilyBase):
    def test_each_page_offers_every_family_member_on_the_same_page(self):
        for name in ('portal_bookings', 'portal_plans', 'portal_syllabus', 'portal_my_details', 'portal_payments', 'member_portal'):
            page = self.client.get(self.portal_for(self.member, name))
            if page.status_code != 200:
                continue  # that section is switched off for the test club
            self.assertContains(page, 'aria-label="Family members"')
            self.assertContains(page, self.portal_for(self.other, name))

    def test_no_switcher_without_a_family(self):
        family.unlink(self.other)
        page = self.client.get(self.portal_for(self.member, 'portal_bookings'))
        self.assertNotContains(page, 'aria-label="Family members"')

    def test_each_child_books_separately(self):
        self.put_on(plan(self.org, limit=None))
        self.client.post(self.portal_for(self.other, 'portal_booking_action'),
                         {'action': 'book', 'session_id': self.week[0].pk})
        self.assertEqual(list(SessionBooking.objects.values_list('member', flat=True)), [self.other.pk])


class PortalPaymentsTests(FamilyBase):
    def invoice(self, member, period='November 2026'):
        return Invoice.objects.create(organisation=self.org, member=member, amount=30, period=period,
                                      due_date=date(2026, 11, 30))

    def test_payments_shows_the_whole_familys_invoices_labelled_by_child(self):
        self.invoice(self.member, 'Alice November')
        self.invoice(self.other, 'Bob November')
        page = self.client.get(self.portal_for(self.member, 'portal_payments'))
        self.assertContains(page, 'Alice November')
        self.assertContains(page, 'Bob November')
        self.assertContains(page, 'Bob Best')

    def test_strangers_invoices_are_not_shown(self):
        stranger = Member.objects.create(organisation=self.org, name='Dan Dee')
        self.invoice(stranger, 'Dan November')
        page = self.client.get(self.portal_for(self.member, 'portal_payments'))
        self.assertNotContains(page, 'Dan November')

    def test_no_child_labels_without_a_family(self):
        family.unlink(self.other)
        self.invoice(self.member, 'Alice November')
        page = self.client.get(self.portal_for(self.member, 'portal_payments'))
        self.assertNotContains(page, 'bi-person me-1')

    def test_a_parent_can_start_payment_for_a_siblings_invoice(self):
        from unittest import mock
        inv = self.invoice(self.other)
        with mock.patch('members.portal_views.stripe_keys', return_value=mock.Mock(secret='sk_test_x')), \
                mock.patch('stripe.checkout.Session.create', return_value=mock.Mock(url='https://stripe.test/x')):
            response = self.client.post(self.portal_for(self.member, 'portal_checkout', invoice_pk=inv.pk))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], 'https://stripe.test/x')

    def test_but_not_a_strangers(self):
        stranger = Member.objects.create(organisation=self.org, name='Dan Dee')
        inv = self.invoice(stranger)
        response = self.client.post(self.portal_for(self.member, 'portal_checkout', invoice_pk=inv.pk))
        self.assertEqual(response.status_code, 404)


class PortalPlanChoiceTests(FamilyBase):
    def test_choosing_a_family_plan_puts_every_child_on_it(self):
        shared = family_policy(self.org, member_selectable=True)
        self.act(action='choose_plan', policy_id=shared.pk)
        self.member.refresh_from_db(), self.other.refresh_from_db()
        self.assertEqual((self.member.billing_policy, self.other.billing_policy), (shared, shared))

    def test_choosing_an_ordinary_plan_changes_only_that_child(self):
        solo = plan(self.org, 'Solo', selectable=True)
        self.act(action='choose_plan', policy_id=solo.pk)
        self.member.refresh_from_db(), self.other.refresh_from_db()
        self.assertEqual((self.member.billing_policy, self.other.billing_policy), (solo, None))

    def test_children_can_be_on_different_plans(self):
        a, b = plan(self.org, 'A', selectable=True), plan(self.org, 'B', selectable=True)
        self.act(action='choose_plan', policy_id=a.pk)
        self.client.post(self.portal_for(self.other, 'portal_booking_action'), {'action': 'choose_plan', 'policy_id': b.pk})
        self.member.refresh_from_db(), self.other.refresh_from_db()
        self.assertEqual((self.member.billing_policy, self.other.billing_policy), (a, b))


class SharedLimitTests(FamilyBase):
    def test_a_family_plan_limit_is_shared_between_the_children(self):
        shared = family_policy(self.org, session_limit=2, limit_period='week')
        family.assign_plan(self.member, shared)
        self.member.refresh_from_db(), self.other.refresh_from_db()
        booking.book_session(self.member, self.week[0])
        booking.book_session(self.other, self.week[1])
        for child in (self.member, self.other):
            with self.assertRaises(booking.BookingError) as ctx:
                booking.book_session(child, self.week[2])
            self.assertIn('shared by your family', str(ctx.exception))
        booking.book_session(self.member, self.next_week)  # a new week has its own allowance

    def test_an_ordinary_limit_is_not_shared(self):
        solo = plan(self.org, 'Solo', limit=1, period='week')
        self.put_on(solo)
        self.other.billing_policy = solo
        self.other.save()
        booking.book_session(self.member, self.week[0])
        booking.book_session(self.other, self.week[1])

    def test_a_sibling_on_a_different_plan_does_not_use_up_the_allowance(self):
        shared = family_policy(self.org, session_limit=1, limit_period='week')
        self.put_on(shared)
        self.other.billing_policy = plan(self.org, 'Unlimited', limit=None)
        self.other.save()
        booking.book_session(self.other, self.week[0])
        booking.book_session(self.member, self.week[1])


class FamilyInvoiceTests(FamilyBase):
    def test_flat_family_plan_is_charged_once_on_the_lead_member(self):
        shared = family_policy(self.org, amount=50)
        family.assign_plan(self.member, shared)
        lead = calculate_invoice_amount(self.member, shared, year=2026, month=12)
        other = calculate_invoice_amount(self.other, shared, year=2026, month=12)
        self.assertEqual(lead['net'], Decimal('50'))
        self.assertEqual(lead['covers'], ['Alice Able', 'Bob Best'])
        self.assertEqual(other['net'], Decimal('0'))
        self.assertIn('Covered by the family invoice for Alice Able', other['breakdown'][0]['label'])

    def test_family_discount_applies_once_to_the_family_total(self):
        group = family.family_group(self.member)
        group.discount_percentage = Decimal('10')
        group.save()
        shared = family_policy(self.org, amount=50)
        family.assign_plan(self.member, shared)
        self.assertEqual(calculate_invoice_amount(self.member, shared, year=2026, month=12)['net'], Decimal('45.00'))

    def test_per_session_family_plan_adds_up_every_childs_classes(self):
        shared = family_policy(self.org, amount=0, pricing_model='per_session', per_session_rate=5)
        family.assign_plan(self.member, shared)
        ClassMember.objects.create(assigned_class=self.juniors, member=self.member)
        ClassMember.objects.create(assigned_class=self.juniors, member=self.other)
        self.juniors.schedule = [{'day': 0, 'time': '18:00'}]
        self.juniors.save()
        lead = calculate_invoice_amount(self.member, shared, year=2026, month=12)
        solo_member = calculate_invoice_amount(self.other, shared, year=2026, month=12)
        self.assertEqual(len(lead['breakdown']), 2)
        self.assertTrue(all(row['label'].startswith(('Alice Able:', 'Bob Best:')) for row in lead['breakdown']))
        self.assertEqual(solo_member['net'], Decimal('0'))

    def test_one_child_on_a_family_plan_is_billed_as_normal(self):
        shared = family_policy(self.org, amount=50)
        self.member.billing_policy = shared
        self.member.save()
        result = calculate_invoice_amount(self.member, shared, year=2026, month=12)
        self.assertEqual((result['net'], result['covers']), (Decimal('50'), []))


class FamilyPlanLimitTests(FamilyBase):
    def setUp(self):
        super().setUp()
        self.third = Member.objects.create(organisation=self.org, name='Cat Able')
        family.link(self.member, self.third)

    def test_a_plan_limited_to_two_cannot_be_spread_over_three(self):
        small = family_policy(self.org, 'Duo', max_family_members=2)
        with self.assertRaises(family.FamilyPlanFull):
            family.assign_plan(self.member, small)
        for m in (self.member, self.other, self.third):
            m.refresh_from_db()
            self.assertIsNone(m.billing_policy)

    def test_a_big_enough_limit_or_no_limit_is_fine(self):
        family.assign_plan(self.member, family_policy(self.org, 'Trio', max_family_members=3))
        family.assign_plan(self.member, family_policy(self.org, 'Any'))

    def test_portal_refuses_and_explains(self):
        small = family_policy(self.org, 'Duo', max_family_members=2, member_selectable=True)
        self.act(action='choose_plan', policy_id=small.pk)
        self.member.refresh_from_db()
        self.assertIsNone(self.member.billing_policy)
        page = self.client.get(self.portal('portal_plans'))
        self.assertContains(page, 'covers up to 2 family members')

    def test_staff_assignment_is_refused_too(self):
        small = family_policy(self.org, 'Duo', max_family_members=2)
        self.client.force_login(self.admin)
        self.client.post(reverse('member_billing_policy_set', kwargs={'org_slug': self.org.slug, 'pk': self.member.pk}),
                         {'billing_policy': small.pk})
        self.member.refresh_from_db()
        self.assertIsNone(self.member.billing_policy)

    def test_member_form_stops_a_third_person_joining_a_full_plan(self):
        from members.forms import MemberForm
        small = family_policy(self.org, 'Duo', max_family_members=2)
        for m in (self.member, self.other):
            m.billing_policy = small
            m.save()
        form = MemberForm({'name': 'Cat Able', 'billing_policy': small.pk}, instance=self.third, org=self.org)
        self.assertFalse(form.is_valid())
        self.assertIn('billing_policy', form.errors)

    def test_policy_form_saves_and_validates_the_limit(self):
        create = reverse('billing_policy_create', kwargs={'org_slug': self.org.slug})
        self.client.force_login(self.admin)
        self.client.post(create, {'name': 'Duo', 'billing_cycle': 'monthly', 'amount': '40',
                                  'is_family_plan': '1', 'max_family_members': '2'})
        self.assertEqual(BillingPolicy.objects.get(name='Duo').max_family_members, 2)
        self.client.post(create, {'name': 'Bad', 'billing_cycle': 'monthly', 'amount': '40',
                                  'is_family_plan': '1', 'max_family_members': '1'})
        self.assertFalse(BillingPolicy.objects.filter(name='Bad').exists())

