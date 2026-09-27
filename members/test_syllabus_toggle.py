from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from members.models import Member
from organisations.models import Organisation, OrganisationMember
from progression.models import (
    MemberProgression, MemberSyllabusProgress, ProgressionStage, ProgressionSystem,
    SyllabusItem, SyllabusSection, SyllabusSubsection,
)


class SyllabusToggleTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user('admin', password='pw')
        self.org = Organisation.objects.create(name='Club', slug='club')
        OrganisationMember.objects.create(user=user, organisation=self.org, role=OrganisationMember.Role.ORG_ADMIN)
        self.member = Member.objects.create(organisation=self.org, name='Sam')
        section = SyllabusSection.objects.create(organisation=self.org, name='White belt')
        sub = SyllabusSubsection.objects.create(section=section, name='Kicks')
        self.item = SyllabusItem.objects.create(section=section, subsection=sub, name='Front kick')
        system = ProgressionSystem.objects.create(organisation=self.org, name='Belts')
        stage = ProgressionStage.objects.create(system=system, name='White', syllabus_section=section)
        MemberProgression.objects.create(member=self.member, stage=stage, achieved_date=date.today())
        self.client.force_login(user)
        self.url = reverse('member_syllabus_toggle', kwargs={'org_slug': 'club', 'pk': self.member.pk, 'item_pk': self.item.pk})

    def test_member_page_shows_subsection_heading_and_item(self):
        html = self.client.get(reverse('member_detail', kwargs={'org_slug': 'club', 'pk': self.member.pk})).content.decode()
        self.assertIn('Kicks', html)
        self.assertIn('Front kick', html)
        self.assertIn('hx-post', html)

    def test_htmx_toggle_returns_item_without_redirect(self):
        resp = self.client.post(self.url, HTTP_HX_REQUEST='true')
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn('checked', html)
        self.assertIn('text-decoration-line-through', html)
        self.assertNotIn('<html', html)
        self.assertTrue(MemberSyllabusProgress.objects.get(member=self.member, item=self.item).completed)

        resp = self.client.post(self.url, HTTP_HX_REQUEST='true')
        self.assertNotIn('text-decoration-line-through', resp.content.decode())
        self.assertFalse(MemberSyllabusProgress.objects.get(member=self.member, item=self.item).completed)

    def test_plain_post_still_redirects(self):
        resp = self.client.post(self.url)
        self.assertEqual(resp.status_code, 302)

    def test_portal_syllabus_shows_items(self):
        html = self.client.get(reverse('portal_syllabus', kwargs={'token': self.member.token})).content.decode()
        self.assertIn('Kicks', html)
        self.assertIn('Front kick', html)
