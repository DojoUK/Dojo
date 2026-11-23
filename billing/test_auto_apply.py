from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from billing.calculator import calculate_invoice_amount
from billing.models import BillingPolicy, MemberDiscount, PolicyDiscount
from classes.models import Class, ClassMember
from members.models import Member
from organisations.features import FEATURES_BY_KEY
from organisations.models import Organisation, OrganisationMember

ALL_ON = {key: True for key in FEATURES_BY_KEY}


class AutoApplyTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name='Test Club', slug='test-club', features=dict(ALL_ON),
                                               settings={'two_factor': 'optional'})
        self.plan = BillingPolicy.objects.create(organisation=self.org, name='Adults', billing_cycle='monthly',
                                                 amount=Decimal('40.00'))
        self.auto = PolicyDiscount.objects.create(policy=self.plan, name='Founders', discount_type='fixed',
                                                  value=Decimal('5'), auto_apply=True)
        self.manual = PolicyDiscount.objects.create(policy=self.plan, name='Student', discount_type='percentage',
                                                    value=Decimal('10'))
        self.member = Member.objects.create(organisation=self.org, name='Alice Able', billing_policy=self.plan)

    def net(self, member=None, **period):
        return calculate_invoice_amount(member or self.member, self.plan, **period)['net']

    def test_everyone_on_the_plan_gets_it_once(self):
        self.assertEqual(self.net(), Decimal('35.00'))
        MemberDiscount.objects.create(member=self.member, discount=self.auto)   # also applied by hand: not twice
        self.assertEqual(self.net(), Decimal('35.00'))
        self.assertEqual([l['label'] for l in calculate_invoice_amount(self.member, self.plan)['discount_lines']], ['Founders'])

    def test_members_billed_through_their_class_plan_get_it_too(self):
        other = Member.objects.create(organisation=self.org, name='Bob Best')
        cls = Class.objects.create(organisation=self.org, name='Adults', is_active=True, billing_policy=self.plan)
        ClassMember.objects.create(assigned_class=cls, member=other)
        self.plan.pricing_model = 'flat'
        self.assertEqual(self.net(other), Decimal('35.00'))

    def test_exempt_a_member_and_apply_again(self):
        admin = User.objects.create_user('admin', password='pw')
        OrganisationMember.objects.create(user=admin, organisation=self.org, role='org_admin')
        self.client.force_login(admin)
        detail = reverse('member_detail', kwargs={'org_slug': self.org.slug, 'pk': self.member.pk})
        self.assertContains(self.client.get(detail), "Don't apply")
        self.client.post(reverse('member_discount_exempt', kwargs={
            'org_slug': self.org.slug, 'member_pk': self.member.pk, 'discount_pk': self.auto.pk}))
        self.assertEqual(self.net(), Decimal('40.00'))
        self.assertContains(self.client.get(detail), 'Apply again')
        exemption = MemberDiscount.objects.get(member=self.member, discount=self.auto)
        self.client.post(reverse('member_discount_remove', kwargs={'org_slug': self.org.slug, 'pk': exemption.pk}))
        self.assertEqual(self.net(), Decimal('35.00'))

    def test_one_that_lasts_a_set_time_runs_from_joining(self):
        self.auto.duration_months = 3
        self.auto.save()
        self.member.joined_date = date(2026, 1, 10)
        self.member.save()
        self.assertEqual(self.net(year=2026, month=4), Decimal('35.00'))   # 1 April is before 10 April
        self.assertEqual(self.net(year=2026, month=5), Decimal('40.00'))

    def test_other_plans_and_exempted_rows_dont_leak(self):
        other_plan = BillingPolicy.objects.create(organisation=self.org, name='Kids', billing_cycle='monthly',
                                                  amount=Decimal('25.00'))
        kid = Member.objects.create(organisation=self.org, name='Kid', billing_policy=other_plan)
        self.assertEqual(calculate_invoice_amount(kid, other_plan)['net'], Decimal('25.00'))
        MemberDiscount.objects.create(member=self.member, discount=self.manual)
        self.assertEqual(self.net(), Decimal('31.00'))   # 40 - 4 (10%) - 5
