"""The Dojo network: member accounts across clubs, the club finder, favourites and joining clubs."""
import re
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from classes.models import Class, ClassMember, Session
from members.models import Guardian, Member, MemberApplication
from network import accounts, finder
from network.models import FavouriteClub, MemberAccount
from organisations.features import FEATURES_BY_KEY
from organisations.models import Organisation, OrganisationMember

ALL_ON = {key: True for key in FEATURES_BY_KEY}
# What sign-up requires besides the name, email and password.
SIGN_UP_DETAILS = {'date_of_birth': '1990-05-01', 'phone': '07700 900000', 'address_line1': '1 Test St',
                   'city': 'Cardiff', 'postcode': 'CF10 1AA', 'emergency_contact_name': 'Pat Contact',
                   'emergency_contact_phone': '07700 900099'}


def club(name, lat, lng, disciplines=('judo',), **kw):
    return Organisation.objects.create(
        name=name, features=dict(ALL_ON), address_line1='1 High St', city='Town', postcode='AB1 2CD',
        latitude=Decimal(str(lat)), longitude=Decimal(str(lng)), disciplines=list(disciplines), **kw,
    )


class NetworkTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        # Cardiff, Newport (~11 miles away) and London (~130 miles away).
        cls.cardiff = club('Cardiff Judo', 51.4816, -3.1791, ('judo',))
        cls.newport = club('Newport BJJ', 51.5842, -2.9977, ('bjj', 'mma'))
        cls.london = club('London Karate', 51.5074, -0.1278, ('karate',))

    def sign_up(self, email='sam@example.com', name='Sam Smith', password='a-long-passphrase-1', **details):
        first, _, surname = name.rpartition(' ') if ' ' in name else (name, '', '')
        return self.client.post(reverse('network_signup'), {
            'first_name': first, 'surname': surname, 'email': email, 'password': password, 'password_confirm': password,
            **SIGN_UP_DETAILS, **details,
        })

    def blank_details(self):
        """As an account from before sign-up asked for details (so club records can fill it in)."""
        MemberAccount.objects.update(date_of_birth=None, **{f: '' for f in SIGN_UP_DETAILS if f != 'date_of_birth'})

    def account(self, email='sam@example.com'):
        return MemberAccount.objects.get(user__email=email)

    def verify(self, account):
        token = accounts.verify_token(account)
        return self.client.get(reverse('network_verify', kwargs={'token': token}))


class AccountTests(NetworkTestBase):
    def test_sign_up_signs_in_and_emails_a_confirmation_link(self):
        response = self.sign_up(email='Sam@Example.com ')
        self.assertRedirects(response, reverse('network_progression') + '?welcome=1', fetch_redirect_response=False)
        account = self.account()
        self.assertEqual((account.name, account.user.username, account.is_verified), ('Sam Smith', 'sam@example.com', False))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('/account/verify/', mail.outbox[0].body)
        self.assertContains(self.client.get(reverse('network_home')), 'Confirm your email')

    def test_email_must_be_new_and_password_strong(self):
        self.sign_up()
        self.client.logout()
        response = self.sign_up(email='SAM@example.com')
        self.assertContains(response, 'already a Dojo account with that email')
        response = self.sign_up(email='new@example.com', password='password')
        self.assertContains(response, 'too common')
        self.assertEqual(MemberAccount.objects.count(), 1)

    def test_sign_in_with_email_and_sign_out(self):
        self.sign_up()
        self.client.post(reverse('network_logout'))
        response = self.client.post(reverse('network_login'), {'username': 'SAM@example.com', 'password': 'a-long-passphrase-1'})
        self.assertRedirects(response, reverse('network_home'), fetch_redirect_response=False)
        self.client.post(reverse('network_logout'))
        response = self.client.post(reverse('network_login'), {'username': 'sam@example.com', 'password': 'wrong'})
        self.assertContains(response, "don't match a Dojo account")

    def test_account_pages_need_signing_in(self):
        response = self.client.get(reverse('network_home'))
        self.assertRedirects(response, reverse('network_login') + '?next=/account/', fetch_redirect_response=False)

    def test_password_reset_email(self):
        self.sign_up()
        self.client.post(reverse('network_logout'))
        mail.outbox.clear()
        self.client.post(reverse('network_password_reset'), {'email': 'sam@example.com'})
        self.assertEqual(len(mail.outbox), 1)
        link = re.search(r'https?://[^/]+(/account/password-reset/\S+/)', mail.outbox[0].body).group(1)
        page = self.client.get(link, follow=True)
        self.assertContains(page, 'Choose a new password')

    def test_bad_or_stale_verification_links(self):
        self.sign_up()
        self.assertEqual(self.client.get(reverse('network_verify', kwargs={'token': 'nonsense'})).status_code, 400)
        account = self.account()
        token = accounts.verify_token(account)
        account.user.email = 'changed@example.com'
        account.user.save()
        self.assertEqual(self.client.get(reverse('network_verify', kwargs={'token': token})).status_code, 400)


class LinkingTests(NetworkTestBase):
    def test_verifying_links_memberships_by_email_or_guardian_email(self):
        mine = Member.objects.create(organisation=self.cardiff, name='Sam Smith', email='sam@example.com')
        child = Member.objects.create(organisation=self.newport, name='Kid Smith')
        Guardian.objects.create(member=child, name='Sam Smith', email='SAM@example.com', relationship='Parent')
        other = Member.objects.create(organisation=self.london, name='Someone Else', email='else@example.com')
        self.sign_up()
        self.assertFalse(Member.objects.filter(account__isnull=False).exists())   # not before verifying
        response = self.verify(self.account())
        self.assertRedirects(response, reverse('network_home'), fetch_redirect_response=False)
        self.assertEqual(set(self.account().memberships.all()), {mine, child})
        other.refresh_from_db()
        self.assertIsNone(other.account)
        home = self.client.get(reverse('network_home')).content.decode()
        self.assertIn('Cardiff Judo', home)
        self.assertIn('Kid Smith', home)
        self.assertIn(reverse('portal_bookings', kwargs={'token': mine.token}), home)

    def test_members_added_later_are_picked_up(self):
        self.sign_up()
        self.verify(self.account())
        later = Member.objects.create(organisation=self.cardiff, name='Sam Smith', email='sam@example.com')
        self.client.get(reverse('network_home'))
        later.refresh_from_db()
        self.assertEqual(later.account, self.account())

    def test_up_next_spans_clubs(self):
        mine = Member.objects.create(organisation=self.cardiff, name='Sam Smith', email='sam@example.com')
        juniors = Class.objects.create(organisation=self.cardiff, name='Juniors', is_active=True)
        Session.objects.create(assigned_class=juniors, date=timezone.localdate() + timedelta(days=3))
        ClassMember.objects.create(member=mine, assigned_class=juniors, auto_enrol=True)
        self.sign_up()
        self.verify(self.account())
        home = self.client.get(reverse('network_home')).content.decode()
        self.assertIn('Juniors', home[home.index('Up next'):])


class FinderTests(NetworkTestBase):
    def search(self, **params):
        return self.client.get(reverse('network_search'), params).json()

    def test_nearest_first_within_the_radius(self):
        data = self.search(lat=51.48, lng=-3.18, radius=25)
        self.assertEqual([c['name'] for c in data['clubs']], ['Cardiff Judo', 'Newport BJJ'])
        self.assertLess(data['clubs'][0]['miles'], 1)
        self.assertAlmostEqual(data['clubs'][1]['miles'], 11, delta=2)
        self.assertEqual(len(self.search(lat=51.48, lng=-3.18, radius=5)['clubs']), 1)
        self.assertEqual(len(self.search(lat=51.48, lng=-3.18, radius=100)['clubs']), 2)   # London is too far

    def test_filter_by_discipline(self):
        data = self.search(lat=51.48, lng=-3.18, radius=25, discipline='mma')
        self.assertEqual([c['name'] for c in data['clubs']], ['Newport BJJ'])
        self.assertEqual(data['clubs'][0]['disciplines'], ['Brazilian Jiu-Jitsu', 'MMA'])

    def test_without_a_point_lists_every_club_by_name(self):
        data = self.search()
        self.assertFalse(data['searched'])
        self.assertEqual([c['name'] for c in data['clubs']], ['Cardiff Judo', 'London Karate', 'Newport BJJ'])
        self.assertIsNone(data['clubs'][0]['miles'])

    def test_hidden_and_unplaced_clubs_are_left_out(self):
        self.london.listed_in_finder = False
        self.london.save()
        Organisation.objects.create(name='No Address Club', disciplines=['judo'])
        self.assertEqual([c['name'] for c in self.search()['clubs']], ['Cardiff Judo', 'Newport BJJ'])
        self.assertEqual(self.client.get(reverse('network_club', kwargs={'slug': self.london.slug})).status_code, 404)

    def test_bad_parameters_are_ignored(self):
        data = self.search(lat='abc', lng='1', radius='9999', discipline='<script>')
        self.assertFalse(data['searched'])
        self.assertEqual(data['radius'], finder.DEFAULT_RADIUS)
        self.assertEqual(len(data['clubs']), 3)

    def test_finder_and_club_pages(self):
        page = self.client.get(reverse('network_finder'))
        self.assertContains(page, 'Find a club')
        self.assertContains(page, 'data-discipline="bjj"')
        club_page = self.client.get(reverse('network_club', kwargs={'slug': self.newport.slug}))
        self.assertContains(club_page, 'Brazilian Jiu-Jitsu · MMA')
        self.assertContains(club_page, 'Apply to join')

    @override_settings(GOOGLE_MAPS_API_KEY='test-key', GOOGLE_MAPS_MAP_ID='')
    def test_maps_pin_clubs_with_the_dojo_logo(self):
        # Google's advanced markers, which only show on a map with a Map ID (DEMO_MAP_ID unless one is set).
        for page in (self.client.get(reverse('network_finder')),
                     self.client.get(reverse('network_club', kwargs={'slug': self.newport.slug}))):
            self.assertContains(page, 'libraries=marker')
            self.assertContains(page, 'DEMO_MAP_ID')
            self.assertContains(page, 'content: dojoPin()')
            self.assertNotContains(page, 'google.maps.Marker(')


class FavouriteAndJoinTests(NetworkTestBase):
    def setUp(self):
        self.sign_up()
        self.verify(self.account())

    def test_save_and_unsave_a_club(self):
        url = reverse('network_favourite', kwargs={'slug': self.newport.slug})
        self.assertEqual(self.client.post(url, HTTP_ACCEPT='application/json').json(), {'favourite': True})
        self.assertTrue(self.search_flag('favourite'))
        self.assertContains(self.client.get(reverse('network_home')), 'Newport BJJ')
        self.client.post(url, {'next': '/account/'})
        self.assertFalse(FavouriteClub.objects.exists())

    def search_flag(self, key):
        clubs = self.client.get(reverse('network_search')).json()['clubs']
        return next(c for c in clubs if c['slug'] == self.newport.slug)[key]

    def test_favourites_need_an_account(self):
        self.client.post(reverse('network_logout'))
        response = self.client.post(reverse('network_favourite', kwargs={'slug': self.newport.slug}))
        self.assertTrue(response.url.startswith(reverse('network_login')))

    def test_apply_from_the_account_and_approval_links_the_membership(self):
        response = self.client.get(reverse('network_join', kwargs={'slug': self.newport.slug}))
        self.assertRedirects(response, reverse('member_signup', kwargs={'org_slug': self.newport.slug}),
                             fetch_redirect_response=False)
        form = self.client.get(response.url).content.decode()
        self.assertIn('value="Sam"', form); self.assertIn('value="Smith"', form)   # filled in from the account
        self.client.post(response.url, {'name': 'Sam Smith', 'email': 'sam@example.com'})
        application = MemberApplication.objects.get(organisation=self.newport)
        self.assertEqual(application.account, self.account())
        self.assertEqual(self.search_flag('status'), 'applied')
        self.assertContains(self.client.get(reverse('network_home')), 'Waiting for the club')

        staff = User.objects.create_user('coach', password='pw')
        OrganisationMember.objects.create(user=staff, organisation=self.newport, role='org_admin')
        account_user = self.account().user
        self.client.force_login(staff)
        self.client.post(reverse('application_approve', kwargs={'org_slug': self.newport.slug, 'pk': application.pk}))
        member = Member.objects.get(organisation=self.newport)
        self.assertEqual(member.account, self.account())
        self.client.force_login(account_user)
        self.assertEqual(self.search_flag('status'), 'member')

    def test_clubs_without_online_applications(self):
        self.cardiff.features = {**self.cardiff.features, 'applications': False}
        self.cardiff.save()
        response = self.client.get(reverse('network_join', kwargs={'slug': self.cardiff.slug}), follow=True)
        self.assertContains(response, 'isn&#x27;t taking applications online')


class ApprovalEmailFailureTests(NetworkTestBase):
    def test_a_failed_welcome_email_still_approves_with_a_warning(self):
        from unittest import mock
        from django.contrib.messages import get_messages
        application = MemberApplication.objects.create(organisation=self.newport, name='Sam Smith', email='sam@example.com')
        staff = User.objects.create_user('coach', password='pw')
        OrganisationMember.objects.create(user=staff, organisation=self.newport, role='org_admin')
        self.client.force_login(staff)
        with mock.patch('members.emails.compose.send', side_effect=OSError('[Errno -2] Name or service not known')):
            response = self.client.post(reverse('application_approve', kwargs={'org_slug': self.newport.slug, 'pk': application.pk}))
        member = Member.objects.get(organisation=self.newport)
        self.assertRedirects(response, reverse('member_detail', kwargs={'org_slug': self.newport.slug, 'pk': member.pk}),
                             fetch_redirect_response=False)
        texts = [str(m) for m in get_messages(response.wsgi_request)]
        self.assertTrue(any('welcome email couldn\'t be sent' in t for t in texts), texts)


class MyDetailsTests(NetworkTestBase):
    """My details: kept once in the account, used to fill in join forms, copied to the member's own club records."""

    def setUp(self):
        self.sign_up()
        self.verify(self.account())

    def save_details(self, **data):
        name = data.pop('name', 'Sam Smith')
        first, _, surname = name.partition(' ')
        base = {'first_name': first, 'surname': surname, 'phone': '07700 900001', 'address_line1': '2 Mill Lane', 'city': 'Cardiff',
                'postcode': 'CF11 1AA', 'emergency_contact_name': 'Pat Smith', 'emergency_contact_phone': '07700 900002',
                'medical_info': 'Asthma', 'date_of_birth': '1990-05-01'}
        return self.client.post(reverse('network_details'), {**base, **data})

    def test_save_and_validate(self):
        self.assertContains(self.client.get(reverse('network_details')), 'Emergency contacts')
        self.save_details()
        account = self.account()
        self.assertEqual((account.phone, account.city, account.medical_info, str(account.date_of_birth)),
                         ('07700 900001', 'Cardiff', 'Asthma', '1990-05-01'))
        response = self.save_details(guardian_email='not-an-email', date_of_birth='31/02/2000')
        self.assertContains(response, 'Enter a valid email for the parent or guardian.')
        self.assertContains(response, 'Enter the date of birth as a date.')
        self.assertEqual(self.account().phone, '07700 900001')   # nothing saved when there are errors

    def holder(self, org, **kwargs):
        return Member.objects.create(organisation=org, account=self.account(), account_role='holder',
                                     **{'name': 'Sam Smith', 'email': 'sam@example.com', **kwargs})

    def test_saving_updates_own_club_records_with_only_what_each_club_records(self):
        me = self.holder(self.cardiff, name='Samuel Smith')
        me_newport = self.holder(self.newport)
        child = Member.objects.create(organisation=self.cardiff, name='Kid Smith', email='sam@example.com',
                                      account=self.account(), account_role='dependant')
        self.newport.optional_fields = {**self.newport.optional_fields, 'address': False, 'medical_info': False}
        self.newport.save()
        response = self.save_details()
        me.refresh_from_db(); me_newport.refresh_from_db(); child.refresh_from_db()
        self.assertEqual((me.phone, me.city, me.postcode, me.emergency_contact_name, me.medical_info),
                         ('07700 900001', 'Cardiff', 'CF11 1AA', 'Pat Smith', 'Asthma'))
        self.assertEqual((me.first_name, me.surname, me.name), ('Sam', 'Smith', 'Sam Smith'))   # the member's own name
        self.assertEqual(str(me.date_of_birth), '1990-05-01')   # date of birth is the member's own
        self.assertEqual((me_newport.phone, me_newport.city, me_newport.medical_info), ('07700 900001', '', ''))
        self.assertEqual((child.phone, child.medical_info), ('', ''))   # a child's record isn't the account holder's
        texts = [str(m) for m in response.wsgi_request._messages]
        self.assertIn('My details saved. Updated at Cardiff Judo, Newport BJJ.', texts)

    def test_a_blank_only_reaches_clubs_when_the_member_clears_it(self):
        self.save_details()
        me = self.holder(self.cardiff, emergency_contact_2_name='Jo Smith')
        self.save_details()                                     # account has no second contact: club keeps theirs
        me.refresh_from_db()
        self.assertEqual(me.emergency_contact_2_name, 'Jo Smith')
        self.save_details(medical_info='')                      # clearing a detail clears it at the clubs too
        me.refresh_from_db()
        self.assertEqual(me.medical_info, '')

    def test_archived_and_anonymised_records_are_left_alone(self):
        self.save_details()
        archived = self.holder(self.cardiff, is_active=False)
        gone = self.holder(self.newport)
        gone.anonymise()
        self.save_details(phone='07700 123456')
        archived.refresh_from_db(); gone.refresh_from_db()
        self.assertEqual(archived.phone, '')
        self.assertEqual((gone.phone, gone.account_id, gone.account_role), ('', None, ''))
        self.assertEqual(self.account().phone, '07700 123456')   # the account itself isn't touched by a club

    def test_parents_guardian_rows_follow_their_account(self):
        child = Member.objects.create(organisation=self.cardiff, name='Kid Smith', account=self.account(),
                                      account_role='dependant')
        guardian = Guardian.objects.create(member=child, name='Sam', email='sam@example.com', phone='1',
                                           relationship='Parent', account=self.account())
        self.save_details(name='Sam Jones', phone='07700 222222')
        guardian.refresh_from_db()
        self.assertEqual((guardian.name, guardian.phone, guardian.relationship), ('Sam Jones', '07700 222222', 'Parent'))

    def test_join_form_starts_with_only_what_the_club_collects(self):
        self.save_details(emergency_contact_2_name='Jo Smith')
        self.newport.optional_fields = {**self.newport.optional_fields, 'address': False}
        self.newport.save()
        form = self.client.get(reverse('member_signup', kwargs={'org_slug': self.newport.slug})).content.decode()
        self.assertIn('value="07700 900001"', form)
        self.assertIn('value="Pat Smith"', form)
        self.assertIn('Asthma', form)
        self.assertNotIn('2 Mill Lane', form)                    # Newport doesn't ask for an address
        self.assertIn('filled this in from your Dojo account', form)

    def test_changes_on_the_join_form_are_saved_for_next_time(self):
        self.save_details()
        url = reverse('member_signup', kwargs={'org_slug': self.cardiff.slug})
        self.client.post(url, {'name': 'Sam Smith', 'email': 'sam@example.com', 'phone': '07700 999999',
                               'emergency_contact_name': '', 'applicant': 'me'})
        account = self.account()
        self.assertEqual(account.phone, '07700 999999')
        self.assertEqual(account.emergency_contact_name, 'Pat Smith')   # a blank field doesn't wipe it
        application = MemberApplication.objects.get()
        self.assertEqual(application.phone, '07700 999999')

    def test_applying_for_someone_else_leaves_my_details_alone(self):
        self.save_details()
        self.client.post(reverse('member_signup', kwargs={'org_slug': self.cardiff.slug}),
                         {'name': 'Kid Smith', 'email': 'sam@example.com', 'phone': '07700 111111', 'applicant': 'other'})
        self.assertFalse(MemberApplication.objects.get().for_account_holder)
        self.assertEqual((self.account().name, self.account().phone), ('Sam Smith', '07700 900001'))

    def test_approval_copies_the_full_address_and_emergency_contacts(self):
        application = MemberApplication.objects.create(
            organisation=self.cardiff, name='Sam Smith', email='sam@example.com', account=self.account(),
            address_line1='2 Mill Lane', city='Cardiff', county='South Glamorgan', postcode='CF11 1AA',
            emergency_contact_name='Pat Smith', emergency_contact_phone='07700 900002',
            emergency_contact_2_name='Jo Smith', emergency_contact_2_phone='07700 900003')
        staff = User.objects.create_user('coach', password='pw')
        OrganisationMember.objects.create(user=staff, organisation=self.cardiff, role='org_admin')
        self.client.force_login(staff)
        self.client.post(reverse('application_approve', kwargs={'org_slug': self.cardiff.slug, 'pk': application.pk}))
        member = Member.objects.get(organisation=self.cardiff)
        self.assertEqual((member.city, member.county, member.postcode, member.emergency_contact_name,
                          member.emergency_contact_2_phone),
                         ('Cardiff', 'South Glamorgan', 'CF11 1AA', 'Pat Smith', '07700 900003'))

    def test_club_portal_points_account_holders_to_my_details(self):
        me = self.holder(self.cardiff, phone='07700 900001', medical_info='Asthma')
        url = reverse('portal_my_details', kwargs={'token': me.token})
        page = self.client.get(url).content.decode()
        self.assertIn('Edit in My details', page)
        self.assertNotIn('name="medical_info"', page)
        self.client.post(url, {})                     # saving the page can't wipe details kept in the account
        me.refresh_from_db()
        self.assertEqual((me.phone, me.medical_info), ('07700 900001', 'Asthma'))


class AccountLinkTests(NetworkTestBase):
    """Which club records are the account holder's own, and parents linked as guardians."""

    def staff_client(self, org):
        from django.test import Client
        staff = User.objects.create_user(f'coach-{org.slug}', password='pw')
        OrganisationMember.objects.create(user=staff, organisation=org, role='org_admin')
        client = Client()
        client.force_login(staff)
        return client

    def test_verifying_links_own_and_childrens_records_and_starts_my_details_from_them(self):
        me = Member.objects.create(organisation=self.cardiff, name='Sam', email='SAM@example.com', phone='07700 1',
                                   city='Cardiff', medical_info='Asthma')
        me2 = Member.objects.create(organisation=self.newport, name='Sam', email='sam@example.com', phone='07700 1',
                                    city='Newport')
        child = Member.objects.create(organisation=self.cardiff, name='Kid')
        guardian = Guardian.objects.create(member=child, name='Sam', email='sam@example.com')
        self.sign_up()
        self.blank_details()
        account = self.account()
        self.verify(account)
        me.refresh_from_db(); me2.refresh_from_db(); child.refresh_from_db(); guardian.refresh_from_db()
        self.assertEqual((me.account_role, me2.account_role, child.account_role), ('holder', 'holder', 'dependant'))
        self.assertEqual(guardian.account, account)
        account.refresh_from_db()
        self.assertEqual(account.phone, '07700 1')              # both clubs agree
        self.assertEqual(account.medical_info, 'Asthma')        # only one club holds it
        self.assertEqual(account.city, '')                       # clubs disagree: left for the member
        self.assertEqual((me.city, me2.city), ('Cardiff', 'Newport'))   # and the clubs keep theirs

    def test_approval_marks_who_the_application_was_for(self):
        self.sign_up()
        account = self.account()
        mine = MemberApplication.objects.create(organisation=self.cardiff, name='Sam', email='sam@example.com',
                                                account=account, for_account_holder=True)
        childs = MemberApplication.objects.create(organisation=self.cardiff, name='Kid', email='sam@example.com',
                                                  account=account, guardian_name='Sam',
                                                  guardian_email='Sam@example.com', guardian_phone='1')
        client = self.staff_client(self.cardiff)
        for app in (mine, childs):
            client.post(reverse('application_approve', kwargs={'org_slug': self.cardiff.slug, 'pk': app.pk}))
        me, kid = Member.objects.get(name='Sam'), Member.objects.get(name='Kid')
        self.assertEqual((me.account_role, kid.account_role), ('holder', 'dependant'))
        self.assertEqual(kid.guardians.get().account, account)

    def test_staff_see_shared_details_read_only_on_an_account_holders_record(self):
        self.sign_up()
        me = Member.objects.create(organisation=self.cardiff, name='Sam', email='sam@example.com', phone='07700 1',
                                   medical_info='Asthma', account=self.account(), account_role='holder')
        guardian = Guardian.objects.create(member=me, name='Mum', email='mum@example.com')
        client = self.staff_client(self.cardiff)
        url = reverse('member_edit', kwargs={'org_slug': self.cardiff.slug, 'pk': me.pk})
        page = client.get(url).content.decode()
        self.assertTrue('keeps their name, preferred name, pronouns, email, date of birth, phone, address, emergency contacts and medical information up to date' in page)
        self.assertRegex(page, r'name="phone"[^>]*disabled')
        client.post(url, {'first_name': 'Samuel', 'email': 'other@example.com', 'phone': '999', 'medical_info': '',
                          'is_active': 'on', 'guardians-TOTAL_FORMS': '1', 'guardians-INITIAL_FORMS': '1',
                          'guardians-MIN_NUM_FORMS': '0', 'guardians-MAX_NUM_FORMS': '1000',
                          'guardians-0-id': guardian.pk, 'guardians-0-member': me.pk, 'guardians-0-name': 'Mum',
                          'guardians-0-email': 'mum@example.com'})
        me.refresh_from_db()
        self.assertEqual((me.name, me.email, me.phone, me.medical_info), ('Sam', 'sam@example.com', '07700 1', 'Asthma'))

    def test_staff_can_still_edit_a_childs_record(self):
        self.sign_up()
        kid = Member.objects.create(organisation=self.cardiff, name='Kid', account=self.account(), account_role='dependant')
        client = self.staff_client(self.cardiff)
        page = client.get(reverse('member_edit', kwargs={'org_slug': self.cardiff.slug, 'pk': kid.pk})).content.decode()
        self.assertNotIn('up to date in their Dojo account', page)
        self.assertNotRegex(page, r'name="phone"[^>]*disabled')

    def test_join_form_asks_who_its_for(self):
        self.sign_up()
        page = self.client.get(reverse('member_signup', kwargs={'org_slug': self.cardiff.slug})).content.decode()
        self.assertIn('Who is this application for?', page)
        self.assertIn('id="accountHolder"', page)

    def test_migration_links_existing_records_without_changing_club_data(self):
        import importlib
        from django.apps import apps
        migration = importlib.import_module('members.migrations.0020_link_existing_accounts')
        self.sign_up()
        self.blank_details()
        account = self.account()
        me = Member.objects.create(organisation=self.cardiff, name='Sam', email='sam@example.com', account=account,
                                   phone='07700 1', postcode='CF1')
        dupe = Member.objects.create(organisation=self.cardiff, name='Sam', email='sam@example.com', account=account)
        me2 = Member.objects.create(organisation=self.newport, name='Sam', email='sam@example.com', account=account,
                                    phone='07700 1', postcode='NP1')
        kid = Member.objects.create(organisation=self.cardiff, name='Kid', account=account)
        guardian = Guardian.objects.create(member=kid, name='Sam', email='SAM@example.com')
        account.medical_info = 'Already set'
        account.save()
        migration.link(apps, None)
        for m in (me, dupe, me2, kid, guardian):
            m.refresh_from_db()
        self.assertEqual([m.account_role for m in (me, dupe, me2, kid)], ['holder', 'dependant', 'holder', 'dependant'])
        self.assertEqual(guardian.account, account)
        account.refresh_from_db()
        self.assertEqual((account.phone, account.postcode, account.medical_info), ('07700 1', '', 'Already set'))
        self.assertEqual((me.postcode, me2.postcode), ('CF1', 'NP1'))


class SettingsSectionTests(NetworkTestBase):
    """My settings: one page of tabs, laid out like Organisation settings."""

    def setUp(self):
        self.staff = User.objects.create_user('coach', password='pw')
        self.membership = OrganisationMember.objects.create(user=self.staff, organisation=self.cardiff, role='coach')
        self.cardiff.optional_fields = {**(self.cardiff.optional_fields or {}), 'dbs': True, 'coaching_licence': True}
        self.cardiff.save()
        self.client.force_login(self.staff)
        self.url = reverse('account_settings', kwargs={'org_slug': self.cardiff.slug})

    def test_tabs_and_saving_returns_to_the_tab(self):
        page = self.client.get(self.url).content.decode()
        for tab in ('Profile details', 'Coaching certificates', 'Calendar &amp; colours', 'Holidays', 'Security',
                    'name="calendar_colour"', 'name="accent_theme"', 'name="start_date"', 'name="dbs_number"'):
            self.assertTrue(tab in page, tab)
        response = self.client.post(self.url, {'action': 'add_holiday', 'start_date': '2026-12-01', 'end_date': '2026-12-02'})
        self.assertRedirects(response, self.url + '#holidays', fetch_redirect_response=False)
        old = self.client.get(reverse('account_appearance', kwargs={'org_slug': self.cardiff.slug}))
        self.assertRedirects(old, self.url + '#calendar', fetch_redirect_response=False)

    def test_staff_keep_their_own_certificates(self):
        response = self.client.post(self.url, {'action': 'update_certificates', 'dbs_number': '001234',
                                               'dbs_expiry': '2028-01-31', 'coaching_licence': 'BJA Level 2',
                                               'coaching_licence_expiry': ''})
        self.assertRedirects(response, self.url + '#certificates', fetch_redirect_response=False)
        self.membership.refresh_from_db()
        self.assertEqual((self.membership.dbs_number, str(self.membership.dbs_expiry), self.membership.coaching_licence,
                          self.membership.coaching_licence_expiry), ('001234', '2028-01-31', 'BJA Level 2', None))

    def test_certificates_tab_only_when_the_club_records_them(self):
        self.cardiff.optional_fields = {**(self.cardiff.optional_fields or {}), 'dbs': False, 'coaching_licence': False}
        self.cardiff.save()
        self.assertFalse('Coaching certificates' in self.client.get(self.url).content.decode())

    def test_an_administrator_not_on_the_staff_list_is_told_why_tabs_are_empty(self):
        admin = User.objects.create_superuser('root', password='pw')
        self.cardiff.settings = {**(self.cardiff.settings or {}), 'two_factor': Organisation.TWO_FACTOR_OPTIONAL}
        self.cardiff.save()
        self.client.force_login(admin)
        page = self.client.get(self.url).content.decode()
        self.assertTrue("aren't on Cardiff Judo's staff list" in page or 'aren&#x27;t on Cardiff Judo' in page)


class JoinFormTests(NetworkTestBase):
    """Custom fields and waivers on the join form, and what approval does with them."""

    def setUp(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from documents.models import WaiverTemplate
        from members.models import CustomField
        self.consent = CustomField.objects.create(organisation=self.cardiff, name='Photo consent', field_type='boolean')
        self.kit = CustomField.objects.create(organisation=self.cardiff, name='Kit issued', field_type='boolean')
        self.waiver = WaiverTemplate.objects.create(organisation=self.cardiff, name='Code of conduct',
                                                    file=SimpleUploadedFile('coc.pdf', b'%PDF-1.4'), is_required=True)
        self.url = reverse('member_signup', kwargs={'org_slug': self.cardiff.slug})
        self.sign_up()

    def apply(self, **data):
        return self.client.post(self.url, {'name': 'Sam Smith', 'email': 'sam@example.com', **data})

    def approve(self, application):
        from unittest import mock
        import io
        staff = User.objects.create_user('admin', password='pw')
        OrganisationMember.objects.create(user=staff, organisation=self.cardiff, role='org_admin')
        self.client.force_login(staff)
        with mock.patch('documents.pdf_utils.stamp_signature_on_pdf', return_value=io.BytesIO(b'%PDF-1.4 signed')):
            self.client.post(reverse('application_approve', kwargs={'org_slug': self.cardiff.slug, 'pk': application.pk}))
        return Member.objects.get(organisation=self.cardiff)

    def test_only_fields_asked_on_the_join_form_appear_and_carry_through(self):
        self.assertFalse('Photo consent' in self.client.get(self.url).content.decode())   # off by default
        self.consent.on_application = True
        self.consent.save()
        page = self.client.get(self.url).content.decode()
        self.assertTrue('Photo consent' in page)
        self.assertFalse('Kit issued' in page)
        self.apply(**{f'cf_{self.consent.pk}': 'on', f'agree_{self.waiver.pk}': '1', 'signer_name': 'Sam Smith',
                      'signature_data': 'data:image/png;base64,AAAA'})
        application = MemberApplication.objects.get()
        self.assertEqual(application.custom_field_values, {str(self.consent.pk): True})
        self.assertEqual(application.custom_answers(), [('Photo consent', 'Yes')])
        member = self.approve(application)
        self.assertEqual(member.custom_field_values, {str(self.consent.pk): True})
        self.assertEqual(member.joined_date, timezone.localdate())

    def test_staff_switch_a_field_onto_the_join_form(self):
        staff = User.objects.create_user('admin2', password='pw')
        OrganisationMember.objects.create(user=staff, organisation=self.cardiff, role='org_admin')
        self.client.force_login(staff)
        self.client.post(reverse('org_custom_fields', kwargs={'org_slug': self.cardiff.slug}),
                         {'action': 'on_application', 'field_pk': self.consent.pk, 'value': '1'})
        self.consent.refresh_from_db()
        self.assertTrue(self.consent.on_application)

    def test_the_waiver_must_be_read_agreed_and_signed(self):
        page = self.client.get(self.url).content.decode()
        self.assertTrue('I&#x27;ve read Code of conduct and agree to it' in page or "I've read Code of conduct and agree to it" in page)
        self.assertTrue('<iframe' in page)
        response = self.apply(signature_data='data:image/png;base64,AAAA')
        self.assertContains(response, 'Tick to agree to Code of conduct.')
        response = self.apply(**{f'agree_{self.waiver.pk}': '1', 'signature_data': 'data:image/png;base64,AAAA'})
        self.assertContains(response, 'Type your full name to sign.')
        response = self.apply(**{f'agree_{self.waiver.pk}': '1', 'signer_name': 'Sam Smith'})
        self.assertContains(response, 'Please sign the document before submitting.')
        self.assertFalse(MemberApplication.objects.exists())

    def test_a_parent_signs_for_their_child_and_approval_files_the_signed_copy(self):
        from documents.models import SignedWaiver
        self.apply(name='Kid Smith', applicant='other', signer_name='Sam Smith',
                   signature_data='data:image/png;base64,AAAA', **{f'agree_{self.waiver.pk}': '1'})
        application = MemberApplication.objects.get()
        self.assertEqual((application.signer_name, application.agreed_waivers, application.signer_ip),
                         ('Sam Smith', [self.waiver.pk], '127.0.0.1'))
        member = self.approve(application)
        signed = SignedWaiver.objects.get()
        self.assertEqual((signed.member, signed.signer_name, signed.template, signed.ip_address),
                         (member, 'Sam Smith', self.waiver, '127.0.0.1'))

    def test_the_club_page_says_what_youll_sign(self):
        self.assertContains(self.client.get(reverse('network_club', kwargs={'slug': self.cardiff.slug})),
                            "sign Cardiff Judo's Code of conduct")


class ReviewTests(NetworkTestBase):
    def setUp(self):
        self.sign_up()
        self.verify(self.account())

    def member_of(self, org, account=None):
        return Member.objects.create(organisation=org, name='Sam', account=account or self.account(),
                                     account_role='holder')

    def review(self, org, rating, text=''):
        return self.client.post(reverse('network_review', kwargs={'slug': org.slug}), {'rating': rating, 'text': text})

    def test_only_members_can_review_and_can_edit_or_remove_it(self):
        from network.models import ClubReview
        self.review(self.cardiff, 5)
        self.assertFalse(ClubReview.objects.exists())
        self.member_of(self.cardiff)
        page = self.client.get(reverse('network_club', kwargs={'slug': self.cardiff.slug})).content.decode()
        self.assertTrue('Review Cardiff Judo' in page)
        self.review(self.cardiff, 2, 'Changing rooms were dirty.')
        self.review(self.cardiff, 4, 'Changing rooms sorted now, thanks.')
        review = ClubReview.objects.get()
        self.assertEqual((review.rating, review.text), (4, 'Changing rooms sorted now, thanks.'))
        self.review(self.cardiff, 9)
        review.refresh_from_db()
        self.assertEqual(review.rating, 4)
        self.client.post(reverse('network_review', kwargs={'slug': self.cardiff.slug}), {'action': 'delete'})
        self.assertFalse(ClubReview.objects.exists())

    def test_average_on_the_club_page_and_for_staff(self):
        from network.models import ClubReview
        other = accounts.create_account('Jo Bloggs', 'jo@example.com', 'a-long-passphrase-1')
        ClubReview.objects.create(account=self.account(), organisation=self.cardiff, rating=5, text='Brilliant coaching')
        ClubReview.objects.create(account=other, organisation=self.cardiff, rating=4)
        page = self.client.get(reverse('network_club', kwargs={'slug': self.cardiff.slug})).content.decode()
        self.assertTrue('4.5 (2 reviews)' in page)
        self.assertTrue('Brilliant coaching' in page and 'Jo B.' in page)
        staff = User.objects.create_user('coach', password='pw')
        OrganisationMember.objects.create(user=staff, organisation=self.cardiff, role='coach')
        self.client.force_login(staff)
        page = self.client.get(reverse('org_reviews', kwargs={'org_slug': self.cardiff.slug})).content.decode()
        self.assertTrue('Brilliant coaching' in page and 'From 2 reviews' in page)

    def test_finder_puts_better_rated_clubs_first_then_nearest(self):
        from network.models import ClubReview
        penarth = club('Penarth Judo', 51.4347, -3.1736)     # ~3 miles from Cardiff
        barry = club('Barry Judo', 51.4060, -3.2700)          # ~7 miles
        others = [accounts.create_account(f'Member {i}', f'm{i}@example.com', 'a-long-passphrase-1') for i in range(3)]
        ClubReview.objects.create(account=others[0], organisation=self.newport, rating=4)
        ClubReview.objects.create(account=others[1], organisation=barry, rating=4)
        ClubReview.objects.create(account=others[2], organisation=penarth, rating=4)
        ClubReview.objects.create(account=self.account(), organisation=barry, rating=5)   # Barry 4.5
        response = self.client.get(reverse('network_search'), {'lat': '51.4816', 'lng': '-3.1791', 'radius': '25'})
        clubs = response.json()['clubs']
        # Best rated first; Penarth and Newport both 4.0, so the nearer first; unrated Cardiff last though it's nearest.
        self.assertEqual([c['name'] for c in clubs], ['Barry Judo', 'Penarth Judo', 'Newport BJJ', 'Cardiff Judo'])
        self.assertEqual((clubs[0]['rating'], clubs[0]['reviews']), (4.5, 2))
        self.assertIsNone(clubs[-1]['rating'])


class FinderSortAndFilterTests(NetworkTestBase):
    def names(self, **params):
        query = {'lat': '51.4816', 'lng': '-3.1791', 'radius': '25', **params}
        return [c['name'] for c in self.client.get(reverse('network_search'), query).json()['clubs']]

    def test_sort_by_rating_distance_and_name(self):
        from network.models import ClubReview
        reviewer = accounts.create_account('Jo Bloggs', 'jo@example.com', 'a-long-passphrase-1')
        ClubReview.objects.create(account=reviewer, organisation=self.newport, rating=5)
        self.assertEqual(self.names(), ['Newport BJJ', 'Cardiff Judo'])                 # rating is the default
        self.assertEqual(self.names(sort='distance'), ['Cardiff Judo', 'Newport BJJ'])
        self.assertEqual(self.names(sort='az'), ['Cardiff Judo', 'Newport BJJ'])
        self.assertEqual(self.names(sort='za'), ['Newport BJJ', 'Cardiff Judo'])
        self.assertEqual(self.names(sort='nonsense'), ['Newport BJJ', 'Cardiff Judo'])
        # Without a place there's no distance, so that falls back to rating.
        everywhere = self.client.get(reverse('network_search'), {'sort': 'distance'}).json()['clubs']
        self.assertEqual([c['name'] for c in everywhere], ['Newport BJJ', 'Cardiff Judo', 'London Karate'])

    def test_only_martial_arts_a_listed_club_teaches_are_offered(self):
        page = self.client.get(reverse('network_finder')).content.decode()
        for offered in ('data-discipline="judo"', 'data-discipline="bjj"', 'data-discipline="mma"', 'data-discipline="karate"'):
            self.assertTrue(offered in page, offered)
        self.assertFalse('data-discipline="taekwondo"' in page)
        self.assertFalse('best rated first' in page)
        self.assertTrue('Sort: Rating' in page)
        self.newport.listed_in_finder = False
        self.newport.save()
        page = self.client.get(reverse('network_finder')).content.decode()
        self.assertFalse('data-discipline="mma"' in page)


class NameTests(NetworkTestBase):
    """First name and surname, plus the preferred name and pronouns members own, as registers show them."""

    def test_full_name_and_parts_stay_in_step(self):
        member = Member.objects.create(organisation=self.cardiff, name='Mary Jane Smith')
        self.assertEqual((member.first_name, member.surname), ('Mary Jane', 'Smith'))
        member.surname = 'Jones'
        member.save()
        self.assertEqual(member.name, 'Mary Jane Jones')
        member = Member.objects.get(pk=member.pk)
        member.name = 'Mary Jones'          # older code that only knows the full name
        member.save(update_fields=['name'])
        member.refresh_from_db()
        self.assertEqual((member.first_name, member.surname), ('Mary', 'Jones'))

    def test_register_name(self):
        member = Member(name='Alexander Smith')
        self.assertEqual(member.register_name, 'Alexander Smith')
        member.pronouns = 'he/him'
        self.assertEqual(member.register_name, 'Alexander Smith (he/him)')
        member.first_name, member.surname, member.preferred_name = 'Alexander', 'Smith', 'Alex'
        self.assertEqual(member.register_name, 'Alex Smith (he/him)')

    def test_sign_up_and_my_details_use_name_parts_and_preferred_name_follows_the_account(self):
        self.sign_up(name='Sam Smith')
        account = self.account()
        self.assertEqual((account.first_name, account.surname, account.name, account.user.last_name),
                         ('Sam', 'Smith', 'Sam Smith', 'Smith'))
        self.verify(account)
        me = Member.objects.create(organisation=self.cardiff, name='Samantha Smith', account=account, account_role='holder')
        self.client.post(reverse('network_details'), {'first_name': 'Sam', 'surname': 'Smith-Jones',
                                                      'preferred_name': 'Sammy', 'pronouns': 'she/her'})
        account.refresh_from_db(); me.refresh_from_db()
        self.assertEqual(account.name, 'Sam Smith-Jones')
        self.assertEqual((me.name, me.preferred_name, me.pronouns), ('Sam Smith-Jones', 'Sammy', 'she/her'))
        self.assertEqual(me.register_name, 'Sammy Smith-Jones (she/her)')

    def test_registers_and_class_pages_show_register_names(self):
        staff = User.objects.create_user('coach', password='pw')
        OrganisationMember.objects.create(user=staff, organisation=self.cardiff, role='org_admin')
        member = Member.objects.create(organisation=self.cardiff, first_name='Alexander', surname='Smith',
                                       preferred_name='Alex', pronouns='they/them')
        cls = Class.objects.create(organisation=self.cardiff, name='Juniors', is_active=True)
        ClassMember.objects.create(assigned_class=cls, member=member)
        self.client.force_login(staff)
        page = self.client.get(reverse('class_detail', kwargs={'org_slug': self.cardiff.slug, 'pk': cls.pk})).content.decode()
        self.assertTrue('Alex Smith (they/them)' in page)

    def test_join_form_takes_name_parts_and_approval_keeps_them(self):
        self.client.post(reverse('member_signup', kwargs={'org_slug': self.cardiff.slug}),
                         {'first_name': 'Alexander', 'surname': 'Smith', 'preferred_name': 'Alex', 'pronouns': 'he/him',
                          'email': 'alex@example.com'})
        application = MemberApplication.objects.get()
        self.assertEqual((application.name, application.preferred_name), ('Alexander Smith', 'Alex'))
        staff = User.objects.create_user('admin', password='pw')
        OrganisationMember.objects.create(user=staff, organisation=self.cardiff, role='org_admin')
        self.client.force_login(staff)
        self.client.post(reverse('application_approve', kwargs={'org_slug': self.cardiff.slug, 'pk': application.pk}))
        self.assertEqual(Member.objects.get().register_name, 'Alex Smith (he/him)')


class AccountHolderNameTests(NetworkTestBase):
    def test_a_one_word_club_name_takes_the_accounts_name(self):
        import importlib
        from django.apps import apps
        migration = importlib.import_module('members.migrations.0024_account_holder_names')
        self.sign_up(name='Josh Weavers')
        account = self.account()
        me = Member.objects.create(organisation=self.cardiff, name='testjweavers', account=account,
                                   account_role='holder', preferred_name='Joshy', pronouns='he/him')
        other = Member.objects.create(organisation=self.newport, name='Joshua Weavers', account=account, account_role='holder')
        migration.fill(apps, None)
        me.refresh_from_db(); other.refresh_from_db()
        self.assertEqual((me.name, me.register_name), ('Josh Weavers', 'Joshy Weavers (he/him)'))
        self.assertEqual(other.name, 'Joshua Weavers')          # already had a surname: left for the member

    def test_staff_see_an_account_holders_name_read_only(self):
        self.sign_up()
        me = Member.objects.create(organisation=self.cardiff, name='Sam Smith', account=self.account(), account_role='holder')
        staff = User.objects.create_user('coach', password='pw')
        OrganisationMember.objects.create(user=staff, organisation=self.cardiff, role='org_admin')
        self.client.force_login(staff)
        url = reverse('member_edit', kwargs={'org_slug': self.cardiff.slug, 'pk': me.pk})
        self.assertRegex(self.client.get(url).content.decode(), r'name="surname"[^>]*disabled')
        self.client.post(url, {'first_name': 'Changed', 'surname': 'Name', 'is_active': 'on',
                               'guardians-TOTAL_FORMS': '0', 'guardians-INITIAL_FORMS': '0'})
        me.refresh_from_db()
        self.assertEqual(me.name, 'Sam Smith')


class MyClubsMembershipTests(NetworkTestBase):
    def setUp(self):
        from billing.models import BillingPolicy
        self.sign_up()
        self.verify(self.account())
        self.plan = BillingPolicy.objects.create(organisation=self.cardiff, name='Adult unlimited', billing_cycle='monthly',
                                                 amount=Decimal('40.00'))
        self.me = Member.objects.create(organisation=self.cardiff, name='Sam Smith', email='sam@example.com',
                                        account=self.account(), account_role='holder', billing_policy=self.plan)
        self.kid = Member.objects.create(organisation=self.newport, name='Kid Smith', account=self.account(),
                                         account_role='dependant')

    def test_plans_show_per_membership(self):
        juniors = Class.objects.create(organisation=self.newport, name='Juniors', is_active=True)
        from billing.models import BillingPolicy
        juniors.billing_policy = BillingPolicy.objects.create(organisation=self.newport, name='Junior pay as you go',
                                                              billing_cycle='monthly', pricing_model='per_session',
                                                              per_session_rate=Decimal('6.00'))
        juniors.save()
        ClassMember.objects.create(assigned_class=juniors, member=self.kid)
        page = self.client.get(reverse('network_home')).content.decode()
        self.assertTrue('Adult unlimited · £40.00 a month' in page)
        self.assertTrue('Junior pay as you go · £6.00 a session' in page)   # from the class they're in

    def test_tell_a_club_youre_leaving_from_my_clubs(self):
        from members.models import MemberLeavingRequest
        url = reverse('network_leave', kwargs={'pk': self.kid.pk})
        self.client.post(url, {'reason': 'Moving away'})
        self.client.post(url, {'reason': 'Again'})                        # only ever one pending request
        request = MemberLeavingRequest.objects.get()
        self.assertEqual((request.member, request.reason), (self.kid, 'Moving away'))
        page = self.client.get(reverse('network_home')).content.decode()
        self.assertTrue('Leaving: you told the club on' in page)
        self.assertTrue("Tell Cardiff Judo I&#x27;m leaving" in page or "Tell Cardiff Judo I'm leaving" in page)
        stranger = Member.objects.create(organisation=self.cardiff, name='Someone Else')
        self.assertEqual(self.client.post(reverse('network_leave', kwargs={'pk': stranger.pk})).status_code, 404)


class ProgressionHistoryTests(NetworkTestBase):
    """Progression follows the member: every club's grades plus their own, by martial art; each owned by its source."""

    def setUp(self):
        from datetime import date
        from progression.models import MemberProgression, ProgressionStage, ProgressionSystem
        self.sign_up()
        self.verify(self.account())
        account = self.account()
        self.me_cardiff = Member.objects.create(organisation=self.cardiff, name='Sam Smith', email='sam@example.com',
                                                account=account, account_role='holder')
        self.me_newport = Member.objects.create(organisation=self.newport, name='Sam Smith', email='sam@example.com',
                                                account=account, account_role='holder')
        judo = ProgressionSystem.objects.create(organisation=self.cardiff, name='Kyu', discipline='judo')
        self.yellow = ProgressionStage.objects.create(system=judo, name='Yellow', order=1, colour='#ffff00')
        self.orange = ProgressionStage.objects.create(system=judo, name='Orange', order=2)
        bjj = ProgressionSystem.objects.create(organisation=self.newport, name='Adult belts', discipline='bjj')
        self.blue = ProgressionStage.objects.create(system=bjj, name='Blue', order=1)
        MemberProgression.objects.create(member=self.me_cardiff, stage=self.yellow, achieved_date=date(2024, 1, 10))
        MemberProgression.objects.create(member=self.me_cardiff, stage=self.orange, achieved_date=date(2024, 6, 1))
        MemberProgression.objects.create(member=self.me_cardiff, stage=self.yellow, achieved_date=date(2024, 9, 1))  # demoted
        MemberProgression.objects.create(member=self.me_newport, stage=self.blue, achieved_date=date(2025, 3, 1))
        self.url = reverse('network_progression')

    def test_history_by_martial_art_most_recent_first(self):
        from network import progression
        [me] = progression.people(self.account())
        self.assertEqual([g['label'] for g in me['groups']], ['Brazilian Jiu-Jitsu', 'Judo'])
        judo = me['groups'][1]['entries']
        self.assertEqual([(e['grade'], e['change'], e['awarded_by']) for e in judo],
                         [('Yellow', 'demotion', 'Cardiff Judo'), ('Orange', 'promotion', 'Cardiff Judo'),
                          ('Yellow', 'awarded', 'Cardiff Judo')])
        self.client.post(self.url, {'discipline': 'judo', 'grade': 'Green belt', 'achieved_date': '2025-11-01',
                                    'awarded_by': 'Old club'})
        [me] = progression.people(self.account())
        self.assertEqual([g['label'] for g in me['groups']], ['Judo', 'Brazilian Jiu-Jitsu'])   # judo now most recent
        page = self.client.get(self.url).content.decode()
        for text in ('Green belt', 'Old club, added by you', 'Demoted', 'Promoted', 'Newport BJJ (Adult belts)'):
            self.assertTrue(text in page, text)

    def test_members_own_what_they_add_and_cant_touch_club_grades(self):
        from network.models import PriorProgression
        self.client.post(self.url, {'discipline': 'karate', 'grade': '5th Kyu', 'achieved_date': ''})
        grade = PriorProgression.objects.get()
        self.client.post(self.url, {'pk': grade.pk, 'discipline': 'karate', 'grade': '4th Kyu'})
        grade.refresh_from_db()
        self.assertEqual(grade.grade, '4th Kyu')
        self.client.post(self.url, {'discipline': 'karate', 'grade': 'x', 'achieved_date': '2999-01-01'})
        self.assertEqual(PriorProgression.objects.count(), 1)                  # future dates refused
        other = accounts.create_account('Jo Bloggs', 'jo@example.com', 'a-long-passphrase-1')
        theirs = PriorProgression.objects.create(account=other, discipline='judo', grade='Black')
        self.assertEqual(self.client.post(self.url, {'pk': theirs.pk, 'action': 'delete'}).status_code, 404)
        self.client.post(self.url, {'pk': grade.pk, 'action': 'delete'})
        self.assertFalse(PriorProgression.objects.filter(account=self.account()).exists())

    def test_clubs_see_history_from_elsewhere_read_only(self):
        from network.models import PriorProgression
        from progression.models import MemberProgression
        PriorProgression.objects.create(account=self.account(), discipline='judo', grade='White belt', awarded_by='Old club')
        staff = User.objects.create_user('coach', password='pw')
        OrganisationMember.objects.create(user=staff, organisation=self.newport, role='org_admin')
        self.client.force_login(staff)
        page = self.client.get(reverse('member_detail', kwargs={'org_slug': self.newport.slug, 'pk': self.me_newport.pk})).content.decode()
        self.assertTrue('Progression elsewhere' in page)
        self.assertTrue('Cardiff Judo' in page and 'Orange' in page and 'added by the member' in page)
        cardiff_grade = MemberProgression.objects.filter(member=self.me_cardiff).first()
        # Newport can't remove Cardiff's grade, even through its own member's URL.
        response = self.client.post(reverse('member_progression_delete', kwargs={
            'org_slug': self.newport.slug, 'pk': self.me_newport.pk, 'prog_pk': cardiff_grade.pk}))
        self.assertEqual(response.status_code, 404)
        self.assertTrue(MemberProgression.objects.filter(pk=cardiff_grade.pk).exists())

    def test_new_accounts_are_asked_for_their_grades(self):
        self.client.logout()
        response = self.sign_up(email='new@example.com', name='New Person')
        self.assertRedirects(response, self.url + '?welcome=1', fetch_redirect_response=False)
        self.assertTrue('Already training?' in self.client.get(self.url + '?welcome=1').content.decode())

    def test_grading_systems_have_a_martial_art(self):
        from progression.models import ProgressionSystem
        staff = User.objects.create_user('admin', password='pw')
        OrganisationMember.objects.create(user=staff, organisation=self.newport, role='org_admin')
        self.client.force_login(staff)
        system = ProgressionSystem.objects.get(organisation=self.newport)
        self.client.post(reverse('progression_system_discipline', kwargs={'org_slug': self.newport.slug, 'pk': system.pk}),
                         {'discipline': 'mma'})
        system.refresh_from_db()
        self.assertEqual(system.discipline, 'mma')

    def test_migration_guesses_the_martial_art(self):
        import importlib
        from django.apps import apps
        from progression.models import ProgressionSystem
        migration = importlib.import_module('progression.migrations.0010_guess_system_disciplines')
        single = ProgressionSystem.objects.create(organisation=self.london, name='Belts')           # London: karate only
        named = ProgressionSystem.objects.create(organisation=self.newport, name='MMA levels')     # Newport: bjj + mma
        unclear = ProgressionSystem.objects.create(organisation=self.newport, name='Kids levels')
        migration.guess(apps, None)
        for s in (single, named, unclear):
            s.refresh_from_db()
        self.assertEqual((single.discipline, named.discipline, unclear.discipline), ('karate', 'mma', ''))


class SignInByEmailTests(NetworkTestBase):
    def test_a_member_account_on_a_staff_sign_in_can_sign_in_with_its_email(self):
        staff = User.objects.create_user('testj', email='Josh.Weavers@example.com', password='a-long-passphrase-1')
        accounts.account_for(staff)
        response = self.client.post(reverse('network_login'), {'username': 'josh.weavers@example.com',
                                                               'password': 'a-long-passphrase-1'})
        self.assertRedirects(response, reverse('network_home'), fetch_redirect_response=False)

    def test_wrong_password_still_refused(self):
        self.sign_up()
        self.client.logout()
        response = self.client.post(reverse('network_login'), {'username': 'sam@example.com', 'password': 'nope'})
        self.assertEqual(response.status_code, 200)
        self.assertFalse('_auth_user_id' in self.client.session)


class SignUpDetailsTests(NetworkTestBase):
    def test_sign_up_fills_in_my_details(self):
        self.sign_up(preferred_name='Sammy', pronouns='she/her', medical_info='Asthma',
                     emergency_contact_2_name='Jo', emergency_contact_2_phone='07700 1',
                     guardian_name='Mum Smith', guardian_email='mum@example.com')
        account = self.account()
        self.assertEqual((str(account.date_of_birth), account.phone, account.city, account.postcode,
                          account.emergency_contact_name, account.preferred_name, account.medical_info,
                          account.emergency_contact_2_phone, account.guardian_email),
                         ('1990-05-01', '07700 900000', 'Cardiff', 'CF10 1AA', 'Pat Contact', 'Sammy', 'Asthma',
                          '07700 1', 'mum@example.com'))

    def test_required_details_and_checks(self):
        response = self.client.post(reverse('network_signup'), {
            'first_name': 'Sam', 'surname': 'Smith', 'email': 'sam@example.com', 'password': 'a-long-passphrase-1',
            'password_confirm': 'a-long-passphrase-1', 'date_of_birth': '2999-01-01', 'emergency_contact_2_name': 'Jo'})
        form = response.context['form']
        for name in ('phone', 'address_line1', 'city', 'postcode', 'emergency_contact_name', 'emergency_contact_phone'):
            self.assertIn(name, form.errors)
        self.assertIn('be in the future', str(form.errors['date_of_birth']))
        self.assertIn('emergency_contact_2_phone', form.errors)
        for optional in ('preferred_name', 'pronouns', 'medical_info', 'guardian_name', 'address_line2', 'county'):
            self.assertNotIn(optional, form.errors)
        self.assertFalse(MemberAccount.objects.exists())
        page = response.content.decode()
        self.assertTrue('Parent or guardian' in page and 'Emergency contacts' in page and 'is-invalid' in page)


class ReviewReplyAndPromptTests(NetworkTestBase):
    def setUp(self):
        from network.models import ClubReview
        self.sign_up()
        self.verify(self.account())
        self.review = ClubReview.objects.create(account=self.account(), organisation=self.cardiff, rating=2,
                                                text='Mats were dirty.')
        self.admin = User.objects.create_user('admin', password='pw')
        OrganisationMember.objects.create(user=self.admin, organisation=self.cardiff, role='org_admin')

    def test_admins_reply_publicly_without_marking_the_review_edited(self):
        self.client.force_login(self.admin)
        url = reverse('org_reviews', kwargs={'org_slug': self.cardiff.slug})
        self.client.post(url, {'review': self.review.pk, 'reply': 'Sorry! New mats arrive Monday.', 'action': 'reply'})
        self.review.refresh_from_db()
        self.assertEqual((self.review.reply, self.review.replied_by), ('Sorry! New mats arrive Monday.', self.admin))
        self.assertFalse(self.review.edited)
        page = self.client.get(reverse('network_club', kwargs={'slug': self.cardiff.slug})).content.decode()
        self.assertTrue('Reply from Cardiff Judo' in page and 'New mats arrive Monday' in page)
        coach = User.objects.create_user('coach', password='pw')
        OrganisationMember.objects.create(user=coach, organisation=self.cardiff, role='coach')
        self.client.force_login(coach)
        self.client.post(url, {'review': self.review.pk, 'reply': 'Hijack', 'action': 'reply'})
        self.review.refresh_from_db()
        self.assertEqual(self.review.reply, 'Sorry! New mats arrive Monday.')

    def test_members_are_asked_once_after_enough_sessions(self):
        from classes.models import Attendance, Class, Session
        from network import reviews
        from network.models import ReviewPrompt
        jo = accounts.create_account('Jo Bloggs', 'jo@example.com', 'a-long-passphrase-1')
        accounts.mark_verified(jo)
        me = Member.objects.create(organisation=self.cardiff, name='Jo Bloggs', email='jo@example.com', account=jo,
                                   account_role='holder')
        cls = Class.objects.create(organisation=self.cardiff, name='Adults', is_active=True)
        for n in range(reviews.PROMPT_AFTER_SESSIONS - 1):
            Attendance.objects.create(session=Session.objects.create(assigned_class=cls, date=timezone.localdate() - timedelta(days=n + 1)),
                                      member=me, present=True)
        mail.outbox.clear()
        self.assertEqual(reviews.send_prompts(), 0)
        Attendance.objects.create(session=Session.objects.create(assigned_class=cls, date=timezone.localdate() - timedelta(days=30)),
                                  member=me, present=True)
        self.assertEqual(reviews.send_prompts(), 1)
        self.assertEqual(mail.outbox[0].to, ['jo@example.com'])
        self.assertIn('/clubs/cardiff-judo/#reviews', mail.outbox[0].body)
        self.assertEqual(reviews.send_prompts(), 0)                    # only once
        self.assertEqual(ReviewPrompt.objects.count(), 1)

    def test_clubs_can_stop_asking(self):
        from network import reviews
        self.client.force_login(self.admin)
        self.client.post(reverse('org_reviews', kwargs={'org_slug': self.cardiff.slug}), {'action': 'prompts', 'on': '0'})
        self.cardiff.refresh_from_db()
        self.assertFalse(reviews.prompts_on(self.cardiff))
