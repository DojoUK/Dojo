"""
Optional features a club can switch on or off.

Chosen in the setup wizard and changed later under Settings → Features.
A switched-off feature is hidden from the app and its URLs return 404, but
its data is kept, so switching it back on restores everything.

Choices are stored per organisation in `Organisation.features` as
{key: bool}. A key with no stored choice falls back to the feature's
default here.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Feature:
    key: str
    label: str
    help: str
    default: bool
    requires: tuple = ()


FEATURES = (
    Feature(
        'grades', 'Grades',
        'Progression systems such as belts or grades, shown on member pages and in the member portal.',
        default=False,
    ),
    Feature(
        'gradings', 'Gradings',
        'Grading events where members are promoted, run as one session or split into groups.',
        default=False, requires=('grades',),
    ),
    Feature(
        'syllabus', 'Syllabus',
        "A technique library and syllabuses for each grade, shown on each member's page.",
        default=False, requires=('grades',),
    ),
    Feature(
        'billing', 'Billing',
        'Invoices, billing policies, terms, discounts, overdue reminders and the finance report.',
        default=True,
    ),
    Feature(
        'inventory', 'Inventory',
        'Products and stock, sold as lines on invoices.',
        default=False, requires=('billing',),
    ),
    Feature(
        'documents', 'Waivers & documents',
        'Waiver templates for members to sign, signed waivers, and documents on member records.',
        default=True,
    ),
    Feature(
        'applications', 'Online applications',
        'A public join page where people apply to become members, and a queue to approve them.',
        default=True,
    ),
    Feature(
        'announcements', 'Announcements',
        'Emailing all members or a class at once.',
        default=True,
    ),
    Feature(
        'member_portal', 'Member portal',
        'A private link for each member to view their details, classes and invoices.',
        default=True,
    ),
    Feature(
        'waiting_lists', 'Class capacity & waiting lists',
        'A maximum size for each class, with a waiting list once it is full.',
        default=True,
    ),
)

FEATURES_BY_KEY = {f.key: f for f in FEATURES}


# Card payments used to be a feature switch here ('online_payments'). They're now
# set up in Settings → Payments, which is the one place that turns them on.

def default_enabled(feature):
    return feature.default


def effective_features(stored):
    """
    The set of feature keys switched on, given the stored choices. A feature
    whose requirements are off counts as off, whatever its own choice.
    """
    stored = stored or {}
    chosen = {f.key for f in FEATURES if stored.get(f.key, default_enabled(f))}
    enabled = set()
    for feature in FEATURES:
        if feature.key in chosen and all(r in chosen for r in feature.requires):
            enabled.add(feature.key)
    return enabled


def choices_from_keys(keys):
    """A full {key: bool} choice dict from the keys ticked on a form."""
    keys = set(keys)
    return {f.key: f.key in keys for f in FEATURES}


def feature_rows(enabled):
    """Rows for the feature checkboxes on the setup wizard and settings page."""
    return [
        {
            'key': f.key,
            'label': f.label,
            'help': f.help,
            'requires': ' '.join(f.requires),
            'requires_labels': [FEATURES_BY_KEY[r].label for r in f.requires],
            'enabled': f.key in enabled,
        }
        for f in FEATURES
    ]
