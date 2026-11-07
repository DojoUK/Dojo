"""
Shared logic for public membership applications.

Used by both the join page (/join/<org>/) and the JSON signup API
(/api/join/<org>/), so the two can never disagree about what a valid
application is or which fields a club collects.
"""
from datetime import date

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.shortcuts import get_object_or_404

from dojo.mixins import require_features
from organisations.models import Organisation

from .models import MemberApplication

# Each text field on the application, and the optional field switch that
# covers it (None: always collected).
TEXT_FIELDS = {
    'email': None,
    'phone': 'phone',
    'address_line1': 'address',
    'address_line2': 'address',
    'city': 'address',
    'county': 'address',
    'postcode': 'address',
    'guardian_name': 'guardians',
    'guardian_email': 'guardians',
    'guardian_phone': 'guardians',
    'medical_info': 'medical_info',
    'notes': None,
}
EMAIL_FIELDS = ('email', 'guardian_email')
# A drawn signature is a base64 PNG; anything far beyond this is not one.
MAX_SIGNATURE_LENGTH = 2_000_000


def signup_org(org_slug):
    """The organisation for a public signup URL; 404 unless it takes applications."""
    org = get_object_or_404(Organisation, slug=org_slug)
    require_features(org, 'applications')
    return org


def active_waivers(org):
    if not org.has_feature('documents'):
        return org.waiver_templates.none()
    return org.waiver_templates.filter(is_active=True)


def collected_fields(org):
    """{field name: bool} for the optional application fields this club collects."""
    keys = {'date_of_birth': 'date_of_birth', **TEXT_FIELDS}
    return {name: key is None or org.has_field(key) for name, key in keys.items()}


def client_ip(request):
    """
    The visitor's address, for rate limiting.

    X-Forwarded-For is only believed behind a reverse proxy
    (TRUST_PROXY_HTTPS), and then only its last entry: the address the proxy
    itself saw. Anything before it came from the client and can be made up.
    Without a proxy the header is ignored, so changing it can't dodge the limit.
    """
    if settings.TRUST_PROXY_HTTPS:
        forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[-1].strip()
        if forwarded:
            return forwarded
    return request.META.get('REMOTE_ADDR', '')


def origin_allowed(origin, org_slug):
    """
    Whether a browser on `origin` may use this club's signup API.

    SIGNUP_API_ORIGINS entries are either an address, allowed for every club
    on the instance, or "<club-slug>=<address>", allowed for that club only,
    so one club's website can't send applications to another club.
    """
    origin = origin.rstrip('/')
    for entry in settings.SIGNUP_API_ORIGINS:
        slug, _, address = entry.rpartition('=')
        if address.rstrip('/') == origin and slug.strip() in ('', org_slug):
            return True
    return False


def create_application(org, data):
    """
    Validate submitted fields and create a MemberApplication.

    `data` is any mapping of field name to value (request.POST or parsed
    JSON). Fields the club has switched off are ignored. Returns
    (application, errors); exactly one of them is empty.
    """
    def value(key):
        v = data.get(key, '')
        return v.strip() if isinstance(v, str) else ''

    collected = collected_fields(org)
    errors = []

    name = value('name')
    if not name:
        errors.append('Full name is required.')

    signature_data = value('signature_data')
    if active_waivers(org).filter(is_required=True).exists() and not signature_data:
        errors.append('Please sign the document before submitting.')
    if len(signature_data) > MAX_SIGNATURE_LENGTH:
        errors.append('Signature is too large.')

    fields = {key: value(key) if collected[key] else '' for key in TEXT_FIELDS}
    for key, current in (('name', name), *fields.items()):
        field = MemberApplication._meta.get_field(key)
        if field.max_length and len(current) > field.max_length:
            errors.append(f'{field.verbose_name.capitalize()} must be at most {field.max_length} characters.')
    for key in EMAIL_FIELDS:
        if fields[key]:
            try:
                validate_email(fields[key])
            except ValidationError:
                errors.append(f'Enter a valid {MemberApplication._meta.get_field(key).verbose_name}.')

    dob = None
    dob_raw = value('date_of_birth') if collected['date_of_birth'] else ''
    if dob_raw:
        try:
            dob = date.fromisoformat(dob_raw)
        except ValueError:
            errors.append('Enter the date of birth as YYYY-MM-DD.')

    if errors:
        return None, errors

    application = MemberApplication.objects.create(
        organisation=org,
        name=name,
        date_of_birth=dob,
        signature_data=signature_data,
        **fields,
    )
    return application, []

