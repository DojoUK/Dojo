"""Club locations: required in setup, editable in Settings → Location, geocoded, and used for distances."""
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from organisations import location
from organisations.models import Organisation, OrganisationMember

# A complete location for setup forms (with coordinates, so nothing is geocoded). Other tests that
# post the setup form use this too.
SETUP_LOCATION = {
    'address_line1': '1 Dojo Way', 'city': 'Cardiff', 'postcode': 'CF10 1AA', 'country': 'United Kingdom',
    'latitude': '51.481583', 'longitude': '-3.179090', 'disciplines': ['judo'],
    'listed_in_finder_shown': '1', 'listed_in_finder': '1',
}


def setup_form(**extra):
    return {
        'org_name': 'Cardiff Judo', 'username': 'boss', 'password': 'longpassword1', 'password_confirm': 'longpassword1',
        'email_provider': 'log', **SETUP_LOCATION, **extra,
    }


class DistanceTests(TestCase):
    def test_cardiff_to_london_is_about_131_miles(self):
        self.assertAlmostEqual(location.distance_miles(51.4816, -3.1791, 51.5074, -0.1278), 131, delta=2)
        self.assertEqual(location.distance_miles(51.5, -0.1, 51.5, -0.1), 0)


@override_settings(GOOGLE_MAPS_API_KEY='')
class SetupLocationTests(TestCase):
    def test_location_is_required(self):
        data = setup_form()
        for field in ('address_line1', 'latitude', 'longitude', 'disciplines'):
            data.pop(field)
        response = self.client.post(reverse('setup'), data)
        self.assertFalse(Organisation.objects.exists())
        self.assertContains(response, 'Enter where your club trains')
        self.assertContains(response, 'Choose at least one martial art')

    def test_without_a_maps_key_coordinates_must_be_typed(self):
        data = setup_form()
        data.pop('latitude'); data.pop('longitude')
        response = self.client.post(reverse('setup'), data)
        self.assertFalse(Organisation.objects.exists())
        self.assertContains(response, 'latitude and longitude')

    def test_saves_the_location(self):
        self.client.post(reverse('setup'), setup_form(disciplines=['judo', 'bjj']))
        org = Organisation.objects.get()
        self.assertTrue(org.has_location)
        self.assertEqual((org.city, org.latitude, org.disciplines, org.listed_in_finder),
                         ('Cardiff', Decimal('51.481583'), ['bjj', 'judo'], True))
        self.assertEqual(org.discipline_labels, ['Brazilian Jiu-Jitsu', 'Judo'])

    def test_bad_coordinates_are_refused(self):
        response = self.client.post(reverse('setup'), setup_form(latitude='123', longitude='0'))
        self.assertFalse(Organisation.objects.exists())
        self.assertContains(response, 'latitude must be between -90 and 90')


@override_settings(GOOGLE_MAPS_API_KEY='test-key')
class GeocodingTests(TestCase):
    def _google(self, payload):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = __import__('json').dumps(payload).encode()
        return mock.patch('urllib.request.urlopen', return_value=response)

    def test_address_is_geocoded_when_no_pin_was_placed(self):
        data = setup_form()
        data.pop('latitude'); data.pop('longitude')
        with self._google({'status': 'OK', 'results': [{'geometry': {'location': {'lat': 51.4801234, 'lng': -3.1801234}}}]}) as call:
            self.client.post(reverse('setup'), data)
        self.assertIn('1+Dojo+Way%2C+Cardiff%2C+CF10+1AA', call.call_args[0][0])
        org = Organisation.objects.get()
        self.assertEqual((org.latitude, org.longitude), (Decimal('51.480123'), Decimal('-3.180123')))

    def test_unknown_address_asks_for_the_pin(self):
        data = setup_form()
        data.pop('latitude'); data.pop('longitude')
        with self._google({'status': 'ZERO_RESULTS', 'results': []}):
            response = self.client.post(reverse('setup'), data)
        self.assertFalse(Organisation.objects.exists())
        self.assertContains(response, 'couldn&#x27;t find that address on the map')

    def test_setup_page_shows_the_map(self):
        self.assertContains(self.client.get(reverse('setup')), 'maps.googleapis.com/maps/api/js?key=test-key')


class AddingClubsTests(TestCase):
    """Once one club exists, more can join the installation (the Dojo network) through setup."""

    def setUp(self):
        self.client.post(reverse('setup'), setup_form())
        self.client.logout()

    def test_closed_to_the_public_by_default(self):
        self.assertRedirects(self.client.get(reverse('setup')), reverse('login'), fetch_redirect_response=False)
        self.client.post(reverse('setup'), setup_form(org_name='Another Club', username='other'))
        self.assertEqual(Organisation.objects.count(), 1)

    @override_settings(DOJO_OPEN_CLUB_REGISTRATION=True)
    def test_open_registration_adds_a_club_with_its_own_admin(self):
        self.assertContains(self.client.get(reverse('setup')), 'Add a club to the Dojo network')
        self.client.post(reverse('setup'), setup_form(org_name='Cardiff Judo', username='second'))
        second = Organisation.objects.exclude(slug='cardiff-judo').get()
        self.assertEqual(second.slug, 'cardiff-judo-2')     # same name, its own address
        admin = User.objects.get(username='second')
        self.assertFalse(admin.is_superuser)                   # only the first club's admin runs the site
        self.assertTrue(OrganisationMember.objects.filter(user=admin, organisation=second, role='org_admin').exists())

    def test_a_superuser_can_add_a_club_and_stays_signed_in(self):
        first = Organisation.objects.get()
        first.settings = {**first.settings, 'two_factor': Organisation.TWO_FACTOR_OPTIONAL}   # not testing 2FA here
        first.save()
        self.client.force_login(User.objects.get(username='boss'))
        response = self.client.post(reverse('setup'), setup_form(org_name='Swansea BJJ', username='swansea'))
        self.assertEqual(Organisation.objects.count(), 2)
        self.assertEqual(int(self.client.session['_auth_user_id']), User.objects.get(username='boss').pk)
        self.assertRedirects(response, reverse('root'), fetch_redirect_response=False)

    @override_settings(DOJO_OPEN_CLUB_REGISTRATION=True)
    def test_no_demo_seeding_once_clubs_exist(self):
        self.assertEqual(self.client.post(reverse('setup'), {'mode': 'demo'}).status_code, 403)


class LocationSettingsTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name='Test Club', slug='test-club')
        self.admin = User.objects.create_user('admin', password='pw')
        OrganisationMember.objects.create(user=self.admin, organisation=self.org, role='org_admin')
        self.client.force_login(self.admin)
        self.url = reverse('org_settings', kwargs={'org_slug': self.org.slug})

    def test_dashboard_asks_for_a_location_until_there_is_one(self):
        dashboard = reverse('org_dashboard', kwargs={'org_slug': self.org.slug})
        self.assertContains(self.client.get(dashboard), "Add your club's location.")
        self.client.post(self.url, {'action': 'location', **SETUP_LOCATION})
        self.assertNotContains(self.client.get(dashboard), "Add your club's location.")

    def test_save_and_hide_from_the_finder(self):
        self.client.post(self.url, {'action': 'location', **{**SETUP_LOCATION, 'listed_in_finder': ''}})
        self.org.refresh_from_db()
        self.assertTrue(self.org.has_location)
        self.assertFalse(self.org.listed_in_finder)
        page = self.client.get(self.url).content.decode()
        self.assertIn('value="1 Dojo Way"', page)
        self.assertIn('id="disc-judo" autocomplete="off" checked', page)

    def test_errors_change_nothing(self):
        self.client.post(self.url, {'action': 'location', **{**SETUP_LOCATION, 'postcode': ''}})
        self.org.refresh_from_db()
        self.assertFalse(self.org.has_location)
