import json
from datetime import date, datetime, timedelta
from datetime import timezone as dt_timezone
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from billing.forecast import linear_forecast, next_months
from billing.models import Expense, Invoice, Payment
from members.models import Member
from organisations.features import FEATURES_BY_KEY
from organisations.models import Organisation, OrganisationMember

ALL_ON = {key: True for key in FEATURES_BY_KEY}


class ExpenseTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organisation.objects.create(name='Test Club', slug='test-club', features=dict(ALL_ON))
        cls.admin = User.objects.create_user('admin', password='pw')
        OrganisationMember.objects.create(user=cls.admin, organisation=cls.org, role='org_admin')
        cls.coach_user = User.objects.create_user('coach', password='pw')
        cls.coach = OrganisationMember.objects.create(user=cls.coach_user, organisation=cls.org, role='coach')
        cls.other_org = Organisation.objects.create(name='Other', slug='other', features=dict(ALL_ON))
        cls.other_admin = User.objects.create_user('other', password='pw')
        OrganisationMember.objects.create(user=cls.other_admin, organisation=cls.other_org, role='org_admin')
        cls.rent = Expense.objects.create(
            organisation=cls.org, description='Hall rent', category='rent', amount=Decimal('350.00'),
            expense_date=date.today(),
        )

    def setUp(self):
        self.client.force_login(self.admin)

    def url(self, name, org=None, **kwargs):
        return reverse(name, kwargs={'org_slug': (org or self.org).slug, **kwargs})


class ExpensePageTests(ExpenseTestBase):
    def test_add_edit_delete(self):
        self.client.post(self.url('expense_add'), {
            'description': 'New mats', 'category': 'equipment', 'amount': '1200.50', 'expense_date': '2026-03-02',
        })
        mats = Expense.objects.get(description='New mats')
        self.assertEqual(mats.amount, Decimal('1200.50'))
        self.assertEqual(mats.created_by, self.admin)
        self.client.post(self.url('expense_edit', pk=mats.pk), {
            'description': 'New mats (6)', 'category': 'equipment', 'amount': '1100', 'expense_date': '2026-03-02',
        })
        mats.refresh_from_db()
        self.assertEqual((mats.description, mats.amount), ('New mats (6)', Decimal('1100.00')))
        self.client.post(self.url('expense_delete', pk=mats.pk))
        self.assertFalse(Expense.objects.filter(pk=mats.pk).exists())

    def test_list_and_category_filter(self):
        Expense.objects.create(organisation=self.org, description='Insurance', category='insurance',
                               amount=Decimal('99'), expense_date=date.today())
        response = self.client.get(self.url('expense_list'), {'category': 'rent'})
        self.assertEqual([e.description for e in response.context['expenses']], ['Hall rent'])
        self.assertEqual(response.context['total_this_month'], Decimal('449.00'))

    def test_bad_amounts_are_refused(self):
        for amount in ('', '-5', '0', 'lots', 'NaN', 'Infinity'):
            self.client.post(self.url('expense_add'), {
                'description': f'Bad {amount}', 'category': 'other', 'amount': amount, 'expense_date': '2026-03-02',
            })
        self.assertEqual(Expense.objects.filter(description__startswith='Bad').count(), 0)

    def test_unknown_category_becomes_other(self):
        self.client.post(self.url('expense_add'), {
            'description': 'Odd', 'category': 'bribes', 'amount': '5', 'expense_date': '2026-03-02',
        })
        self.assertEqual(Expense.objects.get(description='Odd').category, 'other')

    def test_coach_needs_billing_permission(self):
        self.client.force_login(self.coach_user)
        self.assertEqual(self.client.get(self.url('expense_list')).status_code, 403)
        self.assertEqual(self.client.post(self.url('expense_delete', pk=self.rent.pk)).status_code, 403)
        self.coach.can_manage_billing = True
        self.coach.save()
        self.assertEqual(self.client.get(self.url('expense_list')).status_code, 200)

    def test_take_payments_alone_does_not_open_expenses(self):
        self.coach.can_take_payments = True
        self.coach.save()
        self.client.force_login(self.coach_user)
        self.assertEqual(self.client.get(self.url('expense_list')).status_code, 403)

    def test_another_clubs_admin_cannot_touch_them(self):
        self.client.force_login(self.other_admin)
        self.assertNotContains(self.client.get(self.url('expense_list', org=self.other_org)), 'Hall rent')
        self.assertEqual(self.client.post(self.url('expense_delete', org=self.other_org, pk=self.rent.pk)).status_code, 404)
        self.assertEqual(self.client.post(self.url('expense_edit', org=self.other_org, pk=self.rent.pk), {
            'description': 'Hacked', 'category': 'rent', 'amount': '1', 'expense_date': '2026-03-02',
        }).status_code, 404)
        self.assertNotEqual(self.client.get(self.url('expense_list')).status_code, 200)
        self.rent.refresh_from_db()
        self.assertEqual(self.rent.description, 'Hall rent')

    def test_signed_out_visitors_are_sent_to_log_in(self):
        self.client.logout()
        for name, kwargs in (('expense_list', {}), ('expense_add', {}), ('expense_edit', {'pk': self.rent.pk}),
                             ('expense_delete', {'pk': self.rent.pk})):
            response = self.client.post(self.url(name, **kwargs), {'description': 'Hacked', 'amount': '1'})
            self.assertEqual(response.status_code, 302, name)
            self.assertIn(reverse('login'), response['Location'], name)
        self.rent.refresh_from_db()
        self.assertEqual(self.rent.description, 'Hall rent')
        self.assertEqual(Expense.objects.count(), 1)

    def test_off_without_billing(self):
        self.org.features = {**ALL_ON, 'billing': False}
        self.org.save(update_fields=['features'])
        self.assertEqual(self.client.get(self.url('expense_list')).status_code, 404)


class FinanceProfitTests(ExpenseTestBase):
    def test_profit_figures(self):
        today = date.today()
        member = Member.objects.create(organisation=self.org, name='Payer')
        invoice = Invoice.objects.create(organisation=self.org, member=member, amount=Decimal('500'), period='x',
                                         due_date=today)
        Payment.objects.create(invoice=invoice, amount=Decimal('500.00'),
                               paid_at=datetime(today.year, today.month, 1, 12, tzinfo=dt_timezone.utc))
        Expense.objects.create(organisation=self.org, description='Old', amount=Decimal('100'),
                               expense_date=date(today.year - 1, 6, 1))
        response = self.client.get(self.url('org_finance'))
        # Revenue 500; expenses this year 350 (last year's 100 doesn't count).
        self.assertEqual(response.context['total_expenses_ytd'], Decimal('350.00'))
        self.assertEqual(response.context['total_profit_ytd'], Decimal('150.00'))
        self.assertEqual(response.context['profit_this_month'], Decimal('150.00'))
        self.assertEqual(json.loads(response.context['profit_chart_data'])[-1], 150.0)
        labels = json.loads(response.context['forecast_chart_labels'])
        self.assertEqual(len(labels), 18)
        forecast = json.loads(response.context['expense_forecast_series'])
        self.assertEqual(forecast[:11], [None] * 11)
        self.assertEqual(forecast[11], 350.0)  # joins the actual line at this month

    def test_expenses_page_uses_the_same_money_format(self):
        Expense.objects.create(organisation=self.org, description='Mats', amount=Decimal('1200.50'), expense_date=date.today())
        response = self.client.get(self.url('expense_list'))
        self.assertContains(response, '£1,200.50')
        self.assertContains(response, '£1,550.50')   # this year: 350 + 1,200.50

    def test_cards_use_one_money_format(self):
        Expense.objects.create(organisation=self.org, description='Mats', amount=Decimal('2000'), expense_date=date.today())
        response = self.client.get(self.url('org_finance'))
        self.assertContains(response, '£2,350.00')    # expenses this year: 350 + 2,000
        self.assertContains(response, '-£2,350.00')   # no revenue, so a loss
        self.assertNotContains(response, '£-')

    def test_another_clubs_expenses_are_not_counted(self):
        Expense.objects.create(organisation=self.other_org, description='Theirs', amount=Decimal('999'),
                               expense_date=date.today())
        response = self.client.get(self.url('org_finance'))
        self.assertEqual(response.context['total_expenses_ytd'], Decimal('350.00'))


class ForecastMathsTests(TestCase):
    def test_straight_line_carries_on(self):
        self.assertEqual(linear_forecast([10, 20, 30], 2), [40.0, 50.0])

    def test_flat_with_one_month(self):
        self.assertEqual(linear_forecast([5], 3), [5.0, 5.0, 5.0])

    def test_nothing_yet(self):
        self.assertEqual(linear_forecast([], 2), [0.0, 0.0])

    def test_never_below_zero(self):
        self.assertEqual(linear_forecast([30, 20, 10], 2), [0.0, 0.0])

    def test_noisy_line(self):
        # Least squares through (0,2), (1,4), (2,3): slope 0.5, intercept 2.5 → x=3: 4.0, x=4: 4.5
        self.assertEqual(linear_forecast([2, 4, 3], 2), [4.0, 4.5])

    def test_months_roll_into_the_next_year(self):
        self.assertEqual(next_months(date(2026, 11, 1), 3), [date(2026, 12, 1), date(2027, 1, 1), date(2027, 2, 1)])
        self.assertEqual(next_months(date(2026, 1, 1), 0), [])
        self.assertEqual(next_months(date(2026, 1, 1), 1)[0] - date(2026, 1, 1), timedelta(days=31))


class PoundsFilterTests(TestCase):
    def test_formats(self):
        from billing.templatetags.money import pounds
        self.assertEqual(pounds(Decimal('-340')), '-£340.00')
        self.assertEqual(pounds(Decimal('1250.5')), '£1,250.50')
        self.assertEqual(pounds(0), '£0.00')
        self.assertEqual(pounds('not money'), '')
