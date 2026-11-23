"""
Where a club trains: reading the location form, finding coordinates for an address (Google's
Geocoding API, with GOOGLE_MAPS_API_KEY), and distances for the Dojo club finder.

The location form normally sends coordinates itself: with a Google Maps key, the address box
suggests addresses and a map pin can be dragged into place, filling hidden latitude/longitude
fields. If they're missing, the server geocodes the address. Without a key, coordinates are
typed in.
"""
import json
import math
import urllib.parse
import urllib.request
from decimal import Decimal, InvalidOperation

from django.conf import settings

from . import disciplines

ADDRESS_FIELDS = ('address_line1', 'address_line2', 'city', 'postcode', 'country')
EARTH_RADIUS_MILES = 3958.8


def maps_key():
    return getattr(settings, 'GOOGLE_MAPS_API_KEY', '') or ''


def geocode(address):
    """(latitude, longitude) for an address, or None if Google can't place it (or there's no key)."""
    key = maps_key()
    if not key or not address.strip():
        return None
    query = urllib.parse.urlencode({'address': address, 'key': key})
    try:
        with urllib.request.urlopen(f'https://maps.googleapis.com/maps/api/geocode/json?{query}', timeout=6) as r:
            data = json.load(r)
    except (OSError, ValueError):
        return None
    if data.get('status') != 'OK' or not data.get('results'):
        return None
    point = data['results'][0]['geometry']['location']
    return Decimal(str(round(point['lat'], 6))), Decimal(str(round(point['lng'], 6)))


def distance_miles(lat1, lng1, lat2, lng2):
    """Straight-line (great-circle) distance between two points, in miles."""
    p1, p2 = math.radians(float(lat1)), math.radians(float(lat2))
    dp, dl = p2 - p1, math.radians(float(lng2) - float(lng1))
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * math.asin(math.sqrt(a))


def _coordinate(raw, limit):
    try:
        value = Decimal(str(raw).strip())
    except (InvalidOperation, ValueError):
        return None
    if not value.is_finite() or abs(value) > limit:
        return None
    return value.quantize(Decimal('0.000001'))


def address_text(values):
    return ', '.join(v for v in (values.get(f, '').strip() for f in ADDRESS_FIELDS) if v)


def from_post(post, listed_default=False):
    """
    The location part of a form: (values, errors). Everything is required except the second
    address line; the club must teach at least one discipline so the finder can filter on it.
    """
    values = {f: post.get(f, '').strip()[:255] for f in ADDRESS_FIELDS}
    values['country'] = values['country'] or 'United Kingdom'
    errors = []
    if not values['address_line1'] or not values['city'] or not values['postcode']:
        errors.append('Enter where your club trains: the first line of the address, the town or city, and the postcode.')
    values['disciplines'] = disciplines.clean(post.getlist('disciplines'))
    if not values['disciplines']:
        errors.append('Choose at least one martial art your club teaches, so members can find you by it.')
    values['listed_in_finder'] = post.get('listed_in_finder') == '1' if 'listed_in_finder_shown' in post else listed_default

    lat_raw, lng_raw = post.get('latitude', '').strip(), post.get('longitude', '').strip()
    latitude, longitude = _coordinate(lat_raw, 90), _coordinate(lng_raw, 180)
    if (lat_raw or lng_raw) and (latitude is None or longitude is None):
        errors.append('The map position isn\'t valid: latitude must be between -90 and 90, longitude between -180 and 180.')
    elif latitude is None and not errors:
        found = geocode(address_text(values))
        if found:
            latitude, longitude = found
        else:
            errors.append('We couldn\'t find that address on the map. Check it, or place the pin on the map yourself.'
                          if maps_key() else
                          'Enter your club\'s latitude and longitude (on Google Maps, right-click your club and click '
                          'the numbers at the top of the menu to copy them).')
    values['latitude'], values['longitude'] = latitude, longitude
    return values, errors


def apply(org, values):
    for field, value in values.items():
        setattr(org, field, value)
