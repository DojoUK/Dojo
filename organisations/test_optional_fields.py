from datetime import date, timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from classes.models import Class, ClassMember, Session
from members.models import Guardian, Member, MemberApplication
from organisations.models import Organisation, OrganisationMember
from organisations.optional_fields import OPTIONAL_FIELDS_BY_KEY, effective_fields

ALL_ON = {key: True for key in OPTIONAL_FIELDS_BY_KEY}


class EffectiveFieldsTests(TestCase):
    def test_defaults_for_a_new_club(self):
        self.assertEqual(
            effective_fields({}),
            {'date_of_birth', 'phone', 'address', 'emergency_contact_2', 'medical_info',
             'joined_date', 'guardians', 'staff_emergency_contact', 'staff_emergency_contact_2'},
        )

    def test_unknown_field_is_an_error(self):
        with self.assertRaises(ValueError):
            Organisation(name='X', slug='x').has_field('shoe_size')

    def test_labels_fall_back_to_defaults(self):
        org = Organisation(name='X', slug='x')
        self.assertEqual(org.licence_label, 'Licence')
        org.settings = {'licence_label': 'BJA licence'}
        self.assertEqual(org.licence_label, 'BJA licence')


class FieldTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organisation.objects.create(
            name='Test Club', slug='test-club', optional_fields=dict(ALL_ON),
            settings={'licence_label': 'BJA licence', 'coaching_licence_label': 'BJA coaching licence'},
        )
        cls.admin = User.objects.create_user('admin', password='pw', first_name='Ada')
        cls.admin_om = OrganisationMember.objects.create(
            user=cls.admin, organisation=cls.org, role='org_admin',
            dbs_number='DBS-SENTINEL', dbs_expiry=date.today() - timedelta(days=1),
            coaching_licence='CL-SENTINEL', emergency_contact_name='Eve Emergency',
        )
        cls.member = Member.objects.create(
            organisation=cls.org, name='Alice A', email='alice@example.com',
            phone='07700 900111', date_of_birth=date(2012, 5, 1), joined_date=date(2020, 9, 1),
            address_line1='1 SENTINEL Street', medical_info='MEDICAL-SENTINEL',
            emergency_contact_name='Mum', emergency_contact_phone='07700 900222',
            emergency_contact_2_name='EC2-SENTINEL', emergency_contact_2_phone='07700 900333',
            licence_number='LIC-SENTINEL', licence_expiry=date.today() - timedelta(days=1),
        )
        Guardian.objects.create(member=cls.member, name='GUARDIAN-SENTINEL', email='g@example.com')

    def setUp(self):
        self.client.force_login(self.admin)

    def switch_off(self, *keys):
        self.org.set_optional_fields({**ALL_ON, **{k: False for k in keys}})
        self.org.save(update_fields=['optional_fields'])

    def url(self, name, **kwargs):
        return reverse(name, kwargs={'org_slug': self.org.slug, **kwargs})


class MemberPagesTests(FieldTestBase):
    SENTINELS = {
        'date_of_birth': '01 May 2012',
        'phone': '07700 900111',
        'address': '1 SENTINEL Street',
        'emergency_contact_2': 'EC2-SENTINEL',
        'medical_info': 'MEDICAL-SENTINEL',
        'guardians': 'GUARDIAN-SENTINEL',
        'licence': 'LIC-SENTINEL',
    }

    def test_member_page_shows_fields_when_on(self):
        response = self.client.get(self.url('member_detail', pk=self.member.pk))
        for key, sentinel in self.SENTINELS.items():
            with self.subTest(field=key):
                self.assertContains(response, sentinel)
        self.assertContains(response, 'BJA licence no.')

    def test_member_page_hides_fields_when_off(self):
        self.switch_off(*self.SENTINELS)
        response = self.client.get(self.url('member_detail', pk=self.member.pk))
        for key, sentinel in self.SENTINELS.items():
            with self.subTest(field=key):
                self.assertNotContains(response, sentinel)

    def test_member_form_drops_switched_off_fields(self):
        self.switch_off('date_of_birth', 'phone', 'address', 'licence', 'medical_info', 'joined_date',
                        'emergency_contact_2', 'guardians')
        response = self.client.get(self.url('member_edit', pk=self.member.pk))
        fields = response.context['form'].fields
        for name in ('date_of_birth', 'phone', 'address_line1', 'address_line2', 'licence_number',
                     'licence_expiry', 'medical_info', 'joined_date', 'emergency_contact_2_name'):
            self.assertNotIn(name, fields)
        self.assertIsNone(response.context['guardian_formset'])

    def test_editing_a_member_keeps_hidden_values(self):
        self.switch_off('phone', 'medical_info', 'licence', 'guardians')
        response = self.client.post(self.url('member_edit', pk=self.member.pk), {
            'name': 'Alice Renamed', 'email': 'alice@example.com', 'is_active': 'on',
            'emergency_contact_name': 'Mum', 'emergency_contact_phone': '07700 900222',
        })
        self.assertEqual(response.status_code, 302)
        self.member.refresh_from_db()
        self.assertEqual(self.member.name, 'Alice Renamed')
        self.assertEqual(self.member.phone, '07700 900111')
        self.assertEqual(self.member.medical_info, 'MEDICAL-SENTINEL')
        self.assertEqual(self.member.licence_number, 'LIC-SENTINEL')
        self.assertTrue(self.member.guardians.exists())

    def test_member_list_hides_phone_and_joined_columns(self):
        self.switch_off('phone', 'joined_date', 'medical_info')
        response = self.client.get(self.url('member_list'))
        self.assertNotContains(response, '07700 900111')
        self.assertNotContains(response, '<th>Joined</th>', html=False)
        self.assertNotContains(response, 'MEDICAL-SENTINEL')

    def test_register_hides_medical_info(self):
        cls = Class.objects.create(organisation=self.org, name='Juniors')
        ClassMember.objects.create(assigned_class=cls, member=self.member)
        session = Session.objects.create(assigned_class=cls, date=date.today())
        register = self.url('session_register', pk=cls.pk, session_pk=session.pk)
        self.assertContains(self.client.get(register), 'MEDICAL-SENTINEL')
        self.switch_off('medical_info')
        self.assertNotContains(self.client.get(register), 'MEDICAL-SENTINEL')

    def test_export_leaves_out_switched_off_columns(self):
        self.switch_off('date_of_birth', 'phone', 'guardians')
        content = self.client.get(self.url('member_export')).content.decode()
        header = content.splitlines()[0]
        self.assertNotIn('Date of birth', header)
        self.assertNotIn('Phone', header)
        self.assertNotIn('Guardian', header)
        self.assertNotIn('07700 900111', content)
        self.assertIn('Joined date', header)

    def test_import_ignores_switched_off_columns(self):
        self.switch_off('phone', 'date_of_birth')
        self.client.post(self.url('member_import'), {
            'action': 'import',
            'raw_csv': 'name,date_of_birth,email,phone\nBob B,2010-01-01,bob@example.com,07700 900999',
        })
        bob = Member.objects.get(name='Bob B')
        self.assertEqual(bob.phone, '')
        self.assertIsNone(bob.date_of_birth)
        self.assertEqual(bob.email, 'bob@example.com')

    def test_portal_hides_joined_date_and_junior_badge(self):
        portal = reverse('member_portal', kwargs={'token': self.member.token})
        self.assertContains(self.client.get(portal), 'Junior member')
        self.switch_off('joined_date', 'guardians')
        response = self.client.get(portal)
        self.assertNotContains(response, 'September 2020')
        self.assertNotContains(response, 'Junior member')


class JoinPageTests(FieldTestBase):
    def test_join_page_hides_fields(self):
        self.switch_off('date_of_birth', 'phone', 'address', 'guardians', 'medical_info')
        response = self.client.get(reverse('member_signup', kwargs={'org_slug': self.org.slug}))
        for name in ('date_of_birth', 'phone', 'address_line1', 'postcode', 'guardian_name', 'medical_info'):
            self.assertNotContains(response, f'name="{name}"')

    def test_join_page_does_not_store_switched_off_fields(self):
        self.switch_off('phone', 'address', 'guardians', 'medical_info', 'date_of_birth')
        self.client.post(reverse('member_signup', kwargs={'org_slug': self.org.slug}), {
            'name': 'Cara C', 'email': 'cara@example.com', 'phone': '07700 900444',
            'date_of_birth': '2015-01-01', 'address_line1': 'Somewhere', 'postcode': 'AB1 2CD',
            'guardian_name': 'Dad', 'medical_info': 'Asthma',
        })
        app = MemberApplication.objects.get(name='Cara C')
        self.assertEqual(app.email, 'cara@example.com')
        for value in (app.phone, app.address_line1, app.postcode, app.guardian_name, app.medical_info):
            self.assertEqual(value, '')
        self.assertIsNone(app.date_of_birth)


class StaffFieldsTests(FieldTestBase):
    def test_staff_page_hides_switched_off_fields(self):
        response = self.client.get(self.url('org_staff'))
        self.assertContains(response, 'DBS-SENTINEL')
        self.assertContains(response, 'BJA coaching licence')
        self.switch_off('dbs', 'coaching_licence', 'staff_emergency_contact')
        response = self.client.get(self.url('org_staff'))
        for sentinel in ('DBS-SENTINEL', 'CL-SENTINEL', 'Eve Emergency', 'name="dbs_number"'):
            self.assertNotContains(response, sentinel)

    def test_saving_staff_details_keeps_hidden_values(self):
        self.switch_off('dbs')
        self.client.post(self.url('org_staff'), {
            'action': 'update_qualifications', 'member_pk': self.admin_om.pk,
            'coaching_licence': 'CL-NEW', 'emergency_contact_name': 'Eve Emergency',
        })
        self.admin_om.refresh_from_db()
        self.assertEqual(self.admin_om.coaching_licence, 'CL-NEW')
        self.assertEqual(self.admin_om.dbs_number, 'DBS-SENTINEL')
        self.assertIsNotNone(self.admin_om.dbs_expiry)


class DashboardWarningsTests(FieldTestBase):
    def test_warnings_follow_switches_and_labels(self):
        response = self.client.get(self.url('org_dashboard'))
        self.assertEqual(response.context['licences_expired'], 1)
        self.assertContains(response, 'with an expired BJA licence')
        self.assertEqual(response.context['staff_expired'], 1)
        self.assertContains(response, 'with an expired DBS or BJA coaching licence')

        self.switch_off('licence', 'dbs', 'coaching_licence')
        response = self.client.get(self.url('org_dashboard'))
        self.assertEqual(response.context['licences_expired'], 0)
        self.assertEqual(response.context['staff_expired'], 0)
        self.assertNotContains(response, 'BJA licence')


class FieldSettingsTests(FieldTestBase):
    def save(self, keys, **labels):
        return self.client.post(self.url('org_settings'), {'action': 'fields', 'optional_fields': keys, **labels})

    def test_admin_can_switch_fields_and_set_labels(self):
        self.save(['phone', 'licence'], licence_label='  Licence from the federation of a very long name indeed  ')
        self.org.refresh_from_db()
        self.assertEqual(self.org.enabled_fields, {'phone', 'licence'})
        self.assertEqual(len(self.org.licence_label), 50)
        self.assertTrue(self.org.licence_label.startswith('Licence from'))
        self.assertEqual(self.org.coaching_licence_label, 'Coaching licence')

    def test_saving_fields_keeps_other_settings(self):
        self.org.settings = {**self.org.settings, 'sidebar_color': '#123456'}
        self.org.save(update_fields=['settings'])
        self.save(['phone'])
        self.org.refresh_from_db()
        self.assertEqual(self.org.settings['sidebar_color'], '#123456')

    def test_coach_cannot_change_fields(self):
        coach = User.objects.create_user('coach', password='pw')
        OrganisationMember.objects.create(user=coach, organisation=self.org, role='coach')
        self.client.force_login(coach)
        self.assertEqual(self.save([]).status_code, 403)
        self.org.refresh_from_db()
        self.assertEqual(self.org.optional_fields, ALL_ON)

    def test_other_org_admin_cannot_change_fields(self):
        other = Organisation.objects.create(name='Other', slug='other')
        admin_b = User.objects.create_user('admin-b', password='pw')
        OrganisationMember.objects.create(user=admin_b, organisation=other, role='org_admin')
        self.client.force_login(admin_b)
        self.assertEqual(self.save([]).status_code, 403)
        self.org.refresh_from_db()
        self.assertEqual(self.org.optional_fields, ALL_ON)

    def test_anonymous_is_sent_to_login(self):
        self.client.logout()
        response = self.save([])
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response['Location'])
        self.org.refresh_from_db()
        self.assertEqual(self.org.optional_fields, ALL_ON)


class SetupWizardFieldsTests(TestCase):
    def test_setup_stores_chosen_fields_and_labels(self):
        self.client.post(reverse('setup'), {
            'org_name': 'New Club', 'username': 'boss',
            'password': 'a-long-password', 'password_confirm': 'a-long-password',
            'optional_fields': ['phone', 'licence'], 'licence_label': 'BJA licence',
        })
        org = Organisation.objects.get(name='New Club')
        self.assertEqual(org.enabled_fields, {'phone', 'licence'})
        self.assertEqual(org.licence_label, 'BJA licence')

    def test_setup_form_preticks_defaults(self):
        response = self.client.get(reverse('setup'))
        ticked = {row['key'] for _, rows in response.context['field_groups'] for row in rows if row['enabled']}
        self.assertEqual(ticked, effective_fields({}))


class UpgradeBackfillTests(TestCase):
    """Clubs that existed before field switches keep every field switched on."""

    def test_backfill_switches_on_every_field(self):
        from importlib import import_module

        from django.apps import apps
        migration = import_module('organisations.migrations.0010_organisation_optional_fields')
        org = Organisation.objects.create(name='Existing Club', slug='existing', optional_fields={})
        migration.switch_on_existing_fields(apps, None)
        org.refresh_from_db()
        self.assertEqual(org.enabled_fields, set(OPTIONAL_FIELDS_BY_KEY))

    def test_backfill_covers_every_field_that_existed(self):
        from importlib import import_module
        migration = import_module('organisations.migrations.0010_organisation_optional_fields')
        added_since = {'staff_emergency_contact_2'}  # default on, so existing clubs get it anyway
        self.assertEqual(set(migration.EXISTING_FIELDS), set(OPTIONAL_FIELDS_BY_KEY) - added_since)
