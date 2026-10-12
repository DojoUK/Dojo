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


class StaffEditPanelsTests(StaffContactsTestBase):
    def test_every_staff_member_has_their_own_edit_panel(self):
        # A coach followed by more staff: a panel bug can push later panels out of place.
        OrganisationMember.objects.create(
            user=User.objects.create_user('zara', password='pw'), organisation=self.org, role='coach',
        )
        self.client.force_login(self.admin)
        html = self.client.get(self.url('org_staff')).content.decode()
        for om in OrganisationMember.objects.filter(organisation=self.org):
            panel_start = html.index(f'id="quals-{om.pk}"')
            next_panel = html.find('id="quals-', panel_start + 1)
            panel = html[panel_start:next_panel if next_panel != -1 else len(html)]
            with self.subTest(staff=om.user.username):
                self.assertIn(f'name="member_pk" value="{om.pk}"', panel)
        # Panels sit together below the table, and only one opens at a time.
        self.assertEqual(html.count('data-bs-parent="#staff-panels"'), 3)
        self.assertLess(html.index('</table>'), html.index('id="staff-panels"'))
        # A misplaced closing tag would nest one panel inside another, so the
        # browser shows only the first. Check each panel's actual parent.
        self.assertEqual(
            _div_parents(html, 'quals-'),
            {f'quals-{om.pk}': 'staff-panels' for om in OrganisationMember.objects.filter(organisation=self.org)},
        )


def _div_parents(html, id_prefix):
    """{div id: id of the nearest enclosing div with an id} for divs whose id starts with id_prefix."""
    from html.parser import HTMLParser

    class Parser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.stack, self.parents = [], {}

        def handle_starttag(self, tag, attrs):
            if tag != 'div':
                return
            div_id = dict(attrs).get('id')
            if div_id and div_id.startswith(id_prefix):
                self.parents[div_id] = next((i for i in reversed(self.stack) if i), None)
            self.stack.append(div_id)

        def handle_endtag(self, tag):
            if tag == 'div' and self.stack:
                self.stack.pop()

    parser = Parser()
    parser.feed(html)
    return parser.parents
