from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from organisations.models import Organisation, OrganisationMember
from progression.models import SyllabusItem, SyllabusSection, SyllabusSubsection


class SyllabusSubsectionSettingsTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user('admin', password='pw')
        self.org = Organisation.objects.create(name='Club', slug='club')
        OrganisationMember.objects.create(user=user, organisation=self.org, role=OrganisationMember.Role.ORG_ADMIN)
        self.section = SyllabusSection.objects.create(organisation=self.org, name='Yellow belt')
        self.client.force_login(user)
        self.settings_url = reverse('progression_settings', kwargs={'org_slug': 'club'})

    def url(self, name, **kw):
        return reverse(name, kwargs={'org_slug': 'club', 'section_pk': self.section.pk, **kw})

    def test_add_subsection_and_item_under_it(self):
        self.client.post(self.url('syllabus_subsection_add'), {'name': 'Throws'})
        sub = SyllabusSubsection.objects.get(section=self.section, name='Throws')
        self.client.post(self.url('syllabus_item_add'), {'name': 'O-goshi', 'subsection_id': sub.pk})
        self.assertEqual(SyllabusItem.objects.get(name='O-goshi').subsection, sub)

        html = self.client.get(self.settings_url).content.decode()
        self.assertIn('Throws', html)
        self.assertIn('O-goshi', html)
        self.assertIn('Add sub-section', html)
        self.assertIn(f'<option value="{sub.pk}" selected>Throws</option>', html)

    def test_empty_subsection_is_shown(self):
        SyllabusSubsection.objects.create(section=self.section, name='Groundwork')
        html = self.client.get(self.settings_url).content.decode()
        self.assertIn('Groundwork', html)
        self.assertIn('No items in this sub-section yet.', html)

    def test_rename_and_delete_subsection_keeps_items(self):
        sub = SyllabusSubsection.objects.create(section=self.section, name='Throws')
        item = SyllabusItem.objects.create(section=self.section, subsection=sub, name='O-goshi')
        self.client.post(self.url('syllabus_subsection_edit', pk=sub.pk), {'name': 'Nage-waza'})
        sub.refresh_from_db()
        self.assertEqual(sub.name, 'Nage-waza')

        self.client.post(self.url('syllabus_subsection_delete', pk=sub.pk))
        item.refresh_from_db()
        self.assertIsNone(item.subsection)
        self.assertIn('O-goshi', self.client.get(self.settings_url).content.decode())

    def test_systems_and_sections_are_collapsible(self):
        from progression.models import ProgressionSystem
        system = ProgressionSystem.objects.create(organisation=self.org, name='Kyu')
        html = self.client.get(self.settings_url).content.decode()
        self.assertIn(f'id="system-body-{system.pk}"', html)
        self.assertIn(f'data-bs-target="#system-body-{system.pk}"', html)
        self.assertIn(f'id="syllabus-body-{self.section.pk}"', html)
        self.assertIn(f'data-bs-target="#syllabus-body-{self.section.pk}"', html)
