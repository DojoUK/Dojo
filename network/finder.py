"""Finding clubs on the Dojo network: clubs with a location that haven't opted out, by distance and discipline."""
import math

from organisations import disciplines as discipline_list
from organisations.location import distance_miles
from organisations.models import Organisation

RADIUS_CHOICES = (5, 10, 25, 50, 100)
SORT_CHOICES = (('rating', 'Rating'), ('distance', 'Distance'), ('az', 'A–Z'), ('za', 'Z–A'))
DEFAULT_SORT = 'rating'
DEFAULT_RADIUS = 25
MAX_RESULTS = 200


def listed_clubs():
    return (Organisation.objects.filter(listed_in_finder=True, latitude__isnull=False, longitude__isnull=False)
            .exclude(address_line1=''))


def offered_disciplines():
    """(key, label) for the martial arts at least one listed club teaches, in the usual order."""
    taught = set()
    for keys in listed_clubs().values_list('disciplines', flat=True):
        taught.update(keys or [])
    return [(key, label) for key, label in discipline_list.DISCIPLINES if key in taught]


def search(lat=None, lng=None, radius=DEFAULT_RADIUS, discipline='', sort=DEFAULT_SORT):
    """
    Listed clubs, as (club, miles away) pairs. With a point, only clubs within `radius` miles of it;
    without one, every listed club, with no distance. `discipline` (a key from
    organisations/disciplines.py) keeps only clubs that teach it.

    `sort` is one of SORT_CHOICES. By rating (the default): higher rated clubs first (to the
    nearest half star, see reviews.rank), then the nearest (or by name without a point), with clubs
    that have no reviews yet after the rated ones. By distance: nearest first (by rating without a
    point, as there's no distance). Or by name, A–Z or Z–A. Each club gets `rating` (average, or
    None) and `review_count`.
    """
    from . import reviews
    clubs = listed_clubs()
    if lat is not None and lng is not None:
        # A cheap box around the point first, then the exact distance.
        lat_span = radius / 69.0
        lng_span = radius / max(1.0, 69.0 * math.cos(math.radians(lat)))
        clubs = clubs.filter(latitude__range=(lat - lat_span, lat + lat_span),
                             longitude__range=(lng - lng_span, lng + lng_span))
    results = []
    for club in clubs:
        if discipline and discipline not in (club.disciplines or []):
            continue
        miles = distance_miles(lat, lng, club.latitude, club.longitude) if lat is not None and lng is not None else None
        if miles is not None and miles > radius:
            continue
        results.append((club, miles))
    found = reviews.ratings([club.pk for club, _ in results])
    for club, _ in results:
        club.rating, club.review_count = found.get(club.pk, (None, 0))
    located = lat is not None and lng is not None
    if sort in ('az', 'za'):
        results.sort(key=lambda pair: pair[0].name.lower(), reverse=sort == 'za')
    elif sort == 'distance' and located:
        results.sort(key=lambda pair: (pair[1], pair[0].name.lower()))
    else:
        results.sort(key=lambda pair: (reviews.rank(pair[0].rating), pair[1] if located else 0, pair[0].name.lower()))
    return results[:MAX_RESULTS]


def parse_point(get):
    """(lat, lng) from query parameters, or (None, None) if they're missing or invalid."""
    try:
        lat, lng = float(get.get('lat', '')), float(get.get('lng', ''))
    except ValueError:
        return None, None
    if not (-90 <= lat <= 90 and -180 <= lng <= 180) or math.isnan(lat) or math.isnan(lng):
        return None, None
    return lat, lng


def parse_radius(get):
    try:
        radius = int(get.get('radius', DEFAULT_RADIUS))
    except ValueError:
        return DEFAULT_RADIUS
    return radius if radius in RADIUS_CHOICES else DEFAULT_RADIUS


def parse_sort(get):
    sort = get.get('sort', DEFAULT_SORT)
    return sort if sort in dict(SORT_CHOICES) else DEFAULT_SORT


def parse_discipline(get):
    key = get.get('discipline', '')
    return key if key in discipline_list.LABELS else ''
