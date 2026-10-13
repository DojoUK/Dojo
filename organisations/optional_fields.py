"""
Built-in member and staff fields a club can switch on or off.

Chosen in the setup wizard and changed later under Settings → Fields. A
switched-off field is hidden from forms, pages, the join page, registers,
warnings and CSV import/export, but its stored values are kept, so
switching it back on brings them back. (The member's own data export
always includes everything held about them.)

Choices are stored per organisation in `Organisation.optional_fields` as
{key: bool}; a key with no stored choice falls back to the default here.
Fields with `renamable` set take a club-chosen label, stored in
`Organisation.settings['<key>_label']`.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class OptionalField:
    key: str
    label: str
    group: str
    help: str
    default: bool
    renamable: bool = False
    # Model fields it covers: on members.Member for member fields, on
    # organisations.OrganisationMember for staff fields.
    model_fields: tuple = ()


MEMBER = 'member'
STAFF = 'staff'

OPTIONAL_FIELDS = (
    OptionalField('date_of_birth', 'Date of birth', MEMBER,
                  'On member records and the join page.', default=True,
                  model_fields=('date_of_birth',)),
    OptionalField('phone', 'Phone', MEMBER,
                  'On member records, the member list and the join page.', default=True,
                  model_fields=('phone',)),
    OptionalField('address', 'Address', MEMBER,
                  'On member records and the join page.', default=True,
                  model_fields=('address_line1', 'address_line2')),
    OptionalField('emergency_contact_2', 'Second emergency contact', MEMBER,
                  'A second name and number alongside the first emergency contact.', default=True,
                  model_fields=('emergency_contact_2_name', 'emergency_contact_2_phone')),
    OptionalField('medical_info', 'Medical information', MEMBER,
                  'Conditions and allergies, shown to coaches on registers.', default=True,
                  model_fields=('medical_info',)),
    OptionalField('joined_date', 'Joined date', MEMBER,
                  'When the member joined the club.', default=True,
                  model_fields=('joined_date',)),
    OptionalField('guardians', 'Guardians', MEMBER,
                  'Parent or guardian details for junior members.', default=True),
    OptionalField('licence', 'Licence', MEMBER,
                  'A governing-body licence number and expiry, with expiry warnings.',
                  default=False, renamable=True, model_fields=('licence_number', 'licence_expiry')),
    OptionalField('staff_emergency_contact', 'Emergency contact', STAFF,
                  'An emergency contact name and number for each staff member.', default=True,
                  model_fields=('emergency_contact_name', 'emergency_contact_phone')),
    OptionalField('staff_emergency_contact_2', 'Second emergency contact', STAFF,
                  'A second name and number for each staff member.', default=True,
                  model_fields=('emergency_contact_2_name', 'emergency_contact_2_phone')),
    OptionalField('dbs', 'DBS check', STAFF,
                  'DBS number and expiry, with expiry warnings.', default=False,
                  model_fields=('dbs_number', 'dbs_expiry')),
    OptionalField('coaching_licence', 'Coaching licence', STAFF,
                  'A coaching licence number and expiry, with expiry warnings.',
                  default=False, renamable=True,
                  model_fields=('coaching_licence', 'coaching_licence_expiry')),
)

OPTIONAL_FIELDS_BY_KEY = {f.key: f for f in OPTIONAL_FIELDS}

LABEL_MAX_LENGTH = 50


def disabled_model_fields(org, group):
    """Model field names covered by this group's switched-off optional fields."""
    return {
        name
        for f in OPTIONAL_FIELDS
        if f.group == group and not org.has_field(f.key)
        for name in f.model_fields
    }


def enabled_model_fields(org, group, keys=None):
    """Model field names covered by this group's switched-on optional fields (optionally only `keys`)."""
    return [
        name
        for f in OPTIONAL_FIELDS
        if f.group == group and org.has_field(f.key) and (keys is None or f.key in keys)
        for name in f.model_fields
    ]


def effective_fields(stored):
    """The set of optional field keys switched on, given the stored choices."""
    stored = stored or {}
    return {f.key for f in OPTIONAL_FIELDS if stored.get(f.key, f.default)}


def choices_from_keys(keys):
    keys = set(keys)
    return {f.key: f.key in keys for f in OPTIONAL_FIELDS}


def field_rows(enabled, settings=None, group=None):
    """Rows for the field checkboxes on the setup wizard and settings page."""
    settings = settings or {}
    return [
        {
            'key': f.key,
            'label': f.label,
            'help': f.help,
            'group': f.group,
            'renamable': f.renamable,
            'custom_label': settings.get(f'{f.key}_label', ''),
            'enabled': f.key in enabled,
        }
        for f in OPTIONAL_FIELDS
        if group is None or f.group == group
    ]


def field_groups(enabled, settings=None):
    """(heading, rows) pairs for the field checkboxes partial."""
    return [
        ('Member fields', field_rows(enabled, settings, MEMBER)),
        ('Staff fields', field_rows(enabled, settings, STAFF)),
    ]


def labels_from_post(post):
    """{'<key>_label': value} for each renamable field, trimmed to LABEL_MAX_LENGTH."""
    return {
        f'{f.key}_label': post.get(f'{f.key}_label', '').strip()[:LABEL_MAX_LENGTH]
        for f in OPTIONAL_FIELDS
        if f.renamable
    }
