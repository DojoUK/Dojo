from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from organisations.models import Organisation, OrganisationMember
from organisations.optional_fields import OPTIONAL_FIELDS_BY_KEY

ALL_ON = {key: True for key in OPTIONAL_FIELDS_BY_KEY}


class StaffContactsTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organisation.objects.create(name='Test Club', slug='test-club', optional_fields=dict(ALL_ON))
        cls.admin = User.objects.create_user('admin', password='pw')
        OrganisationMember.objects.create(user=cls.admin, organisation=cls.org, role='org_admin')
        cls.coach_user = User.objects.create_user('coach', password='pw')
        cls.coach = OrganisationMember.objects.create(
            user=cls.coach_user, organisation=cls.org, role='coach',
            emergency_contact_name='First Contact', emergency_contact_2_name='SECOND-SENTINEL',
            emergency_contact_2_phone='07700 900555',
        )

    def url(self, name, org=None):
        return reverse(name, kwargs={'org_slug': (org or self.org).slug})

    def switch_off(self, *keys):
        self.org.set_optional_fields({**ALL_ON, **{k: False for k in keys}})
        self.org.save(update_fields=['optional_fields'])


class StaffPageTests(StaffContactsTestBase):
    def setUp(self):
        self.client.force_login(self.admin)

    def test_staff_page_shows_and_edits_the_second_contact(self):
        response = self.client.get(self.url('org_staff'))
        self.assertContains(response, 'SECOND-SENTINEL')
        self.assertContains(response, 'name="emergency_contact_2_name"')

        self.client.post(self.url('org_staff'), {
            'action': 'update_qualifications', 'member_pk': self.coach.pk,
            'emergency_contact_name': 'First Contact',
            'emergency_contact_2_name': 'New Second', 'emergency_contact_2_phone': '07700 900666',
        })
        self.coach.refresh_from_db()
        self.assertEqual(self.coach.emergency_contact_2_name, 'New Second')
        self.assertEqual(self.coach.emergency_contact_2_phone, '07700 900666')

    def test_switched_off_second_contact_is_hidden_and_kept(self):
        self.switch_off('staff_emergency_contact_2')
        response = self.client.get(self.url('org_staff'))
        self.assertNotContains(response, 'SECOND-SENTINEL')
        self.assertNotContains(response, 'name="emergency_contact_2_name"')

        self.client.post(self.url('org_staff'), {
            'action': 'update_qualifications', 'member_pk': self.coach.pk, 'emergency_contact_name': 'Changed',
        })
        self.coach.refresh_from_db()
        self.assertEqual(self.coach.emergency_contact_name, 'Changed')
        self.assertEqual(self.coach.emergency_contact_2_name, 'SECOND-SENTINEL')


class AccountPageTests(StaffContactsTestBase):
    def setUp(self):
        self.client.force_login(self.coach_user)

    def save(self, org=None, **data):
        return self.client.post(self.url('account_settings', org), {'action': 'update_emergency_contacts', **data})

    def test_staff_edit_their_own_contacts(self):
        self.assertContains(self.client.get(self.url('account_settings')), 'My emergency contacts')
        self.save(emergency_contact_name='Mum', emergency_contact_phone='07700 900111',
                  emergency_contact_2_name='Dad', emergency_contact_2_phone='07700 900222')
        self.coach.refresh_from_db()
        self.assertEqual(
            (self.coach.emergency_contact_name, self.coach.emergency_contact_2_name),
            ('Mum', 'Dad'),
        )

    def test_switched_off_contacts_are_hidden_and_kept(self):
        self.switch_off('staff_emergency_contact', 'staff_emergency_contact_2')
        self.assertNotContains(self.client.get(self.url('account_settings')), 'My emergency contacts')
        self.save(emergency_contact_name='Changed', emergency_contact_2_name='Changed')
        self.coach.refresh_from_db()
        self.assertEqual(self.coach.emergency_contact_name, 'First Contact')
        self.assertEqual(self.coach.emergency_contact_2_name, 'SECOND-SENTINEL')

    def test_saving_contacts_only_touches_your_own_record(self):
        admin_membership = OrganisationMember.objects.get(user=self.admin)
        self.save(emergency_contact_name='Mum', member_pk=admin_membership.pk)
        admin_membership.refresh_from_db()
        self.assertEqual(admin_membership.emergency_contact_name, '')

    def test_other_org_is_refused(self):
        other = Organisation.objects.create(name='Other', slug='other')
        self.assertEqual(self.save(org=other, emergency_contact_name='X').status_code, 403)
        self.coach.refresh_from_db()
        self.assertEqual(self.coach.emergency_contact_name, 'First Contact')

    def test_anonymous_is_sent_to_login(self):
        self.client.logout()
        response = self.save(emergency_contact_name='X')
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response['Location'])
