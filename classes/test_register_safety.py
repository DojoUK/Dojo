from datetime import date, timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from classes.models import Attendance, Class, ClassCoach, ClassMember, Session
from members.models import CustomField, Member
from organisations.features import FEATURES_BY_KEY
from organisations.models import Organisation, OrganisationMember
from organisations.optional_fields import OPTIONAL_FIELDS_BY_KEY
from progression.models import (
    Grading, GradingCandidate, GradingSession, MemberProgression, ProgressionStage, ProgressionSystem,
)

ALL_FEATURES = {key: True for key in FEATURES_BY_KEY}
ALL_FIELDS = {key: True for key in OPTIONAL_FIELDS_BY_KEY}


class RegisterSafetyBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organisation.objects.create(
            name='Test Club', slug='test-club', features=dict(ALL_FEATURES), optional_fields=dict(ALL_FIELDS),
        )
        cls.admin = User.objects.create_user('admin', password='pw')
        OrganisationMember.objects.create(user=cls.admin, organisation=cls.org, role='org_admin')
        cls.coach_user = User.objects.create_user('coach', password='pw', first_name='Cora', last_name='Coach')
        OrganisationMember.objects.create(
            user=cls.coach_user, organisation=cls.org, role='coach',
            emergency_contact_name='Coach Partner', emergency_contact_phone='07700 900111',
            emergency_contact_2_name='Coach Parent', emergency_contact_2_phone='07700 900222',
        )
        cls.cls = Class.objects.create(organisation=cls.org, name='Juniors')
        ClassCoach.objects.create(assigned_class=cls.cls, user=cls.coach_user)

        cls.photo = CustomField.objects.create(
            organisation=cls.org, name='Photo consent', field_type=CustomField.FieldType.BOOLEAN,
            show_on_registers=True,
        )
        cls.alice = Member.objects.create(
            organisation=cls.org, name='Alice A',
            emergency_contact_name='Alice Mum', emergency_contact_phone='07700 900333',
            emergency_contact_2_name='Alice Dad', emergency_contact_2_phone='07700 900444',
            custom_field_values={str(cls.photo.pk): True},
        )
        cls.bob = Member.objects.create(
            organisation=cls.org, name='Bob B', custom_field_values={str(cls.photo.pk): False},
        )
        ClassMember.objects.create(assigned_class=cls.cls, member=cls.alice)
        ClassMember.objects.create(assigned_class=cls.cls, member=cls.bob)
        cls.session = Session.objects.create(assigned_class=cls.cls, date=date.today() - timedelta(days=7))

    def setUp(self):
        self.client.force_login(self.coach_user)

    def url(self, name, **kwargs):
        return reverse(name, kwargs={'org_slug': self.org.slug, **kwargs})

    def register_url(self, session=None):
        return self.url('session_register', pk=self.cls.pk, session_pk=(session or self.session).pk)

    def print_url(self, session=None):
        return self.url('session_print', pk=self.cls.pk, session_pk=(session or self.session).pk)

    def switch_off_fields(self, *keys):
        self.org.optional_fields = {**ALL_FIELDS, **{k: False for k in keys}}
        self.org.save(update_fields=['optional_fields'])


class EmergencyContactsOnRegisterTests(RegisterSafetyBase):
    def test_register_shows_both_member_contacts(self):
        response = self.client.get(self.register_url())
        self.assertContains(response, 'Alice Mum')
        self.assertContains(response, '07700 900333')
        self.assertContains(response, 'Alice Dad')
        self.assertContains(response, 'href="tel:07700 900444"')

    def test_register_shows_both_coach_contacts(self):
        response = self.client.get(self.register_url())
        self.assertContains(response, 'Coach Partner')
        self.assertContains(response, '07700 900222')

    def test_second_contacts_follow_the_field_switches(self):
        self.switch_off_fields('emergency_contact_2', 'staff_emergency_contact_2')
        response = self.client.get(self.register_url())
        self.assertContains(response, 'Alice Mum')
        self.assertNotContains(response, 'Alice Dad')
        self.assertContains(response, 'Coach Partner')
        self.assertNotContains(response, 'Coach Parent')

    def test_staff_contacts_hidden_when_switched_off(self):
        self.switch_off_fields('staff_emergency_contact', 'staff_emergency_contact_2')
        response = self.client.get(self.register_url())
        self.assertNotContains(response, 'Coach Partner')
        self.assertNotContains(response, '07700 900111')

    def test_printed_register_shows_contacts(self):
        response = self.client.get(self.print_url())
        self.assertContains(response, 'Alice Mum: 07700 900333')
        self.assertContains(response, 'Coach Partner: 07700 900111')

    def test_coach_of_another_class_cannot_see_the_register(self):
        other = User.objects.create_user('other', password='pw')
        OrganisationMember.objects.create(user=other, organisation=self.org, role='coach')
        self.client.force_login(other)
        self.assertNotEqual(self.client.get(self.register_url()).status_code, 200)
        self.assertNotEqual(self.client.get(self.print_url()).status_code, 200)


class RegisterFlagTests(RegisterSafetyBase):
    def test_ticked_member_gets_the_badge_on_register_print_and_class_pages(self):
        for response in (
            self.client.get(self.register_url()),
            self.client.get(self.print_url()),
            self.client.get(self.url('coach_class_detail', pk=self.cls.pk)),
        ):
            html = response.content.decode()
            self.assertEqual(html.count('Photo consent'), 1, response.request['PATH_INFO'])
        self.client.force_login(self.admin)
        html = self.client.get(self.url('class_detail', pk=self.cls.pk)).content.decode()
        self.assertEqual(html.count('Photo consent'), 1)

    def test_no_badge_when_the_field_is_not_on_registers(self):
        self.photo.show_on_registers = False
        self.photo.save()
        self.assertNotContains(self.client.get(self.register_url()), 'Photo consent')

    def test_yes_like_text_counts_as_ticked(self):
        self.bob.custom_field_values = {str(self.photo.pk): 'Yes'}
        self.bob.save()
        html = self.client.get(self.register_url()).content.decode()
        self.assertEqual(html.count('Photo consent'), 2)

    def test_admin_switches_a_yes_no_field_onto_registers(self):
        self.client.force_login(self.admin)
        field = CustomField.objects.create(organisation=self.org, name='First aider', field_type='boolean')
        self.client.post(self.url('org_custom_fields'), {'action': 'show_on_registers', 'field_pk': field.pk, 'value': '1'})
        field.refresh_from_db()
        self.assertTrue(field.show_on_registers)
        self.client.post(self.url('org_custom_fields'), {'action': 'show_on_registers', 'field_pk': field.pk, 'value': '0'})
        field.refresh_from_db()
        self.assertFalse(field.show_on_registers)

    def test_new_yes_no_field_can_start_on_registers(self):
        self.client.force_login(self.admin)
        self.client.post(self.url('org_custom_fields'), {
            'action': 'add', 'name': 'Allergy card', 'field_type': 'boolean', 'show_on_registers': 'on',
        })
        self.client.post(self.url('org_custom_fields'), {
            'action': 'add', 'name': 'Shirt size', 'field_type': 'text', 'show_on_registers': 'on',
        })
        self.assertTrue(CustomField.objects.get(name='Allergy card').show_on_registers)
        self.assertFalse(CustomField.objects.get(name='Shirt size').show_on_registers)

    def test_only_yes_no_fields_of_this_club_can_be_switched(self):
        self.client.force_login(self.admin)
        text_field = CustomField.objects.create(organisation=self.org, name='Belt size', field_type='text')
        other_org = Organisation.objects.create(name='Other', slug='other')
        other_field = CustomField.objects.create(organisation=other_org, name='Theirs', field_type='boolean')
        for field in (text_field, other_field):
            response = self.client.post(
                self.url('org_custom_fields'), {'action': 'show_on_registers', 'field_pk': field.pk, 'value': '1'},
            )
            self.assertEqual(response.status_code, 404)
            field.refresh_from_db()
            self.assertFalse(field.show_on_registers)

    def test_coach_cannot_change_register_fields(self):
        self.client.post(self.url('org_custom_fields'), {'action': 'show_on_registers', 'field_pk': self.photo.pk, 'value': '0'})
        self.photo.refresh_from_db()
        self.assertTrue(self.photo.show_on_registers)


class FormerMembersOnPastRegistersTests(RegisterSafetyBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.carl = Member.objects.create(organisation=cls.org, name='Carl Left')
        Attendance.objects.create(session=cls.session, member=cls.carl, present=True)
        cls.upcoming = Session.objects.create(assigned_class=cls.cls, date=date.today() + timedelta(days=7))

    def test_past_register_still_shows_someone_who_left(self):
        response = self.client.get(self.register_url())
        self.assertContains(response, 'Carl Left')
        self.assertContains(response, 'No longer enrolled')
        self.assertIn(self.carl.pk, response.context['present_ids'])
        self.assertContains(response, '2 enrolled')
        printed = self.client.get(self.print_url())
        self.assertContains(printed, 'Carl Left')
        self.assertContains(printed, '(no longer enrolled)')
        self.assertContains(printed, '2 enrolled')

    def test_register_without_their_attendance_leaves_them_out(self):
        response = self.client.get(self.register_url(self.upcoming))
        self.assertNotContains(response, 'Carl Left')

    def test_saving_the_register_keeps_their_mark_editable(self):
        self.client.post(self.register_url(), {
            'present': [self.alice.pk], 'coach_present': [self.coach_user.pk], 'notes': '',
        })
        self.assertFalse(Attendance.objects.get(session=self.session, member=self.carl).present)
        self.assertTrue(Attendance.objects.get(session=self.session, member=self.alice).present)
        # Saving doesn't re-enrol them.
        self.assertFalse(ClassMember.objects.filter(assigned_class=self.cls, member=self.carl).exists())


class CurrentGradeColumnTests(RegisterSafetyBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.kyu = ProgressionSystem.objects.create(organisation=cls.org, name='Kyu', order=0)
        white = ProgressionStage.objects.create(system=cls.kyu, name='White belt', order=0)
        yellow = ProgressionStage.objects.create(system=cls.kyu, name='Yellow belt', order=1, colour='#FFD700')
        mon = ProgressionSystem.objects.create(organisation=cls.org, name='Mon', order=1)
        mon3 = ProgressionStage.objects.create(system=mon, name='3rd Mon', order=0)
        MemberProgression.objects.create(member=cls.alice, stage=white, achieved_date=date(2025, 1, 1))
        MemberProgression.objects.create(member=cls.alice, stage=yellow, achieved_date=date(2026, 1, 1))
        MemberProgression.objects.create(member=cls.alice, stage=mon3, achieved_date=date(2026, 2, 1))

    def test_class_page_shows_latest_grade_in_each_system(self):
        self.client.force_login(self.admin)
        response = self.client.get(self.url('class_detail', pk=self.cls.pk))
        self.assertContains(response, 'Yellow belt')
        self.assertContains(response, '3rd Mon')
        self.assertNotContains(response, 'White belt')
        self.assertContains(response, 'Not graded')  # Bob

    def test_coach_class_page_shows_grades(self):
        response = self.client.get(self.url('coach_class_detail', pk=self.cls.pk))
        self.assertContains(response, 'Yellow belt')

    def test_no_grades_when_grades_switched_off(self):
        self.org.features = {**ALL_FEATURES, 'grades': False}
        self.org.save(update_fields=['features'])
        self.client.force_login(self.admin)
        self.assertNotContains(self.client.get(self.url('class_detail', pk=self.cls.pk)), 'Yellow belt')
        self.assertNotContains(self.client.get(self.url('coach_class_detail', pk=self.cls.pk)), 'Yellow belt')


class GradingPageSafetyTests(RegisterSafetyBase):
    def test_grading_page_shows_medical_info_and_contacts(self):
        self.alice.medical_info = 'MEDICAL-SENTINEL'
        self.alice.save()
        system = ProgressionSystem.objects.create(organisation=self.org, name='Kyu', uses_gradings=True)
        grading = Grading.objects.create(organisation=self.org, name='Winter', system=system)
        session = GradingSession.objects.create(grading=grading, date=date.today())
        GradingCandidate.objects.create(session=session, member=self.alice)
        self.client.force_login(self.admin)
        response = self.client.get(self.url('grading_detail', pk=grading.pk))
        self.assertContains(response, 'MEDICAL-SENTINEL')
        self.assertContains(response, 'Alice Mum')
        self.switch_off_fields('medical_info')
        self.assertNotContains(self.client.get(self.url('grading_detail', pk=grading.pk)), 'MEDICAL-SENTINEL')
