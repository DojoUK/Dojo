from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from billing import promos
from billing.calculator import calculate_invoice_amount
from billing.models import BillingPolicy, Invoice, MemberDiscount, PolicyDiscount
from members.models import Member, MemberApplication
from organisations.features import FEATURES_BY_KEY
from organisations.models import Organisation, OrganisationMember

ALL_ON = {key: True for key in FEATURES_BY_KEY}


class PromoCodeTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name='Test Club', slug='test-club', features=dict(ALL_ON),
                                               settings={'two_factor': 'optional'})
        self.plan = BillingPolicy.objects.create(organisation=self.org, name='Adult unlimited', billing_cycle='monthly',
                                                 amount=Decimal('40.00'), member_selectable=True)
        self.welcome = PolicyDiscount.objects.create(policy=self.plan, name='Welcome', discount_type='percentage',
                                                     value=Decimal('50'), code='WELCOME', new_members_only=True,
                                                     duration_months=1, show_publicly=True)
        self.member = Member.objects.create(organisation=self.org, name='Alice Able', email='alice@example.com')

    def test_rules(self):
        self.assertEqual(promos.find(self.org, ' welcome '), self.welcome)
        self.assertIsNone(promos.find(self.org, 'NOPE'))
        self.assertEqual(promos.problem(self.welcome, self.member), '')
        Invoice.objects.create(organisation=self.org, member=self.member, amount=10, due_date=date.today(), period='x')
        self.assertIn('new members only', promos.problem(self.welcome, self.member))
        self.welcome.new_members_only = False
        self.welcome.valid_until = date.today() - timedelta(days=1)
        self.assertIn('expired', promos.problem(self.welcome))
        self.welcome.valid_until = None
        self.welcome.max_uses = 1
        MemberDiscount.objects.create(member=Member.objects.create(organisation=self.org, name='B'), discount=self.welcome)
        self.assertIn('as many times', promos.problem(self.welcome))

    def test_a_discount_that_lasts_a_month_stops_after_it(self):
        today = date(2026, 1, 15)
        self.member.billing_policy = self.plan
        self.member.save()
        md = promos.redeem(self.member, self.welcome, today=today)
        self.assertEqual(md.expires_on, date(2026, 2, 15))
        self.assertEqual(calculate_invoice_amount(self.member, self.plan, year=2026, month=2)['net'], Decimal('20.00'))
        self.assertEqual(calculate_invoice_amount(self.member, self.plan, year=2026, month=3)['net'], Decimal('40.00'))

    def test_join_form_takes_a_code_and_approval_applies_it(self):
        url = reverse('member_signup', kwargs={'org_slug': self.org.slug})
        page = self.client.get(url + '?code=welcome').content.decode()
        self.assertIn('Intro offer:', page)
        self.assertIn('value="WELCOME"', page)
        self.assertContains(self.client.post(url, {'first_name': 'Bea', 'surname': 'Bee', 'promo_code': 'WRONG'}),
                            'recognise the promo code WRONG')
        self.client.post(url, {'first_name': 'Bea', 'surname': 'Bee', 'email': 'bea@example.com', 'promo_code': 'welcome'})
        application = MemberApplication.objects.get()
        self.assertEqual(application.promo, self.welcome)
        admin = User.objects.create_user('admin', password='pw')
        OrganisationMember.objects.create(user=admin, organisation=self.org, role='org_admin')
        self.client.force_login(admin)
        self.client.post(reverse('application_approve', kwargs={'org_slug': self.org.slug, 'pk': application.pk}))
        self.assertTrue(MemberDiscount.objects.filter(member__name='Bea Bee', discount=self.welcome).exists())

    def test_portal_code_moves_them_onto_the_plan(self):
        url = reverse('portal_booking_action', kwargs={'token': self.member.token})
        self.client.post(url, {'action': 'apply_code', 'promo_code': 'welcome'})
        self.member.refresh_from_db()
        self.assertEqual(self.member.billing_policy, self.plan)
        self.assertTrue(MemberDiscount.objects.filter(member=self.member, discount=self.welcome).exists())
        response = self.client.post(url, {'action': 'apply_code', 'promo_code': 'welcome'}, follow=True)
        self.assertContains(response, 'already has Welcome')

    def test_staff_create_a_code_and_offers_show_in_find_clubs(self):
        admin = User.objects.create_user('admin', password='pw')
        OrganisationMember.objects.create(user=admin, organisation=self.org, role='org_admin')
        self.client.force_login(admin)
        add = reverse('billing_discount_add', kwargs={'org_slug': self.org.slug, 'pk': self.plan.pk})
        self.client.post(add, {'name': 'Spring', 'discount_type': 'fixed', 'value': '10', 'code': 'spring 26',
                               'max_uses': '20', 'duration_months': '3'})
        spring = PolicyDiscount.objects.get(name='Spring')
        self.assertEqual((spring.code, spring.max_uses, spring.duration_months), ('SPRING26', 20, 3))
        self.client.post(add, {'name': 'Dupe', 'discount_type': 'fixed', 'value': '5', 'code': 'Spring26'})
        self.assertFalse(PolicyDiscount.objects.filter(name='Dupe').exists())
        self.assertEqual(self.welcome.offer_text(), '50% off Adult unlimited for 1 month')
        self.assertEqual(promos.offers_by_club([self.org.pk])[self.org.pk], self.welcome)
