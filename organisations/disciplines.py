"""
The martial arts a club can teach, used to describe clubs and to filter the Dojo club finder.

Stored on Organisation.disciplines as a list of keys, e.g. ['judo', 'bjj'].
"""

DISCIPLINES = [
    ('aikido', 'Aikido'),
    ('bjj', 'Brazilian Jiu-Jitsu'),
    ('boxing', 'Boxing'),
    ('jiu_jitsu', 'Jiu-Jitsu'),
    ('judo', 'Judo'),
    ('karate', 'Karate'),
    ('kendo', 'Kendo'),
    ('kickboxing', 'Kickboxing'),
    ('krav_maga', 'Krav Maga'),
    ('kung_fu', 'Kung Fu'),
    ('mma', 'MMA'),
    ('muay_thai', 'Muay Thai'),
    ('taekwondo', 'Taekwondo'),
    ('wrestling', 'Wrestling'),
    ('other', 'Other'),
]
LABELS = dict(DISCIPLINES)

# The discipline each starter template (setup's "Club type") is for, so setup can pre-tick it.
FROM_STARTER_TEMPLATE = {'bjj': 'bjj', 'judo-bja': 'judo', 'karate-gkr': 'karate', 'taekwondo-bt': 'taekwondo'}


def clean(keys):
    """Known discipline keys from `keys`, in the standard order, without repeats."""
    wanted = set(keys or [])
    return [key for key, _ in DISCIPLINES if key in wanted]


def labels(keys):
    return [LABELS[key] for key in clean(keys)]
