import tempfile
from datetime import date

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from documents.models import WaiverTemplate
from members.models import Member, MemberNote
from organisations.features import FEATURES_BY_KEY
from organisations.models import Organisation, OrganisationMember
from progression.models import ProgressionStage, ProgressionSystem

ALL_ON = {key: True for key in FEATURES_BY_KEY}
PANES = ('overview', 'grades', 'billing', 'documents', 'notes')


def panes(html):
    """{pane key: that pane's HTML} for the member page's tab panes."""
    starts = sorted((html.index(f'id="pane-{key}"'), key) for key in PANES if f'id="pane-{key}"' in html)
    return {
        key: html[start:(starts[i + 1][0] if i + 1 < len(starts) else len(html))]
        for i, (start, key) in enumerate(starts)
    }


class MemberTabsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organisation.objects.create(name='Test Club', slug='test-club', features=dict(ALL_ON))
        cls.admin = User.objects.create_user('admin', password='pw')
        OrganisationMember.objects.create(user=cls.admin, organisation=cls.org, role='org_admin')
        cls.member = Member.objects.create(organisation=cls.org, name='Alice A', medical_info='MEDICAL-NOTE')
        system = ProgressionSystem.objects.create(organisation=cls.org, name='Kyu')
        cls.stage = ProgressionStage.objects.create(system=system, name='White')

    def setUp(self):
        self.client.force_login(self.admin)

    def url(self, name, **kwargs):
        return reverse(name, kwargs={'org_slug': self.org.slug, **kwargs})

    def page(self):
        return self.client.get(self.url('member_detail', pk=self.member.pk)).content.decode()

    def test_each_area_is_in_its_own_tab(self):
        sections = panes(self.page())
        self.assertEqual(set(sections), set(PANES))
        self.assertIn('Personal details', sections['overview'])
        self.assertIn('MEDICAL-NOTE', sections['overview'])
        self.assertIn('Emergency contacts', sections['overview'])
        self.assertIn(self.url('member_promote', pk=self.member.pk), sections['grades'])
        self.assertIn(self.url('invoice_list') + '?q=', sections['billing'])
        self.assertIn(self.url('document_upload', member_pk=self.member.pk), sections['documents'])
        self.assertIn(self.url('member_note_add', pk=self.member.pk), sections['notes'])

    def test_tabs_follow_the_clubs_features(self):
        self.org.set_features({**ALL_ON, 'grades': False, 'billing': False, 'documents': False})
        self.org.save(update_fields=['features'])
        html = self.page()
        self.assertEqual(set(panes(html)), {'overview', 'notes'})
        for key in ('grades', 'billing', 'documents'):
            self.assertNotIn(f'data-section="{key}"', html)

    def test_missing_waiver_is_flagged_on_the_tab_and_overview(self):
        self.assertNotIn('Missing required waivers', self.page())
        WaiverTemplate.objects.create(
            organisation=self.org, name='Club waiver', is_active=True, is_required=True,
            file=SimpleUploadedFile('waiver.pdf', b'%PDF-1.4'),
        )
        html = self.page()
        self.assertIn('title="Missing required waivers"', html)
        self.assertIn('they should not train', panes(html)['overview'])

    def test_actions_return_to_their_tab(self):
        cases = [
            ('notes', self.url('member_note_add', pk=self.member.pk), {'body': 'Great session'}),
            ('grades', self.url('member_promote', pk=self.member.pk), {'stage_id': self.stage.pk, 'achieved_date': str(date.today())}),
            ('billing', self.url('member_billing_policy_set', pk=self.member.pk), {'policy_id': ''}),
        ]
        for tab, url, data in cases:
            with self.subTest(tab=tab):
                response = self.client.post(url, data)
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response['Location'].endswith(f'#{tab}'), response['Location'])
        note = MemberNote.objects.get(member=self.member)
        response = self.client.post(self.url('member_note_delete', pk=self.member.pk, note_pk=note.pk))
        self.assertTrue(response['Location'].endswith('#notes'))

    @override_settings(MEDIA_ROOT=tempfile.mkdtemp())
    def test_document_upload_returns_to_documents(self):
        response = self.client.post(
            self.url('document_upload', member_pk=self.member.pk),
            {'name': 'Medical form', 'file': SimpleUploadedFile('form.pdf', b'%PDF-1.4')},
        )
        self.assertTrue(response['Location'].endswith('#documents'), response['Location'])
