from datetime import date
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from billing.credit import member_credit
from billing.models import AutoPay, Expense, Invoice, Payment, PaymentSettings
from billing.payment_forms import apply_post
from billing.test_payments import CARD, form
from classes.importers import parse_schedule
from classes.models import Attendance, Class, Session, SessionCoach
from dojo.csv_import import RowError, parse_date, parse_money, parse_yes_no
from members.models import Member
from organisations.features import FEATURES_BY_KEY
from organisations.models import Organisation, OrganisationMember

ALL_ON = {key: True for key in FEATURES_BY_KEY}


class ParserTests(TestCase):
    def test_dates(self):
        for text in ('2026-03-14', '14/03/2026', '14-03-2026', '14-Mar-2026', '14 Mar 2026', '14/03/26', '14 March 2026'):
            self.assertEqual(parse_date(text), date(2026, 3, 14), text)
        for text in ('', '31/02/2026', 'next Tuesday'):
            with self.assertRaises(RowError):
                parse_date(text)

    def test_yes_no(self):
        self.assertTrue(parse_yes_no('Yes'))
        self.assertTrue(parse_yes_no('1'))
        self.assertFalse(parse_yes_no('absent'))
        self.assertFalse(parse_yes_no('', default=False))
        with self.assertRaises(RowError):
            parse_yes_no('maybe')
        with self.assertRaises(RowError):
            parse_yes_no('')

    def test_money(self):
        self.assertEqual(parse_money('£1,250.5'), Decimal('1250.50'))
        self.assertEqual(parse_money('0', allow_zero=True), Decimal('0.00'))
        for text in ('', '-5', '0', 'lots', 'NaN', 'Infinity', '9999999'):
            with self.assertRaises(RowError):
                parse_money(text)

    def test_schedule(self):
        self.assertEqual(parse_schedule('Tue 18:00-18:45, thursday 9.30'), [
            {'day': 1, 'time': '18:00', 'end': '18:45'}, {'day': 3, 'time': '09:30'},
        ])
        self.assertEqual(parse_schedule(''), [])
        for text in ('Funday 18:00', 'Tue 25:00', 'every Tuesday'):
            with self.assertRaises(RowError):
                parse_schedule(text)


class ImportTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organisation.objects.create(name='Test Club', slug='test-club', features=dict(ALL_ON))
        cls.admin = User.objects.create_user('admin', password='pw')
        OrganisationMember.objects.create(user=cls.admin, organisation=cls.org, role='org_admin')
        cls.coach_user = User.objects.create_user('cora', password='pw', first_name='Cora', last_name='Coach')
        cls.coach = OrganisationMember.objects.create(user=cls.coach_user, organisation=cls.org, role='coach')
        cls.juniors = Class.objects.create(organisation=cls.org, name='Juniors', schedule=[{'day': 5, 'time': '10:00'}])
        cls.alice = Member.objects.create(organisation=cls.org, name='Alice Able')
        cls.bob = Member.objects.create(organisation=cls.org, name='Bob Best')
        cls.other = Organisation.objects.create(name='Other', slug='other', features=dict(ALL_ON))
        cls.other_admin = User.objects.create_user('other', password='pw')
        OrganisationMember.objects.create(user=cls.other_admin, organisation=cls.other, role='org_admin')
        cls.outsider = Member.objects.create(organisation=cls.other, name='Out Sider')

    def setUp(self):
        self.client.force_login(self.admin)

    def url(self, kind=None, org=None):
        org = org or self.org
        if kind is None:
            return reverse('org_import', kwargs={'org_slug': org.slug})
        return reverse('org_import_kind', kwargs={'org_slug': org.slug, 'kind': kind})

    def preview(self, kind, text, org=None):
        return self.client.post(self.url(kind, org), {'action': 'preview', 'raw_csv': text})

    def do_import(self, kind, text, org=None):
        return self.client.post(self.url(kind, org), {'action': 'import', 'raw_csv': text})


class ClassImportTests(ImportTestBase):
    CSV = 'Name,Schedule,Max Capacity\nSeniors,"Tue 19:00-20:30, Thu 19:00-20:30",24\njuniors,Sat 09:00-10:00,\n'

    def test_preview_changes_nothing(self):
        response = self.preview('classes', self.CSV)
        self.assertEqual(response.context['counts']['create'], 1)
        self.assertEqual(response.context['counts']['update'], 1)
        self.assertContains(response, 'Add Seniors')
        self.assertContains(response, 'Update Juniors')
        self.assertEqual(Class.objects.filter(organisation=self.org).count(), 1)

    def test_import_adds_and_updates(self):
        response = self.do_import('classes', self.CSV)
        self.assertRedirects(response, reverse('class_list', kwargs={'org_slug': self.org.slug}), fetch_redirect_response=False)
        seniors = Class.objects.get(organisation=self.org, name='Seniors')
        self.assertEqual(seniors.max_capacity, 24)
        self.assertEqual(len(seniors.schedule), 2)
        self.juniors.refresh_from_db()
        self.assertEqual(self.juniors.schedule, [{'day': 5, 'time': '09:00', 'end': '10:00'}])

    def test_bad_rows_are_reported_and_left_out(self):
        response = self.preview('classes', 'name,schedule,max_capacity\nA,Funday 10:00,\nB,,lots\nC,,\nC,,\n')
        self.assertEqual(response.context['counts'], {'create': 1, 'update': 0, 'skip': 0, 'error': 3})
        self.assertContains(response, 'Not imported: schedule &quot;Funday 10:00&quot;')
        self.do_import('classes', 'name,schedule,max_capacity\nA,Funday 10:00,\nC,,\n')
        self.assertEqual(sorted(Class.objects.filter(organisation=self.org).values_list('name', flat=True)), ['C', 'Juniors'])

    def test_updates_only_what_the_file_gives(self):
        self.juniors.description = 'Ages 8 to 12'
        self.juniors.max_capacity = 20
        self.juniors.save()
        # No description or capacity column, and a blank schedule cell.
        response = self.preview('classes', 'name,schedule\nJuniors,\n')
        self.assertContains(response, 'Juniors: nothing to change')
        self.do_import('classes', 'name,max_capacity\nJuniors,24\n')
        self.juniors.refresh_from_db()
        self.assertEqual(self.juniors.description, 'Ages 8 to 12')
        self.assertEqual(self.juniors.schedule, [{'day': 5, 'time': '10:00'}])
        self.assertEqual(self.juniors.max_capacity, 24)

    def test_missing_column(self):
        response = self.preview('classes', 'title\nX\n')
        self.assertContains(response, 'The CSV needs these columns: name.')


class RegisterImportTests(ImportTestBase):
    def test_marks_attendance_and_adds_sessions(self):
        csv = ('class_name,date,member_name,present\n'
               'Juniors,14/03/2026,Alice Able,yes\n'   # a Saturday: on the schedule
               'Juniors,14/03/2026,Best Bob,\n'        # reversed name, blank = absent
               'Juniors,17/03/2026,Alice Able,y\n')    # a Tuesday: an extra session
        self.do_import('registers', csv)
        saturday = Session.objects.get(assigned_class=self.juniors, date=date(2026, 3, 14))
        tuesday = Session.objects.get(assigned_class=self.juniors, date=date(2026, 3, 17))
        self.assertFalse(saturday.is_extra)
        self.assertTrue(tuesday.is_extra)
        self.assertTrue(Attendance.objects.get(session=saturday, member=self.alice).present)
        self.assertFalse(Attendance.objects.get(session=saturday, member=self.bob).present)

    def test_importing_again_updates_rather_than_duplicates(self):
        self.do_import('registers', 'class_name,date,member_name,present\nJuniors,2026-03-14,Alice Able,no\n')
        self.do_import('registers', 'class_name,date,member_name,present\nJuniors,2026-03-14,Alice Able,yes\n')
        self.assertEqual(Attendance.objects.count(), 1)
        self.assertTrue(Attendance.objects.get().present)
        self.assertEqual(Session.objects.filter(assigned_class=self.juniors).count(), 1)

    def test_unknown_class_member_or_other_clubs_member(self):
        response = self.preview('registers', (
            'class_name,date,member_name,present\n'
            'Zumba,2026-03-14,Alice Able,yes\nJuniors,2026-03-14,Nobody,yes\nJuniors,2026-03-14,Out Sider,yes\n'
        ))
        self.assertEqual(response.context['counts']['error'], 3)
        self.do_import('registers', 'class_name,date,member_name,present\nJuniors,2026-03-14,Out Sider,yes\n')
        self.assertFalse(Attendance.objects.exists())


    def test_two_members_with_one_name_are_never_guessed(self):
        twin = Member.objects.create(organisation=self.org, name='Alice Able', email='twin@example.com')
        response = self.preview('registers', 'class_name,date,member_name,present\nJuniors,2026-03-14,Alice Able,yes\n')
        self.assertContains(response, '2 members are called &quot;Alice Able&quot;')
        self.do_import('registers', (
            'class_name,date,member_name,member_email,present\nJuniors,2026-03-14,Alice Able,TWIN@example.com,yes\n'
        ))
        self.assertEqual(Attendance.objects.get().member, twin)


class CoachAttendanceImportTests(ImportTestBase):
    def test_matches_staff_by_name_or_username(self):
        self.do_import('coach-attendance', (
            'class_name,date,coach_name,present\nJuniors,2026-03-14,Cora Coach,yes\nJuniors,2026-03-21,cora,no\n'
        ))
        marks = dict(SessionCoach.objects.values_list('session__date', 'present'))
        self.assertEqual(marks, {date(2026, 3, 14): True, date(2026, 3, 21): False})

    def test_two_staff_with_one_name_are_never_guessed(self):
        namesake = User.objects.create_user('cora2', password='pw', first_name='Cora', last_name='Coach')
        OrganisationMember.objects.create(user=namesake, organisation=self.org, role='coach')
        response = self.preview('coach-attendance', 'class_name,date,coach_name\nJuniors,2026-03-14,Cora Coach\n')
        self.assertContains(response, 'use their username instead')
        self.do_import('coach-attendance', 'class_name,date,coach_name,present\nJuniors,2026-03-14,cora2,yes\n')
        self.assertEqual(SessionCoach.objects.get().coach, namesake)

    def test_other_clubs_staff_are_not_matched(self):
        response = self.preview('coach-attendance', 'class_name,date,coach_name\nJuniors,2026-03-14,other\n')
        self.assertEqual(response.context['counts']['error'], 1)


@override_settings(STRIPE_PUBLIC_KEY='', STRIPE_SECRET_KEY='', STRIPE_WEBHOOK_SECRET='')
class InvoiceImportTests(ImportTestBase):
    CSV = ('member_name,amount,period,due_date,paid_date,payment_method,paid_amount\n'
           'Alice Able,30.00,January 2026,31/01/2026,28/01/2026,bank transfer,\n'
           'Bob Best,30.00,January 2026,31/01/2026,,,\n'
           'Bob Best,30.00,February 2026,28/02/2026,20/02/2026,cash,10\n')

    def test_invoices_and_payments(self):
        self.do_import('invoices', self.CSV)
        alice = Invoice.objects.get(member=self.alice)
        self.assertEqual((alice.amount, alice.status), (Decimal('30.00'), 'paid'))
        payment = alice.payments.get()
        self.assertEqual((payment.amount, payment.method, payment.paid_at.date()), (Decimal('30.00'), 'bacs', date(2026, 1, 28)))
        jan = Invoice.objects.get(member=self.bob, period='January 2026')
        self.assertEqual((jan.status, jan.payments.count()), ('unpaid', 0))
        feb = Invoice.objects.get(member=self.bob, period='February 2026')
        self.assertEqual(feb.status, 'unpaid')         # £10 of £30 paid
        self.assertEqual(feb.balance_due, Decimal('20.00'))

    def test_existing_invoices_are_skipped(self):
        self.do_import('invoices', self.CSV)
        response = self.preview('invoices', self.CSV)
        self.assertEqual(response.context['counts']['skip'], 3)
        self.do_import('invoices', self.CSV)
        self.assertEqual(Invoice.objects.count(), 3)
        self.assertEqual(Payment.objects.count(), 2)

    def test_imported_invoices_are_not_collected_or_credited(self):
        payment_settings = PaymentSettings(organisation=self.org)
        apply_post(payment_settings, {**CARD, **form(auto_pay='optional')})
        payment_settings.save()
        AutoPay.objects.create(member=self.bob, payment_method_id='pm_1', method_type='card', label='Visa')
        overpaid = Invoice.objects.create(organisation=self.org, member=self.bob, amount=10, period='Old', due_date=date(2025, 1, 1))
        Payment.objects.create(invoice=overpaid, amount=Decimal('25'), paid_at=timezone.now())
        with self.captureOnCommitCallbacks(execute=True):
            self.do_import('invoices', 'member_name,amount,period,due_date\nBob Best,30,March 2026,2026-03-31\n')
        imported = Invoice.objects.get(member=self.bob, period='March 2026')
        self.assertEqual(imported.autopay_status, '')
        self.assertEqual(imported.amount, Decimal('30.00'))

    def test_an_imported_overpayment_is_not_new_credit(self):
        self.do_import('invoices', 'member_name,amount,period,due_date,paid_date,paid_amount\nAlice Able,30,Jan,2026-01-31,2026-01-20,45\n')
        invoice = Invoice.objects.get(member=self.alice)
        self.assertEqual(invoice.amount_paid, Decimal('45.00'))
        self.assertEqual(member_credit(self.alice), Decimal('0'))

    def test_a_payment_needs_a_date(self):
        response = self.preview('invoices', 'member_name,amount,period,due_date,paid_amount\nAlice Able,30,Jan,2026-01-31,30\n')
        self.assertContains(response, 'paid_amount needs a paid_date')

    def test_problems(self):
        response = self.preview('invoices', (
            'member_name,amount,period,due_date,status,payment_method\n'
            'Nobody,30,Jan,2026-01-31,,\nAlice Able,lots,Jan,2026-01-31,,\nAlice Able,30,Jan,soon,,\n'
            'Alice Able,30,Jan,2026-01-31,refunded,\nAlice Able,30,Jan,2026-01-31,,cheque\nOut Sider,30,Jan,2026-01-31,,\n'
        ))
        self.assertEqual(response.context['counts']['error'], 6)


class ExpenseImportTests(ImportTestBase):
    CSV = 'description,amount,expense_date,category\nHall rent,350,01/03/2026,Rent & venue hire\nMats,1200.50,2026-03-14,\n'

    def test_expenses(self):
        self.do_import('expenses', self.CSV)
        self.assertEqual(
            sorted(Expense.objects.values_list('description', 'amount', 'category')),
            [('Hall rent', Decimal('350.00'), 'rent'), ('Mats', Decimal('1200.50'), 'other')],
        )
        self.assertEqual(Expense.objects.first().created_by, self.admin)

    def test_importing_again_skips_them(self):
        self.do_import('expenses', self.CSV)
        self.do_import('expenses', self.CSV)
        self.assertEqual(Expense.objects.count(), 2)


class ImportAccessTests(ImportTestBase):
    def test_coach_needs_permission(self):
        self.client.force_login(self.coach_user)
        self.assertEqual(self.client.get(self.url()).status_code, 403)
        self.assertEqual(self.client.get(self.url('expenses')).status_code, 403)
        self.coach.can_manage_billing = True
        self.coach.save()
        hub = self.client.get(self.url())
        self.assertEqual([i.key for i in hub.context['importers']], ['invoices', 'expenses'])
        self.assertEqual(self.client.get(self.url('invoices')).status_code, 200)
        for kind in ('classes', 'registers', 'coach-attendance'):
            self.assertEqual(self.client.get(self.url(kind)).status_code, 403, kind)
            self.assertEqual(self.do_import(kind, 'name\nSneaky\n').status_code, 403, kind)
        self.assertFalse(Class.objects.filter(name='Sneaky').exists())

    def test_another_clubs_admin_cannot_import_here(self):
        self.client.force_login(self.other_admin)
        response = self.do_import('expenses', 'description,amount,expense_date\nTheirs,5,2026-01-01\n')
        self.assertIn(response.status_code, (403, 404))
        self.assertFalse(Expense.objects.exists())

    def test_imports_stay_in_their_own_club(self):
        self.client.force_login(self.other_admin)
        self.do_import('expenses', 'description,amount,expense_date\nTheirs,5,2026-01-01\n', org=self.other)
        self.assertEqual(Expense.objects.get().organisation, self.other)

    def test_billing_imports_need_billing(self):
        self.org.features = {**ALL_ON, 'billing': False}
        self.org.save(update_fields=['features'])
        self.assertEqual(self.client.get(self.url('invoices')).status_code, 403)
        self.assertNotIn('invoices', [i.key for i in self.client.get(self.url()).context['importers']])

    def test_unknown_kind(self):
        self.assertEqual(self.client.get(self.url('passwords')).status_code, 404)

    def test_file_limits(self):
        big = SimpleUploadedFile('big.csv', b'description,amount,expense_date\n' + b'x' * 10)
        with mock.patch('dojo.csv_import.MAX_CSV_BYTES', 5):
            response = self.client.post(self.url('expenses'), {'action': 'preview', 'csv_file': big})
        self.assertContains(response, 'too big')
        bad = SimpleUploadedFile('bad.csv', 'description,amount,expense_date\nCafé,5,2026-01-01\n'.encode('latin-1'))
        self.assertContains(self.client.post(self.url('expenses'), {'action': 'preview', 'csv_file': bad}), 'UTF-8')

    def test_a_cell_too_long_to_read_is_reported(self):
        huge = 'description,amount,expense_date\n"' + 'x' * 200_000 + '",5,2026-01-01\n'
        with mock.patch('dojo.csv_import.MAX_CSV_BYTES', 10 * 1024 * 1024):
            response = self.preview('expenses', huge)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'couldn&#x27;t be read')
        self.assertFalse(Expense.objects.exists())

    def test_signed_out_visitors_are_sent_to_log_in(self):
        self.client.logout()
        for url in (self.url(), self.url('expenses')):
            response = self.client.post(url, {'action': 'import', 'raw_csv': 'description,amount,expense_date\nX,5,2026-01-01\n'})
            self.assertEqual(response.status_code, 302, url)
            self.assertIn(reverse('login'), response['Location'])
        self.assertFalse(Expense.objects.exists())

    def test_upload_and_preview_from_a_file(self):
        upload = SimpleUploadedFile('e.csv', '﻿Description,Amount,Expense Date\nRent,350,2026-03-01\n'.encode('utf-8'))
        response = self.client.post(self.url('expenses'), {'action': 'preview', 'csv_file': upload})
        self.assertEqual(response.context['counts']['create'], 1)
        self.assertFalse(Expense.objects.exists())
