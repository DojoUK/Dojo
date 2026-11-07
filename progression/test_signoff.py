import re
from datetime import date

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from members.models import Member
from organisations.features import FEATURES_BY_KEY
from organisations.models import Organisation, OrganisationMember
from progression.models import (
    MemberProgression, MemberTechnique, ProgressionStage, ProgressionSystem, Syllabus, SyllabusEntry, Technique,
)
from progression.syllabus import current_syllabuses

ALL_ON = {key: True for key in FEATURES_BY_KEY}


class SignOffTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organisation.objects.create(name='Test Club', slug='test-club', features=dict(ALL_ON))
        cls.admin = User.objects.create_user('admin', password='pw', first_name='Ada', last_name='Admin')
        OrganisationMember.objects.create(user=cls.admin, organisation=cls.org, role='org_admin')
        cls.coach = User.objects.create_user('coach', password='pw')
        OrganisationMember.objects.create(user=cls.coach, organisation=cls.org, role='coach')

        kyu = ProgressionSystem.objects.create(organisation=cls.org, name='Kyu')
        mon = ProgressionSystem.objects.create(organisation=cls.org, name='Mon', order=1)
        cls.kyu_syllabus = Syllabus.objects.create(organisation=cls.org, name='White to Yellow')
        cls.mon_syllabus = Syllabus.objects.create(organisation=cls.org, name='Mon 3 to 4')
        white = ProgressionStage.objects.create(system=kyu, name='White', syllabus=cls.kyu_syllabus)
        mon3 = ProgressionStage.objects.create(system=mon, name='3rd Mon', syllabus=cls.mon_syllabus)

        cls.ukemi = Technique.objects.create(organisation=cls.org, name='Ushiro ukemi')
        cls.o_goshi = Technique.objects.create(organisation=cls.org, name='O-goshi')
        cls.kesa = Technique.objects.create(organisation=cls.org, name='Kesa gatame')
        for technique in (cls.ukemi, cls.o_goshi, cls.kesa):
            SyllabusEntry.objects.create(syllabus=cls.kyu_syllabus, technique=technique)
        SyllabusEntry.objects.create(syllabus=cls.mon_syllabus, technique=cls.ukemi)  # shared

        cls.member = Member.objects.create(organisation=cls.org, name='Jo Judoka')
        MemberProgression.objects.create(member=cls.member, stage=white, achieved_date=date(2026, 1, 1))
        MemberProgression.objects.create(member=cls.member, stage=mon3, achieved_date=date(2026, 1, 2))

        cls.other_org = Organisation.objects.create(name='Other', slug='other', features=dict(ALL_ON))
        cls.their_technique = Technique.objects.create(organisation=cls.other_org, name='Theirs')
        cls.their_member = Member.objects.create(organisation=cls.other_org, name='Their member')

    def setUp(self):
        self.client.force_login(self.admin)

    def toggle(self, technique, member=None, syllabus=None, **headers):
        member = member or self.member
        return self.client.post(
            reverse('member_technique_toggle', kwargs={
                'org_slug': self.org.slug, 'pk': member.pk, 'technique_pk': technique.pk,
            }),
            {'syllabus': (syllabus or self.kyu_syllabus).pk},
            headers=headers,
        )

    def test_sign_off_and_undo(self):
        response = self.toggle(self.o_goshi)
        self.assertRedirects(
            response, reverse('member_detail', kwargs={'org_slug': self.org.slug, 'pk': self.member.pk}) + '#grades',
            fetch_redirect_response=False,
        )
        signed = MemberTechnique.objects.get(member=self.member, technique=self.o_goshi)
        self.assertEqual(signed.signed_off_by, self.admin)
        self.toggle(self.o_goshi)
        self.assertFalse(MemberTechnique.objects.exists())

    def test_htmx_returns_just_the_row(self):
        response = self.toggle(self.o_goshi, **{'HX-Request': 'true'})
        self.assertContains(response, f'id="technique-{self.kyu_syllabus.pk}-{self.o_goshi.pk}">')
        self.assertContains(response, 'bi-check-square-fill')
        self.assertContains(response, 'by Ada Admin')
        self.assertContains(response, f'id="signoff-count-{self.kyu_syllabus.pk}" hx-swap-oob="true">1 of 3 signed off')
        self.assertNotContains(response, '<html')

    def test_a_technique_in_two_syllabuses_is_ticked_in_both(self):
        response = self.toggle(self.ukemi, syllabus=self.mon_syllabus, **{'HX-Request': 'true'})
        html = response.content.decode()
        # The ticked row is swapped in place, the other syllabus's copy out of band.
        self.assertIn(f'id="technique-{self.mon_syllabus.pk}-{self.ukemi.pk}">', html)
        self.assertIn(f'id="technique-{self.kyu_syllabus.pk}-{self.ukemi.pk}" hx-swap-oob="true">', html)
        self.assertEqual(html.count('bi-check-square-fill'), 2)
        self.assertContains(response, f'id="signoff-count-{self.kyu_syllabus.pk}" hx-swap-oob="true">1 of 3 signed off')
        self.assertContains(response, f'id="signoff-count-{self.mon_syllabus.pk}" hx-swap-oob="true">1 of 1 signed off')

    def test_member_page_ids_are_unique(self):
        response = self.client.get(reverse('member_detail', kwargs={'org_slug': self.org.slug, 'pk': self.member.pk}))
        ids = re.findall(r'id="(technique-[\d-]+)"', response.content.decode())
        self.assertEqual(len(ids), 4)
        self.assertEqual(len(ids), len(set(ids)))

    def test_out_of_date_page_is_reloaded(self):
        other = Syllabus.objects.create(organisation=self.org, name='Not theirs')
        response = self.toggle(self.o_goshi, syllabus=other, **{'HX-Request': 'true'})
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response['HX-Refresh'], 'true')

    def test_signed_out_visitors_are_sent_to_log_in(self):
        self.client.logout()
        response = self.toggle(self.o_goshi)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response['Location'])
        self.assertFalse(MemberTechnique.objects.exists())

    def test_member_page_counts_sign_offs(self):
        self.toggle(self.ukemi)
        response = self.client.get(reverse('member_detail', kwargs={'org_slug': self.org.slug, 'pk': self.member.pk}))
        self.assertContains(response, '1 of 3 signed off')
        self.assertContains(response, '1 of 1 signed off')  # the shared technique counts in both syllabuses

    def test_current_syllabuses_marks_entries(self):
        MemberTechnique.objects.create(member=self.member, technique=self.kesa)
        card = next(c for c in current_syllabuses(self.member) if c['syllabus'] == self.kyu_syllabus)
        marked = {e.technique.name: bool(e.signed_off) for _, group in card['groups'] for e in group}
        self.assertEqual(marked, {'Ushiro ukemi': False, 'O-goshi': False, 'Kesa gatame': True})
        self.assertEqual((card['done'], card['total']), (1, 3))

    def test_another_clubs_technique_or_member_is_refused(self):
        self.assertEqual(self.toggle(self.their_technique).status_code, 404)
        self.assertEqual(self.toggle(self.o_goshi, member=self.their_member).status_code, 404)
        self.assertFalse(MemberTechnique.objects.exists())

    def test_coaches_cannot_sign_off(self):
        self.client.force_login(self.coach)
        self.assertEqual(self.toggle(self.o_goshi).status_code, 403)
        self.assertFalse(MemberTechnique.objects.exists())

    def test_off_without_the_syllabus_feature(self):
        self.org.features = {**ALL_ON, 'syllabus': False}
        self.org.save(update_fields=['features'])
        self.assertEqual(self.toggle(self.o_goshi).status_code, 404)
