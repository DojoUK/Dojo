import json

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from documents.models import WaiverTemplate
from members.models import MemberApplication
from organisations.features import FEATURES_BY_KEY
from organisations.models import Organisation

ALL_ON = {key: True for key in FEATURES_BY_KEY}
SITE = 'https://www.club.example'


@override_settings(SIGNUP_API_ORIGINS=[SITE], SIGNUP_API_RATE_LIMIT=3)
class SignupApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organisation.objects.create(name='Test Club', slug='test-club', features=dict(ALL_ON))
        cls.other = Organisation.objects.create(name='Other Club', slug='other-club', features=dict(ALL_ON))

    def setUp(self):
        cache.clear()
        self.url = reverse('member_signup_api', kwargs={'org_slug': self.org.slug})

    def post(self, data, origin=SITE, **extra):
        headers = {'Origin': origin} if origin else {}
        return self.client.post(
            self.url, json.dumps(data), content_type='application/json', headers=headers, **extra,
        )

    def test_json_application_is_created_for_the_club_in_the_url(self):
        response = self.post({'name': 'Alice Applicant', 'email': 'alice@example.com', 'date_of_birth': '2015-03-01'})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json(), {'ok': True})
        application = MemberApplication.objects.get()
        self.assertEqual(application.organisation, self.org)
        self.assertEqual(application.name, 'Alice Applicant')
        self.assertEqual(str(application.date_of_birth), '2015-03-01')
        self.assertEqual(response['Access-Control-Allow-Origin'], SITE)

    def test_form_encoded_application_is_accepted(self):
        response = self.client.post(self.url, {'name': 'Bob Form'}, headers={'Origin': SITE})
        self.assertEqual(response.status_code, 201)
        self.assertTrue(MemberApplication.objects.filter(name='Bob Form').exists())

    def test_request_with_no_origin_is_allowed(self):
        """Server-to-server calls carry no Origin; CORS only guards browsers."""
        self.assertEqual(self.post({'name': 'Carol'}, origin=None).status_code, 201)

    def test_other_origin_is_refused_and_nothing_stored(self):
        response = self.post({'name': 'Mallory'}, origin='https://evil.example')
        self.assertEqual(response.status_code, 403)
        self.assertNotIn('Access-Control-Allow-Origin', response)
        self.assertFalse(MemberApplication.objects.exists())

    def test_preflight_from_allowed_origin(self):
        response = self.client.options(self.url, headers={'Origin': SITE})
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response['Access-Control-Allow-Origin'], SITE)
        self.assertIn('POST', response['Access-Control-Allow-Methods'])

    def test_missing_name_and_bad_email_are_reported(self):
        response = self.post({'email': 'not-an-email'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['errors'], ['Full name is required.', 'Enter a valid email.'])
        self.assertFalse(MemberApplication.objects.exists())

    def test_over_long_value_is_an_error_not_a_crash(self):
        response = self.post({'name': 'Dan', 'phone': '1' * 50})
        self.assertEqual(response.status_code, 400)
        self.assertIn('Phone must be at most 20 characters.', response.json()['errors'])

    def test_invalid_json(self):
        response = self.client.post(self.url, '{nope', content_type='application/json', headers={'Origin': SITE})
        self.assertEqual(response.status_code, 400)
        response = self.client.post(self.url, '[1, 2]', content_type='application/json', headers={'Origin': SITE})
        self.assertEqual(response.status_code, 400)

    def test_honeypot_looks_successful_but_stores_nothing(self):
        response = self.post({'name': 'Bot', 'website': 'http://spam.example'})
        self.assertEqual(response.status_code, 201)
        self.assertFalse(MemberApplication.objects.exists())

    def test_rate_limit_per_address(self):
        for i in range(3):
            self.assertEqual(self.post({'name': f'Person {i}'}).status_code, 201)
        response = self.post({'name': 'One too many'})
        self.assertEqual(response.status_code, 429)
        self.assertEqual(MemberApplication.objects.count(), 3)
        # A different address still gets through.
        self.assertEqual(self.post({'name': 'Elsewhere'}, REMOTE_ADDR='10.0.0.9').status_code, 201)

    def test_made_up_forwarded_address_does_not_dodge_the_limit(self):
        """Without a proxy in front, X-Forwarded-For comes from the client, so it's ignored."""
        for i in range(5):
            self.post({'name': f'Spammer {i}'}, HTTP_X_FORWARDED_FOR=f'203.0.113.{i}')
        self.assertEqual(MemberApplication.objects.count(), 3)

    @override_settings(TRUST_PROXY_HTTPS=True)
    def test_behind_a_proxy_the_address_it_saw_is_counted(self):
        # A proxy that appends (nginx) leaves whatever the client sent in front.
        for i in range(5):
            self.post({'name': f'Spammer {i}'}, REMOTE_ADDR='172.18.0.2', HTTP_X_FORWARDED_FOR=f'203.0.113.{i}, 198.51.100.7')
        self.assertEqual(MemberApplication.objects.count(), 3)
        # Different visitors behind the same proxy each get their own allowance.
        response = self.post({'name': 'Neighbour'}, REMOTE_ADDR='172.18.0.2', HTTP_X_FORWARDED_FOR='198.51.100.8')
        self.assertEqual(response.status_code, 201)

    @override_settings(SIGNUP_API_ORIGINS=[SITE, 'other-club=https://other.example'])
    def test_a_website_listed_for_one_club_cannot_post_to_another(self):
        other_url = reverse('member_signup_api', kwargs={'org_slug': self.other.slug})
        post = lambda url: self.client.post(  # noqa: E731
            url, json.dumps({'name': 'Zed'}), content_type='application/json', headers={'Origin': 'https://other.example'},
        )
        self.assertEqual(post(self.url).status_code, 403)
        self.assertEqual(post(other_url).status_code, 201)
        self.assertEqual(list(MemberApplication.objects.values_list('organisation__slug', flat=True)), ['other-club'])
        # A plain entry still works for every club.
        self.assertEqual(self.post({'name': 'Yan'}).status_code, 201)

    def test_bad_date_of_birth_is_reported(self):
        response = self.post({'name': 'Dee', 'date_of_birth': '31/02/2015'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['errors'], ['Enter the date of birth as YYYY-MM-DD.'])
        self.assertFalse(MemberApplication.objects.exists())

    def test_required_waiver_needs_a_signature(self):
        WaiverTemplate.objects.create(organisation=self.org, name='Club rules', is_required=True, is_active=True)
        response = self.post({'name': 'Erin'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['errors'], ['Please sign the document before submitting.'])
        response = self.post({'name': 'Erin', 'signature_data': 'data:image/png;base64,AAAA'})
        self.assertEqual(response.status_code, 201)

    def test_switched_off_fields_are_not_stored(self):
        self.org.optional_fields = {'medical_info': False, 'address': False}
        self.org.save()
        self.post({'name': 'Fay', 'medical_info': 'Asthma', 'postcode': 'BS1 1AA', 'phone': '0123'})
        application = MemberApplication.objects.get()
        self.assertEqual(application.medical_info, '')
        self.assertEqual(application.postcode, '')
        self.assertEqual(application.phone, '0123')

    def test_get_lists_club_name_active_waivers_and_fields(self):
        WaiverTemplate.objects.create(organisation=self.org, name='Club rules', is_required=True, is_active=True)
        WaiverTemplate.objects.create(organisation=self.org, name='Old form', is_active=False)
        WaiverTemplate.objects.create(organisation=self.other, name='Other club form', is_active=True)
        self.org.optional_fields = {'medical_info': False}
        self.org.save()
        response = self.client.get(self.url, headers={'Origin': SITE})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['organisation'], 'Test Club')
        self.assertEqual([w['name'] for w in body['waivers']], ['Club rules'])
        self.assertTrue(body['waivers'][0]['required'])
        self.assertFalse(body['fields']['medical_info'])
        self.assertTrue(body['fields']['email'])

    def test_get_never_shows_members_or_applications(self):
        MemberApplication.objects.create(organisation=self.org, name='SENTINEL-APPLICANT', medical_info='SENTINEL-MEDICAL')
        content = self.client.get(self.url).content.decode()
        self.assertNotIn('SENTINEL', content)

    def test_waivers_hidden_when_documents_switched_off(self):
        WaiverTemplate.objects.create(organisation=self.org, name='Club rules', is_required=True, is_active=True)
        self.org.features = {**ALL_ON, 'documents': False}
        self.org.save()
        self.assertEqual(self.client.get(self.url).json()['waivers'], [])
        # And a required waiver the club can't show doesn't block applications.
        self.assertEqual(self.post({'name': 'Gus'}).status_code, 201)

    def test_club_without_applications_gets_404(self):
        self.org.features = {**ALL_ON, 'applications': False}
        self.org.save()
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.post({'name': 'Hal'}).status_code, 404)
        self.assertFalse(MemberApplication.objects.exists())

    def test_unknown_club_gets_404(self):
        url = reverse('member_signup_api', kwargs={'org_slug': 'no-such-club'})
        self.assertEqual(self.client.get(url).status_code, 404)


class JoinPageTests(TestCase):
    """The join page shares create_application with the API."""

    @classmethod
    def setUpTestData(cls):
        cls.org = Organisation.objects.create(name='Test Club', slug='test-club', features=dict(ALL_ON))

    def test_join_page_creates_application(self):
        url = reverse('member_signup', kwargs={'org_slug': self.org.slug})
        response = self.client.post(url, {'name': 'Ivy', 'email': 'ivy@example.com'})
        self.assertContains(response, 'Test Club')
        self.assertTrue(MemberApplication.objects.filter(name='Ivy', organisation=self.org).exists())

    def test_join_page_shows_errors_and_keeps_input(self):
        url = reverse('member_signup', kwargs={'org_slug': self.org.slug})
        response = self.client.post(url, {'email': 'jo@example.com'})
        self.assertContains(response, 'Full name is required.')
        self.assertContains(response, 'jo@example.com')
        self.assertFalse(MemberApplication.objects.exists())

    def test_join_page_drops_switched_off_fields(self):
        self.org.optional_fields = {'guardians': False}
        self.org.save()
        url = reverse('member_signup', kwargs={'org_slug': self.org.slug})
        self.client.post(url, {'name': 'Kit', 'guardian_name': 'Parent'})
        self.assertEqual(MemberApplication.objects.get().guardian_name, '')
