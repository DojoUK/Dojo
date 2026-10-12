from datetime import date

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from members.models import Member
from organisations.features import FEATURES_BY_KEY
from organisations.models import Organisation, OrganisationMember
from progression.models import (
    MemberProgression,
    ProgressionStage,
    ProgressionSystem,
    Syllabus,
    SyllabusEntry,
    SyllabusHeading,
    Technique,
)

ALL_ON = {key: True for key in FEATURES_BY_KEY}


class SyllabusTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organisation.objects.create(name='Test Club', slug='test-club', features=dict(ALL_ON))
        cls.admin = User.objects.create_user('admin', password='pw')
        OrganisationMember.objects.create(user=cls.admin, organisation=cls.org, role='org_admin')

        cls.kyu = ProgressionSystem.objects.create(organisation=cls.org, name='Kyu')
        cls.white = ProgressionStage.objects.create(system=cls.kyu, name='White', order=0)
        cls.white_bar = ProgressionStage.objects.create(system=cls.kyu, name='White + 1 Yellow Bar', order=1)
        cls.yellow = ProgressionStage.objects.create(system=cls.kyu, name='Yellow', order=2)

        cls.syllabus = Syllabus.objects.create(organisation=cls.org, name='White to Yellow')
        cls.throws = SyllabusHeading.objects.create(syllabus=cls.syllabus, name='Nage Waza', order=0)
        cls.ukemi = Technique.objects.create(organisation=cls.org, name='Ushiro ukemi', category='Ukemi')
        cls.o_goshi = Technique.objects.create(
            organisation=cls.org, name='O goshi', category='Nage Waza', link='https://example.com/o-goshi',
        )
        SyllabusEntry.objects.create(syllabus=cls.syllabus, technique=cls.o_goshi, heading=cls.throws, order=0)

        cls.member = Member.objects.create(organisation=cls.org, name='Alice A')
        MemberProgression.objects.create(member=cls.member, stage=cls.white_bar, achieved_date=date(2026, 3, 1))

    def setUp(self):
        self.client.force_login(self.admin)

    def url(self, name, **kwargs):
        return reverse(name, kwargs={'org_slug': self.org.slug, **kwargs})

    def syllabus_url(self, name, **kwargs):
        return self.url(name, pk=self.syllabus.pk, **kwargs)


class LibraryTests(SyllabusTestBase):
    def test_a_technique_can_be_in_many_syllabuses(self):
        other = Syllabus.objects.create(organisation=self.org, name='Yellow to Orange')
        for syllabus in (self.syllabus, other):
            self.client.post(self.url('syllabus_entry_add', pk=syllabus.pk), {'technique_id': self.ukemi.pk})
        self.assertEqual(self.ukemi.syllabus_entries.count(), 2)
        self.assertEqual(Technique.objects.filter(name='Ushiro ukemi').count(), 1)

    def test_adding_a_new_technique_puts_it_in_the_library(self):
        self.client.post(self.syllabus_url('syllabus_entry_add'), {
            'name': 'Tai otoshi', 'category': 'Nage Waza', 'heading_id': self.throws.pk, 'notes': 'both sides',
        })
        technique = Technique.objects.get(name='Tai otoshi')
        entry = self.syllabus.entries.get(technique=technique)
        self.assertEqual((entry.heading, entry.notes), (self.throws, 'both sides'))

    def test_the_same_technique_is_only_added_once_per_syllabus(self):
        self.client.post(self.syllabus_url('syllabus_entry_add'), {'technique_id': self.o_goshi.pk})
        self.assertEqual(self.syllabus.entries.filter(technique=self.o_goshi).count(), 1)

    def test_links_must_be_web_addresses(self):
        for link in ('javascript:alert(1)', 'not a url', 'ftp://example.com/x'):
            with self.subTest(link=link):
                self.client.post(self.url('technique_list'), {'name': f'Bad {link}', 'link': link})
                self.assertFalse(Technique.objects.filter(name=f'Bad {link}').exists())
                self.client.post(self.url('technique_edit', pk=self.ukemi.pk), {'name': 'Ushiro ukemi', 'link': link})
                self.ukemi.refresh_from_db()
                self.assertEqual(self.ukemi.link, '')
        self.client.post(self.url('technique_list'), {'name': 'Good', 'link': 'https://example.com/v'})
        self.assertEqual(Technique.objects.get(name='Good').link, 'https://example.com/v')

    def test_a_technique_in_use_cannot_be_deleted(self):
        self.client.post(self.url('technique_edit', pk=self.o_goshi.pk), {'action': 'delete', 'name': 'O goshi'})
        self.assertTrue(Technique.objects.filter(pk=self.o_goshi.pk).exists())
        self.client.post(self.url('technique_edit', pk=self.ukemi.pk), {'action': 'delete', 'name': 'Ushiro ukemi'})
        self.assertFalse(Technique.objects.filter(pk=self.ukemi.pk).exists())

    def test_library_search_and_category_filter(self):
        response = self.client.get(self.url('technique_list'), {'category': 'Ukemi'})
        self.assertEqual(list(response.context['techniques']), [self.ukemi])
        response = self.client.get(self.url('technique_list'), {'q': 'goshi'})
        self.assertEqual(list(response.context['techniques']), [self.o_goshi])


class SyllabusEditingTests(SyllabusTestBase):
    def test_headings_and_ordering(self):
        self.client.post(self.syllabus_url('syllabus_heading_add'), {'name': 'Katame Waza'})
        katame = self.syllabus.headings.get(name='Katame Waza')
        self.client.post(self.syllabus_url('syllabus_heading_edit', heading_pk=katame.pk), {'action': 'up'})
        self.assertEqual([h.name for h in self.syllabus.headings.all()], ['Katame Waza', 'Nage Waza'])

    def test_removing_a_heading_keeps_its_techniques(self):
        self.client.post(self.syllabus_url('syllabus_heading_edit', heading_pk=self.throws.pk), {'action': 'delete'})
        entry = self.syllabus.entries.get(technique=self.o_goshi)
        self.assertIsNone(entry.heading)

    def test_removing_an_entry_keeps_the_technique(self):
        entry = self.syllabus.entries.get(technique=self.o_goshi)
        self.client.post(self.syllabus_url('syllabus_entry_edit', entry_pk=entry.pk), {'action': 'delete'})
        self.assertFalse(self.syllabus.entries.exists())
        self.assertTrue(Technique.objects.filter(pk=self.o_goshi.pk).exists())

    def test_several_grades_share_one_syllabus(self):
        other = Syllabus.objects.create(organisation=self.org, name='Old')
        ProgressionStage.objects.filter(pk=self.yellow.pk).update(syllabus=other)
        self.client.post(self.syllabus_url('syllabus_stages'), {'stage_ids': [self.white.pk, self.white_bar.pk]})
        self.assertEqual(set(self.syllabus.stages.all()), {self.white, self.white_bar})

        self.client.post(self.syllabus_url('syllabus_stages'), {'stage_ids': [self.white.pk, self.yellow.pk]})
        self.assertEqual(set(self.syllabus.stages.all()), {self.white, self.yellow})
        self.assertFalse(other.stages.exists())  # moved off the other syllabus

    def test_pages_render(self):
        for route in (self.url('syllabus_home'), self.url('technique_list'), self.syllabus_url('syllabus_detail')):
            with self.subTest(route=route):
                self.assertEqual(self.client.get(route).status_code, 200)


class MemberSyllabusTests(SyllabusTestBase):
    def test_member_page_shows_the_syllabus_for_their_grade(self):
        ProgressionStage.objects.filter(pk=self.white_bar.pk).update(syllabus=self.syllabus)
        response = self.client.get(self.url('member_detail', pk=self.member.pk))
        self.assertContains(response, 'White to Yellow')
        self.assertContains(response, 'O goshi')
        self.assertContains(response, 'href="https://example.com/o-goshi"')

    def test_nothing_shown_without_a_linked_syllabus_or_with_the_feature_off(self):
        response = self.client.get(self.url('member_detail', pk=self.member.pk))
        self.assertNotContains(response, 'White to Yellow')

        ProgressionStage.objects.filter(pk=self.white_bar.pk).update(syllabus=self.syllabus)
        self.org.set_features({**ALL_ON, 'syllabus': False})
        self.org.save(update_fields=['features'])
        self.assertNotContains(self.client.get(self.url('member_detail', pk=self.member.pk)), 'White to Yellow')


class SyllabusAccessTests(SyllabusTestBase):
    """Tenant-matrix rows for the syllabus routes."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.coach_user = User.objects.create_user('coach', password='pw')
        OrganisationMember.objects.create(user=cls.coach_user, organisation=cls.org, role='coach')
        cls.org_b = Organisation.objects.create(name='Other', slug='other', features=dict(ALL_ON))
        cls.admin_b = User.objects.create_user('admin-b', password='pw')
        OrganisationMember.objects.create(user=cls.admin_b, organisation=cls.org_b, role='org_admin')
        system_b = ProgressionSystem.objects.create(organisation=cls.org_b, name='Kyu')
        cls.stage_b = ProgressionStage.objects.create(system=system_b, name='SENTINEL-B-stage')
        cls.syllabus_b = Syllabus.objects.create(organisation=cls.org_b, name='SENTINEL-B-syllabus')
        cls.heading_b = SyllabusHeading.objects.create(syllabus=cls.syllabus_b, name='B heading')
        cls.technique_b = Technique.objects.create(organisation=cls.org_b, name='SENTINEL-B-technique')

    def routes(self):
        entry = self.syllabus.entries.first()
        return [
            ('get', self.url('syllabus_home')),
            ('post', self.url('syllabus_home')),
            ('get', self.url('technique_list')),
            ('post', self.url('technique_list')),
            ('post', self.url('technique_edit', pk=self.ukemi.pk)),
            ('get', self.syllabus_url('syllabus_detail')),
            ('post', self.syllabus_url('syllabus_edit')),
            ('post', self.syllabus_url('syllabus_delete')),
            ('post', self.syllabus_url('syllabus_stages')),
            ('post', self.syllabus_url('syllabus_heading_add')),
            ('post', self.syllabus_url('syllabus_heading_edit', heading_pk=self.throws.pk)),
            ('post', self.syllabus_url('syllabus_entry_add')),
            ('post', self.syllabus_url('syllabus_entry_edit', entry_pk=entry.pk)),
        ]

    def assert_refused(self, status):
        for method, route in self.routes():
            with self.subTest(route=route):
                response = getattr(self.client, method)(route, {'action': 'delete', 'name': 'X'})
                self.assertEqual(response.status_code, status)
                self.assertNotContains(response, 'O goshi', status_code=status)
        self.assertTrue(Syllabus.objects.filter(pk=self.syllabus.pk, name='White to Yellow').exists())
        self.assertTrue(Technique.objects.filter(pk=self.ukemi.pk, name='Ushiro ukemi').exists())
        self.assertEqual(self.syllabus.entries.count(), 1)

    def test_coach_is_refused(self):
        self.client.force_login(self.coach_user)
        self.assert_refused(403)

    def test_other_org_admin_is_refused(self):
        self.client.force_login(self.admin_b)
        self.assert_refused(403)

    def test_anonymous_is_sent_to_login(self):
        self.client.logout()
        for method, route in self.routes():
            with self.subTest(route=route):
                response = getattr(self.client, method)(route)
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse('login'), response['Location'])

    def test_feature_off_is_404(self):
        self.org.set_features({**ALL_ON, 'syllabus': False})
        self.org.save(update_fields=['features'])
        self.assert_refused(404)

    def test_other_org_objects_are_refused(self):
        self.assertEqual(self.client.get(self.url('syllabus_detail', pk=self.syllabus_b.pk)).status_code, 404)
        self.assertEqual(self.client.post(self.url('technique_edit', pk=self.technique_b.pk), {'name': 'X'}).status_code, 404)
        self.assertEqual(
            self.client.post(self.syllabus_url('syllabus_heading_edit', heading_pk=self.heading_b.pk)).status_code, 404,
        )
        # B's technique and heading posted to A's syllabus
        self.assertEqual(
            self.client.post(self.syllabus_url('syllabus_entry_add'), {'technique_id': self.technique_b.pk}).status_code, 404,
        )
        self.assertEqual(
            self.client.post(self.syllabus_url('syllabus_entry_add'), {'technique_id': self.ukemi.pk, 'heading_id': self.heading_b.pk}).status_code, 404,
        )
        # B's grade ticked on A's syllabus
        self.client.post(self.syllabus_url('syllabus_stages'), {'stage_ids': [self.stage_b.pk]})
        self.stage_b.refresh_from_db()
        self.assertIsNone(self.stage_b.syllabus)
        self.assertNotContains(self.client.get(self.syllabus_url('syllabus_detail')), 'SENTINEL-B')
        self.technique_b.refresh_from_db()
        self.assertEqual(self.technique_b.name, 'SENTINEL-B-technique')
