from datetime import date, timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from classes.models import Class, ClassCoach, ClassMember, Session
from inventory.models import Product, ProductVariant
from members.models import Member
from organisations.features import FEATURES_BY_KEY
from organisations.models import Organisation, OrganisationMember

ALL_ON = {key: True for key in FEATURES_BY_KEY}
WEEKLY = [{'day': 0, 'time': '18:00', 'end_time': '19:00'}]


class CoachCapabilityBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organisation.objects.create(name='Test Club', slug='test-club', features=dict(ALL_ON))
        cls.admin = User.objects.create_user('admin', password='pw')
        OrganisationMember.objects.create(user=cls.admin, organisation=cls.org, role='org_admin')
        cls.coach_user = User.objects.create_user('coach', password='pw')
        cls.coach = OrganisationMember.objects.create(user=cls.coach_user, organisation=cls.org, role='coach')
        cls.other_coach_user = User.objects.create_user('second', password='pw', first_name='Second')
        OrganisationMember.objects.create(user=cls.other_coach_user, organisation=cls.org, role='coach')

        cls.mine = Class.objects.create(organisation=cls.org, name='Mine', schedule=WEEKLY)
        cls.theirs = Class.objects.create(organisation=cls.org, name='Theirs', schedule=WEEKLY)
        ClassCoach.objects.create(assigned_class=cls.mine, user=cls.coach_user)
        cls.session = Session.objects.create(assigned_class=cls.mine, date=date.today())
        cls.their_session = Session.objects.create(assigned_class=cls.theirs, date=date.today())
        cls.member = Member.objects.create(organisation=cls.org, name='New Starter')

        cls.other_org = Organisation.objects.create(name='Other Club', slug='other-club', features=dict(ALL_ON))
        cls.outsider = User.objects.create_user('outsider', password='pw')
        OrganisationMember.objects.create(user=cls.outsider, organisation=cls.other_org, role='org_admin')
        cls.outside_member = Member.objects.create(organisation=cls.other_org, name='Outside Member')

        product = Product.objects.create(organisation=cls.org, name='Adult Gi')
        ProductVariant.objects.create(product=product, size='170cm', price=40, quantity_in_stock=3)
        cls.product = product

    def setUp(self):
        self.client.force_login(self.coach_user)

    def grant(self, *capabilities):
        for capability in capabilities:
            setattr(self.coach, f'can_{capability}', True)
        self.coach.save()

    def url(self, name, **kwargs):
        return reverse(name, kwargs={'org_slug': self.org.slug, **kwargs})


class EnrolFromClassAndRegisterTests(CoachCapabilityBase):
    def enrol(self, cls=None, **extra):
        return self.client.post(
            self.url('class_enrol', pk=(cls or self.mine).pk), {'member_id': self.member.pk, **extra},
        )

    def test_coach_without_permission_cannot_enrol(self):
        self.assertEqual(self.enrol().status_code, 403)
        self.assertFalse(ClassMember.objects.exists())

    def test_coach_with_permission_enrols_on_their_class(self):
        self.grant('manage_class_members')
        response = self.enrol()
        self.assertRedirects(response, self.url('coach_class_detail', pk=self.mine.pk), fetch_redirect_response=False)
        self.assertTrue(ClassMember.objects.filter(assigned_class=self.mine, member=self.member).exists())

    def test_enrolling_from_the_register_returns_to_it(self):
        self.grant('manage_class_members')
        response = self.enrol(next='register', session_pk=self.session.pk)
        self.assertRedirects(
            response, self.url('session_register', pk=self.mine.pk, session_pk=self.session.pk),
            fetch_redirect_response=False,
        )

    def test_a_session_from_another_class_is_not_followed(self):
        self.grant('manage_class_members')
        response = self.enrol(next='register', session_pk=self.their_session.pk)
        self.assertRedirects(response, self.url('coach_class_detail', pk=self.mine.pk), fetch_redirect_response=False)

    def test_permission_does_not_reach_classes_they_do_not_coach(self):
        self.grant('manage_class_members')
        self.assertEqual(self.enrol(cls=self.theirs).status_code, 403)
        self.assertFalse(ClassMember.objects.exists())

    def test_cannot_enrol_another_clubs_member(self):
        self.grant('manage_class_members')
        response = self.client.post(self.url('class_enrol', pk=self.mine.pk), {'member_id': self.outside_member.pk})
        self.assertEqual(response.status_code, 404)
        self.assertFalse(ClassMember.objects.exists())

    def test_other_clubs_admin_cannot_enrol(self):
        self.client.force_login(self.outsider)
        self.assertNotEqual(self.enrol().status_code, 302)
        self.assertFalse(ClassMember.objects.exists())

    def test_admin_still_returns_to_the_class_page(self):
        self.client.force_login(self.admin)
        response = self.enrol(cls=self.theirs)
        self.assertRedirects(response, self.url('class_detail', pk=self.theirs.pk), fetch_redirect_response=False)

    def test_register_offers_quick_add_only_with_permission(self):
        register = self.url('session_register', pk=self.mine.pk, session_pk=self.session.pk)
        response = self.client.get(register)
        self.assertNotContains(response, 'add-member-form')
        self.assertNotContains(response, 'add-coach-form')
        self.grant('manage_class_members')
        response = self.client.get(register)
        self.assertContains(response, 'id="add-member-form"')
        self.assertContains(response, 'New Starter')
        self.assertContains(response, 'id="add-coach-form"')
        self.assertNotContains(response, 'Outside Member')

    def test_coach_page_offers_enrol_only_with_permission(self):
        page = self.url('coach_class_detail', pk=self.mine.pk)
        self.assertNotContains(self.client.get(page), 'name="member_id"')
        self.grant('manage_class_members')
        self.assertContains(self.client.get(page), 'name="member_id"')


class AddCoachTests(CoachCapabilityBase):
    def add(self, user, cls=None):
        return self.client.post(self.url('class_coach_add', pk=(cls or self.mine).pk), {'user_id': user.pk})

    def test_needs_permission(self):
        self.assertEqual(self.add(self.other_coach_user).status_code, 403)
        self.assertEqual(ClassCoach.objects.filter(assigned_class=self.mine).count(), 1)

    def test_coach_with_permission_adds_club_staff(self):
        self.grant('manage_class_members')
        self.add(self.other_coach_user)
        self.assertTrue(ClassCoach.objects.filter(assigned_class=self.mine, user=self.other_coach_user).exists())

    def test_only_this_clubs_staff_can_be_added(self):
        """Even an admin can't make a user from another club (or no club) a coach."""
        self.client.force_login(self.admin)
        stranger = User.objects.create_user('stranger', password='pw')
        for user in (self.outsider, stranger):
            self.assertEqual(self.add(user).status_code, 404)
        self.assertEqual(
            self.client.post(self.url('class_coach_add', pk=self.mine.pk), {'user_id': 'abc'}).status_code, 404,
        )
        self.assertEqual(ClassCoach.objects.filter(assigned_class=self.mine).count(), 1)


class GenerateSessionsTests(CoachCapabilityBase):
    def generate(self, cls=None):
        return self.client.post(self.url('class_generate_sessions', pk=(cls or self.mine).pk), {'weeks': 4})

    def test_needs_permission(self):
        self.assertEqual(self.generate().status_code, 403)
        self.assertEqual(self.mine.sessions.count(), 1)

    def test_coach_with_permission_generates_their_class(self):
        self.grant('generate_sessions')
        response = self.generate()
        self.assertRedirects(response, self.url('coach_class_detail', pk=self.mine.pk), fetch_redirect_response=False)
        self.assertGreaterEqual(self.mine.sessions.count(), 4)
        self.assertEqual(self.mine.sessions.filter(date__gt=date.today() + timedelta(weeks=4)).count(), 0)

    def test_not_for_classes_they_do_not_coach(self):
        self.grant('generate_sessions')
        self.assertEqual(self.generate(cls=self.theirs).status_code, 403)
        self.assertEqual(self.theirs.sessions.count(), 1)

    def test_coach_page_shows_the_control_only_with_permission(self):
        page = self.url('coach_class_detail', pk=self.mine.pk)
        self.assertNotContains(self.client.get(page), 'Generate sessions for the next')
        self.grant('generate_sessions')
        self.assertContains(self.client.get(page), 'Generate sessions for the next')


class ViewStockTests(CoachCapabilityBase):
    def test_coach_without_permission_is_refused(self):
        self.assertEqual(self.client.get(self.url('inventory_list')).status_code, 403)
        self.assertNotContains(self.client.get(self.url('coach_class_list')), self.url('inventory_list'))

    def test_coach_with_permission_sees_stock_read_only(self):
        self.grant('view_stock')
        response = self.client.get(self.url('inventory_list'))
        self.assertContains(response, 'Adult Gi')
        self.assertContains(response, '170cm')
        self.assertNotContains(response, 'New product')
        self.assertNotContains(response, self.url('product_delete', pk=self.product.pk))
        self.assertNotContains(response, 'Adjust stock')
        self.assertContains(self.client.get(self.url('coach_class_list')), self.url('inventory_list'))

    def test_view_stock_does_not_allow_changes(self):
        self.grant('view_stock')
        response = self.client.post(self.url('product_create'), {'name': 'Sneaky', 'category': 'other'})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Product.objects.filter(name='Sneaky').exists())

    def test_admin_keeps_the_controls(self):
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(self.url('inventory_list')), 'New product')

    def test_stock_hidden_when_inventory_switched_off(self):
        self.grant('view_stock')
        self.org.features = {**ALL_ON, 'inventory': False}
        self.org.save(update_fields=['features'])
        self.assertEqual(self.client.get(self.url('inventory_list')).status_code, 404)


class StaffPermissionFormTests(CoachCapabilityBase):
    def test_admin_grants_the_new_permissions(self):
        self.client.force_login(self.admin)
        self.client.post(self.url('org_staff'), {
            'action': 'update_capabilities', 'member_pk': self.coach.pk,
            'capabilities': ['generate_sessions', 'manage_class_members', 'view_stock'],
        })
        self.coach.refresh_from_db()
        self.assertTrue(self.coach.can_generate_sessions)
        self.assertTrue(self.coach.can_manage_class_members)
        self.assertTrue(self.coach.can_view_stock)
        self.assertContains(self.client.get(self.url('org_staff')), 'View stock')
