import shutil
import tempfile
from unittest import mock

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from documents.models import Document
from members.models import Member
from organisations.features import FEATURES_BY_KEY
from organisations.models import Organisation, OrganisationMember

ALL_ON = {key: True for key in FEATURES_BY_KEY}
MEDIA = tempfile.mkdtemp()


def pdf(name='letter.pdf', content=b'%PDF-1.4 test'):
    return SimpleUploadedFile(name, content, content_type='application/pdf')


@override_settings(MEDIA_ROOT=MEDIA)
class PortalDocumentTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organisation.objects.create(name='Test Club', slug='test-club', features=dict(ALL_ON))
        cls.member = Member.objects.create(organisation=cls.org, name='Alice A')
        cls.sibling = Member.objects.create(organisation=cls.org, name='Bob B')
        cls.other_org = Organisation.objects.create(name='Other', slug='other', features=dict(ALL_ON))
        cls.outsider = Member.objects.create(organisation=cls.other_org, name='Out Sider')
        cls.admin = User.objects.create_user('admin', password='pw')
        OrganisationMember.objects.create(user=cls.admin, organisation=cls.org, role='org_admin')

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def upload(self, member=None, token=None, **data):
        token = token or (member or self.member).token
        return self.client.post(
            reverse('portal_document_upload', kwargs={'token': token}), {'file': pdf(), 'category': 'medical', **data},
        )

    def download_url(self, doc, member=None):
        return reverse('portal_document_download', kwargs={'token': (member or self.member).token, 'pk': doc.pk})

    def test_member_uploads_a_document(self):
        response = self.upload(name='GP letter')
        self.assertRedirects(
            response, reverse('member_portal', kwargs={'token': self.member.token}) + '#documents',
            fetch_redirect_response=False,
        )
        doc = Document.objects.get()
        self.assertEqual((doc.member, doc.name, doc.category, doc.uploaded_by_member), (self.member, 'GP letter', 'medical', True))
        self.assertIsNone(doc.uploaded_by)

    def test_name_defaults_to_the_file_name_and_odd_category_to_other(self):
        self.upload(name='', category='secret')
        doc = Document.objects.get()
        self.assertEqual((doc.name, doc.category), ('letter.pdf', 'other'))

    def test_wrong_kind_of_file_is_refused(self):
        self.upload(file=SimpleUploadedFile('run.exe', b'MZ'))
        self.assertFalse(Document.objects.exists())
        page = self.client.get(reverse('member_portal', kwargs={'token': self.member.token}))
        self.assertContains(page, 'Upload a PDF, photo (JPG, PNG or HEIC) or Word document.')

    def test_too_big_is_refused(self):
        with mock.patch('members.portal_views.PORTAL_UPLOAD_MAX_BYTES', 5):
            self.upload()
        self.assertFalse(Document.objects.exists())

    def test_upload_count_is_limited(self):
        with mock.patch('members.portal_views.PORTAL_UPLOAD_LIMIT', 2):
            for _ in range(3):
                self.upload()
        self.assertEqual(Document.objects.count(), 2)

    def test_wrong_or_inactive_token_is_refused(self):
        self.assertEqual(self.upload(token='not-a-real-token').status_code, 404)
        gone = Member.objects.create(organisation=self.org, name='Gone', is_active=False)
        self.assertEqual(self.upload(member=gone).status_code, 404)
        self.assertFalse(Document.objects.exists())

    def test_off_when_documents_or_portal_is_switched_off(self):
        for feature in ('documents', 'member_portal'):
            self.org.features = {**ALL_ON, feature: False}
            self.org.save(update_fields=['features'])
            self.assertEqual(self.upload().status_code, 404)
        self.assertFalse(Document.objects.exists())

    def test_member_downloads_their_own_documents_only(self):
        mine = Document.objects.create(member=self.member, name='Mine', file=pdf('mine.pdf', b'MINE-SENTINEL'))
        sibling = Document.objects.create(member=self.sibling, name='Sibling', file=pdf('s.pdf', b'SIBLING-SENTINEL'))
        theirs = Document.objects.create(member=self.outsider, name='Theirs', file=pdf('t.pdf', b'OTHER-SENTINEL'))
        response = self.client.get(self.download_url(mine))
        self.assertEqual(b''.join(response.streaming_content), b'MINE-SENTINEL')
        self.assertEqual(self.client.get(self.download_url(sibling)).status_code, 404)
        self.assertEqual(self.client.get(self.download_url(theirs)).status_code, 404)
        self.assertEqual(self.client.get(
            reverse('portal_document_download', kwargs={'token': 'nope', 'pk': mine.pk})).status_code, 404)

    def test_upload_is_stored_under_a_random_name(self):
        self.upload(file=pdf('Alice A GP letter.pdf'), name='GP letter')
        doc = Document.objects.get()
        self.assertEqual(doc.name, 'GP letter')
        self.assertNotIn('Alice', doc.file.name)
        self.assertTrue(doc.file.name.startswith('documents/') and doc.file.name.endswith('.pdf'))

    def test_download_keeps_the_file_type(self):
        self.upload(file=pdf('scan.pdf'), name='GP letter')
        doc = Document.objects.get()
        response = self.client.get(self.download_url(doc))
        self.assertIn('filename="GP letter.pdf"', response['Content-Disposition'])
        self.assertEqual(response['Content-Type'], 'application/pdf')
        staff = self.client_class()
        staff.force_login(self.admin)
        response = staff.get(reverse('document_download', kwargs={'org_slug': self.org.slug, 'member_pk': self.member.pk, 'pk': doc.pk}))
        self.assertIn('filename="GP letter.pdf"', response['Content-Disposition'])

    def test_portal_lists_documents_and_the_upload_form(self):
        Document.objects.create(member=self.member, name='Consent form', file=pdf())
        Document.objects.create(member=self.sibling, name='SIBLING-DOC', file=pdf())
        response = self.client.get(reverse('member_portal', kwargs={'token': self.member.token}))
        self.assertContains(response, 'Consent form')
        self.assertNotContains(response, 'SIBLING-DOC')
        self.assertContains(response, reverse('portal_document_upload', kwargs={'token': self.member.token}))

    def test_staff_see_it_marked_from_member(self):
        self.upload(name='GP letter')
        self.client.force_login(self.admin)
        response = self.client.get(reverse('member_detail', kwargs={'org_slug': self.org.slug, 'pk': self.member.pk}))
        self.assertContains(response, 'GP letter')
        self.assertContains(response, 'From member')
