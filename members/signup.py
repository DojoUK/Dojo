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

from .forms import extract_custom_field_values
from .models import MemberApplication

# Each text field on the application, and the optional field switch that
# covers it (None: always collected).
TEXT_FIELDS = {
    'email': None,
    'first_name': None,
    'surname': None,
    'preferred_name': None,
    'pronouns': None,
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
    'emergency_contact_name': None,
    'emergency_contact_phone': None,
    'emergency_contact_2_name': 'emergency_contact_2',
    'emergency_contact_2_phone': 'emergency_contact_2',
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


def application_custom_fields(org):
    """The club's custom fields it asks on the join form."""
    from .models import CustomField
    return CustomField.objects.filter(organisation=org, on_application=True)


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


def create_application(org, data, require_agreement=False):
    """
    Validate submitted fields and create a MemberApplication.

    `data` is any mapping of field name to value (request.POST or parsed
    JSON). Fields the club has switched off are ignored. Returns
    (application, errors); exactly one of them is empty.

    With `require_agreement` (Dojo's own join form), each required document must be ticked as
    agreed (agree_<pk>) and the signer must type their name, as well as drawing a signature. The
    signup API keeps its older contract: a signature agrees to all of the club's documents.
    """
    def value(key):
        v = data.get(key, '')
        return v.strip() if isinstance(v, str) else ''

    collected = collected_fields(org)
    errors = []

    # First name and surname; the signup API may still send one full name, which is split.
    first_name, surname = value('first_name'), value('surname')
    if not first_name and not surname and value('name'):
        from .names import split_name
        first_name, surname = split_name(value('name'))
    from .names import join_name
    name = join_name(first_name, surname)
    if not first_name:
        # The signup API's older contract sends one full name.
        errors.append('First name is required.' if 'first_name' in data else 'Full name is required.')

    signature_data = value('signature_data')
    waivers = list(active_waivers(org))
    signer_name = value('signer_name')[:255]
    if require_agreement:
        agreed = [w.pk for w in waivers if data.get(f'agree_{w.pk}')]
        for waiver in waivers:
            if waiver.is_required and waiver.pk not in agreed:
                errors.append(f'Tick to agree to {waiver.name}.')
        if agreed and not signer_name:
            errors.append('Type your full name to sign.')
        needs_signature = bool(agreed)
    else:
        agreed = [w.pk for w in waivers] if signature_data else []
        needs_signature = any(w.is_required for w in waivers)
    if needs_signature and not signature_data:
        errors.append('Please sign the document before submitting.')
    if len(signature_data) > MAX_SIGNATURE_LENGTH:
        errors.append('Signature is too large.')

    fields = {key: value(key) if collected[key] else '' for key in TEXT_FIELDS}
    fields.update(first_name=first_name, surname=surname)
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

    promo = None
    if value('promo_code'):
        from billing import promos
        promo = promos.find(org, value('promo_code')) if org.has_feature('billing') else None
        if promo is None:
            errors.append(f'We don\'t recognise the promo code {promos.normalise(value("promo_code"))}.')
        elif promos.problem(promo):
            errors.append(promos.problem(promo))

    if errors:
        return None, errors

    application = MemberApplication.objects.create(
        organisation=org,
        name=name,
        date_of_birth=dob,
        signature_data=signature_data if agreed else '',
        signer_name=(signer_name or name) if agreed else '',
        agreed_waivers=agreed,
        promo=promo,
        custom_field_values=extract_custom_field_values(org, data, application_custom_fields(org)),
        **fields,
    )
    return application, []

