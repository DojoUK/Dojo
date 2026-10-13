from datetime import date, timedelta

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase
from django.urls import reverse

from billing.models import Invoice
from classes.models import Attendance, Class, ClassCoach, ClassMember, Session
from members.models import Member
from organisations.models import Organisation, OrganisationMember


class CapabilityTestBase(TestCase):
    """
    One org with two classes. The coach coaches class A only; member A is
    enrolled in class A and member B in class B, each with one invoice.
    """

    @classmethod
    def setUpTestData(cls):
        cls.org = Organisation.objects.create(name='Test Club', slug='test-club')
        cls.admin = User.objects.create_user('admin', password='pw')
        OrganisationMember.objects.create(user=cls.admin, organisation=cls.org, role='org_admin')
        cls.coach_user = User.objects.create_user('coach', password='pw')
        cls.coach = OrganisationMember.objects.create(user=cls.coach_user, organisation=cls.org, role='coach')

        cls.class_a = Class.objects.create(organisation=cls.org, name='Class A')
        cls.class_b = Class.objects.create(organisation=cls.org, name='Class B')
        ClassCoach.objects.create(assigned_class=cls.class_a, user=cls.coach_user)

        cls.member_a = Member.objects.create(organisation=cls.org, name='Alice A', email='alice@example.com')
        cls.member_b = Member.objects.create(organisation=cls.org, name='Bob B', email='bob@example.com')
        ClassMember.objects.create(assigned_class=cls.class_a, member=cls.member_a)
        ClassMember.objects.create(assigned_class=cls.class_b, member=cls.member_b)

        due = date.today() + timedelta(days=14)
        cls.invoice_a = Invoice.objects.create(
            organisation=cls.org, member=cls.member_a, amount=30, period='October', due_date=due,
        )
        cls.invoice_b = Invoice.objects.create(
            organisation=cls.org, member=cls.member_b, amount=30, period='October', due_date=due,
        )

    def setUp(self):
        self.client.force_login(self.coach_user)

    def grant(self, *capabilities):
        for capability in capabilities:
            setattr(self.coach, f'can_{capability}', True)
        self.coach.save()

    def url(self, name, **kwargs):
        return reverse(name, kwargs={'org_slug': self.org.slug, **kwargs})


class HasCapabilityTests(CapabilityTestBase):
    def test_admin_holds_every_capability(self):
        admin_membership = OrganisationMember.objects.get(user=self.admin)
        for name, _, _ in OrganisationMember.CAPABILITIES:
            self.assertTrue(admin_membership.has_capability(name))

    def test_coach_holds_only_granted_capabilities(self):
        self.assertFalse(self.coach.has_capability('take_payments'))
        self.grant('take_payments')
        self.assertTrue(self.coach.has_capability('take_payments'))
        self.assertFalse(self.coach.has_capability('manage_billing'))

    def test_unknown_capability_is_an_error(self):
        with self.assertRaises(ValueError):
            self.coach.has_capability('launch_rockets')


class BillingAccessTests(CapabilityTestBase):
    def test_plain_coach_has_no_billing_access(self):
        self.assertEqual(self.client.get(self.url('invoice_list')).status_code, 403)
        self.assertEqual(self.client.get(self.url('invoice_detail', pk=self.invoice_a.pk)).status_code, 403)
        self.assertEqual(self.client.get(self.url('billing_policies')).status_code, 403)
        self.assertEqual(self.client.get(self.url('org_finance')).status_code, 403)

    def test_take_payments_sees_only_own_class_members(self):
        self.grant('take_payments')
        response = self.client.get(self.url('invoice_list'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context['invoices']), [self.invoice_a])
        self.assertContains(response, 'Alice A')
        self.assertNotContains(response, 'Bob B')

        self.assertEqual(self.client.get(self.url('invoice_detail', pk=self.invoice_a.pk)).status_code, 200)
        self.assertEqual(self.client.get(self.url('invoice_detail', pk=self.invoice_b.pk)).status_code, 404)

    def test_take_payments_can_record_payment_for_own_member_only(self):
        self.grant('take_payments')
        payment = {'amount': '30', 'method': 'cash'}
        self.client.post(self.url('invoice_record_payment', pk=self.invoice_a.pk), payment)
        self.invoice_a.refresh_from_db()
        self.assertEqual(self.invoice_a.status, Invoice.Status.PAID)

        response = self.client.post(self.url('invoice_record_payment', pk=self.invoice_b.pk), payment)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.invoice_b.payments.count(), 0)

        response = self.client.post(self.url('invoice_mark_paid', pk=self.invoice_b.pk))
        self.assertEqual(response.status_code, 404)

    def test_take_payments_cannot_manage_billing(self):
        self.grant('take_payments')
        self.assertEqual(self.client.post(self.url('invoice_mark_unpaid', pk=self.invoice_a.pk)).status_code, 403)
        self.assertEqual(self.client.get(self.url('invoice_create')).status_code, 403)
        self.assertEqual(self.client.get(self.url('billing_policies')).status_code, 403)
        self.assertEqual(self.client.get(self.url('billing_export')).status_code, 403)
        self.assertEqual(self.client.get(self.url('org_finance')).status_code, 403)

    def test_manage_billing_covers_whole_club(self):
        self.grant('manage_billing')
        response = self.client.get(self.url('invoice_list'))
        self.assertEqual(set(response.context['invoices']), {self.invoice_a, self.invoice_b})
        self.assertEqual(self.client.get(self.url('billing_policies')).status_code, 200)
        self.assertEqual(self.client.get(self.url('org_finance')).status_code, 200)

    def test_non_admin_billing_pages_do_not_link_to_admin_member_page(self):
        self.grant('manage_billing')
        member_url = reverse('member_detail', kwargs={'org_slug': self.org.slug, 'pk': self.member_a.pk})
        self.assertNotContains(self.client.get(self.url('invoice_list')), member_url)
        self.assertNotContains(self.client.get(self.url('invoice_detail', pk=self.invoice_a.pk)), member_url)


class AttendanceAccessTests(CapabilityTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        yesterday = date.today() - timedelta(days=1)
        for cls_, member in ((cls.class_a, cls.member_a), (cls.class_b, cls.member_b)):
            session = Session.objects.create(assigned_class=cls_, date=yesterday)
            Attendance.objects.create(session=session, member=member, present=True)

    def test_coach_sees_only_own_classes(self):
        response = self.client.get(self.url('attendance_analytics'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual([m.pk for m in response.context['members']], [self.member_a.pk])
        self.assertEqual(list(response.context['classes']), [self.class_a])
        self.assertEqual(response.context['total_sessions_4w'], 1)

    def test_coach_cannot_filter_to_another_class(self):
        response = self.client.get(self.url('attendance_analytics'), {'class': self.class_b.pk})
        self.assertEqual([m.pk for m in response.context['members']], [self.member_a.pk])

    def test_export_is_scoped_to_own_classes(self):
        content = self.client.get(self.url('attendance_export')).content.decode()
        self.assertIn('Alice A', content)
        self.assertNotIn('Bob B', content)

        content = self.client.get(self.url('attendance_export'), {'class': self.class_b.pk}).content.decode()
        self.assertNotIn('Bob B', content)

    def test_view_all_attendance_sees_every_class(self):
        self.grant('view_all_attendance')
        response = self.client.get(self.url('attendance_analytics'))
        self.assertEqual({m.pk for m in response.context['members']}, {self.member_a.pk, self.member_b.pk})
        self.assertIn('Bob B', self.client.get(self.url('attendance_export')).content.decode())


class AnnouncementAccessTests(CapabilityTestBase):
    def send(self, **data):
        return self.client.post(self.url('org_announcements'), {'subject': 'Hello', 'body': 'Training on', **data})

    def test_coach_can_message_own_class(self):
        self.send(recipient_type='class', class_pk=self.class_a.pk)
        self.assertEqual([m.to for m in mail.outbox], [['alice@example.com']])

    def test_coach_cannot_message_other_class_or_everyone(self):
        self.assertEqual(self.send(recipient_type='class', class_pk=self.class_b.pk).status_code, 404)
        self.assertEqual(self.send(recipient_type='all', class_pk=self.class_b.pk).status_code, 404)
        self.send(recipient_type='all')
        self.assertEqual(mail.outbox, [])

    def test_message_all_members_can_message_everyone(self):
        self.grant('message_all_members')
        self.send(recipient_type='all')
        self.assertEqual(sorted(m.to[0] for m in mail.outbox), ['alice@example.com', 'bob@example.com'])

    def test_class_recipient_without_class_does_not_fall_back_to_everyone(self):
        self.client.force_login(self.admin)
        self.send(recipient_type='class', class_pk='')
        self.assertEqual(mail.outbox, [])


class StaffPermissionsFormTests(CapabilityTestBase):
    def test_admin_can_toggle_capabilities(self):
        self.client.force_login(self.admin)
        self.client.post(self.url('org_staff'), {
            'action': 'update_capabilities',
            'member_pk': self.coach.pk,
            'capabilities': ['take_payments', 'view_all_attendance'],
        })
        self.coach.refresh_from_db()
        self.assertTrue(self.coach.can_take_payments)
        self.assertTrue(self.coach.can_view_all_attendance)
        self.assertFalse(self.coach.can_manage_billing)

        self.client.post(self.url('org_staff'), {'action': 'update_capabilities', 'member_pk': self.coach.pk})
        self.coach.refresh_from_db()
        self.assertEqual(self.coach.granted_capabilities(), [])

    def test_coach_cannot_change_permissions(self):
        response = self.client.post(self.url('org_staff'), {
            'action': 'update_capabilities',
            'member_pk': self.coach.pk,
            'capabilities': ['manage_billing'],
        })
        self.assertEqual(response.status_code, 403)
        self.coach.refresh_from_db()
        self.assertFalse(self.coach.can_manage_billing)


class PageRenderingTests(CapabilityTestBase):
    def test_staff_page_shows_permission_toggles_for_coaches(self):
        self.client.force_login(self.admin)
        response = self.client.get(self.url('org_staff'))
        self.assertContains(response, 'Save permissions', count=1)
        self.assertContains(response, 'name="capabilities" value="manage_billing"')

    def test_announcement_form_hides_everyone_option_from_plain_coach(self):
        response = self.client.get(self.url('org_announcements'))
        self.assertNotContains(response, 'All active members</option>')
        self.assertContains(response, 'Class A')
        self.assertNotContains(response, 'Class B')

    def test_coach_sidebar_follows_capabilities(self):
        invoices_url = self.url('invoice_list')
        response = self.client.get(self.url('org_dashboard'))
        self.assertContains(response, self.url('attendance_analytics'))
        self.assertNotContains(response, invoices_url)

        self.grant('take_payments')
        response = self.client.get(self.url('org_dashboard'))
        self.assertContains(response, invoices_url)
        self.assertNotContains(response, self.url('billing_policies'))


class TenantBoundaryTests(CapabilityTestBase):
    """
    Tenant-matrix rows for the routes this change touches: organisation B's
    admin and anonymous callers are refused on A's routes, and B's object
    references posted to A's routes are refused without changing anything.
    """
    SENTINEL = 'SENTINEL-B-9d2e'

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.org_b = Organisation.objects.create(name='Other Club', slug='other-club')
        cls.admin_b = User.objects.create_user('admin-b', password='pw')
        OrganisationMember.objects.create(user=cls.admin_b, organisation=cls.org_b, role='org_admin')
        cls.coach_b = OrganisationMember.objects.create(
            user=User.objects.create_user('coach-b', password='pw'), organisation=cls.org_b, role='coach',
        )
        cls.class_other = Class.objects.create(organisation=cls.org_b, name='Other Class')
        cls.member_other = Member.objects.create(
            organisation=cls.org_b, name=cls.SENTINEL, email='other@example.com',
        )
        ClassMember.objects.create(assigned_class=cls.class_other, member=cls.member_other)
        cls.invoice_other = Invoice.objects.create(
            organisation=cls.org_b, member=cls.member_other, amount=30, period='October',
            due_date=date.today() + timedelta(days=14),
        )

    def org_a_get_routes(self):
        return [
            self.url('invoice_list'),
            self.url('invoice_detail', pk=self.invoice_a.pk),
            self.url('org_finance'),
            self.url('attendance_analytics'),
            self.url('attendance_export'),
            self.url('org_announcements'),
            self.url('org_staff'),
        ]

    def org_a_post_routes(self):
        return [
            self.url('invoice_record_payment', pk=self.invoice_a.pk),
            self.url('invoice_mark_paid', pk=self.invoice_a.pk),
            self.url('org_announcements'),
            self.url('org_staff'),
        ]

    def test_other_org_admin_is_refused(self):
        self.client.force_login(self.admin_b)
        for route in self.org_a_get_routes() + self.org_a_post_routes():
            with self.subTest(route=route):
                response = self.client.post(route) if route in self.org_a_post_routes() else self.client.get(route)
                self.assertEqual(response.status_code, 403)
                self.assertNotContains(response, 'Alice A', status_code=403)
        self.invoice_a.refresh_from_db()
        self.assertEqual(self.invoice_a.status, Invoice.Status.UNPAID)
        self.assertEqual(self.invoice_a.payments.count(), 0)
        self.assertEqual(mail.outbox, [])

    def test_anonymous_is_sent_to_login(self):
        self.client.logout()
        for route in self.org_a_get_routes():
            with self.subTest(route=route):
                response = self.client.get(route)
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse('login'), response['Location'])

    def test_other_org_invoice_is_refused_on_org_a_routes(self):
        self.grant('manage_billing')
        self.assertEqual(self.client.get(self.url('invoice_detail', pk=self.invoice_other.pk)).status_code, 404)
        for name in ('invoice_record_payment', 'invoice_mark_paid'):
            with self.subTest(route=name):
                response = self.client.post(self.url(name, pk=self.invoice_other.pk), {'amount': '30'})
                self.assertEqual(response.status_code, 404)
        self.invoice_other.refresh_from_db()
        self.assertEqual(self.invoice_other.status, Invoice.Status.UNPAID)
        self.assertEqual(self.invoice_other.payments.count(), 0)
        self.assertNotContains(self.client.get(self.url('invoice_list')), self.SENTINEL)

    def test_other_org_class_is_refused_for_announcements(self):
        self.client.force_login(self.admin)
        response = self.client.post(self.url('org_announcements'), {
            'subject': 'Hello', 'body': 'Training on', 'recipient_type': 'class', 'class_pk': self.class_other.pk,
        })
        self.assertEqual(response.status_code, 404)
        self.assertEqual(mail.outbox, [])

    def test_other_org_class_filter_is_ignored_for_attendance(self):
        self.client.force_login(self.admin)
        response = self.client.get(self.url('attendance_analytics'), {'class': self.class_other.pk})
        self.assertNotContains(response, self.SENTINEL)
        content = self.client.get(self.url('attendance_export'), {'class': self.class_other.pk}).content.decode()
        self.assertNotIn(self.SENTINEL, content)

    def test_other_org_staff_member_permissions_cannot_be_changed(self):
        self.client.force_login(self.admin)
        response = self.client.post(self.url('org_staff'), {
            'action': 'update_capabilities',
            'member_pk': self.coach_b.pk,
            'capabilities': ['manage_billing'],
        })
        self.assertEqual(response.status_code, 404)
        self.coach_b.refresh_from_db()
        self.assertFalse(self.coach_b.can_manage_billing)

    def test_coach_with_no_classes_sees_no_members(self):
        self.client.force_login(self.coach_b.user)
        response = self.client.get(reverse('attendance_analytics', kwargs={'org_slug': self.org_b.slug}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context['members']), [])
        self.assertNotContains(response, self.SENTINEL)
