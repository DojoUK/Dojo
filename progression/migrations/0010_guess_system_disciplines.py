"""
Give existing grading systems a martial art, so members' progression can be shown by discipline:

- a club that teaches one martial art: that one;
- otherwise one named in the system's name (e.g. "BJJ Adult Belts", "BJA Kyu Grades", "GKR Karate
  Grades", "Taekwondo Poom"), if it names exactly one of the club's martial arts or, failing that,
  exactly one martial art.

Anything else is left unset, for the club to choose in Settings → Progression.
"""
import re

from django.db import migrations

# Words in a system's name that point to a martial art.
NAME_HINTS = {
    'aikido': ['aikido'],
    'bjj': ['bjj', 'brazilian jiu-jitsu', 'brazilian jiu jitsu', 'ibjjf'],
    'boxing': ['boxing'],
    'jiu_jitsu': ['jujitsu', 'ju-jitsu', 'ju jitsu'],
    'judo': ['judo', 'bja', 'mon grades', 'sho awards'],
    'karate': ['karate', 'gkr', 'jka', 'wado', 'shotokan'],
    'kendo': ['kendo'],
    'kickboxing': ['kickboxing', 'kick boxing'],
    'krav_maga': ['krav maga'],
    'kung_fu': ['kung fu', 'wing chun'],
    'mma': ['mma'],
    'muay_thai': ['muay thai'],
    'taekwondo': ['taekwondo', 'tae kwon do', 'poom', 'kup'],
    'wrestling': ['wrestling'],
}


def hinted(name):
    text = name.lower()
    return {key for key, words in NAME_HINTS.items()
            if any(re.search(r'(?<![a-z])' + re.escape(word) + r'(?![a-z])', text) for word in words)}


def guess(apps, schema_editor):
    System = apps.get_model('progression', 'ProgressionSystem')
    for system in System.objects.filter(discipline='').select_related('organisation'):
        taught = list(system.organisation.disciplines or [])
        if len(taught) == 1:
            choice = taught[0]
        else:
            hints = hinted(system.name)
            at_club = hints & set(taught)
            choice = next(iter(at_club)) if len(at_club) == 1 else next(iter(hints)) if len(hints) == 1 else ''
        if choice:
            System.objects.filter(pk=system.pk).update(discipline=choice)


class Migration(migrations.Migration):

    dependencies = [
        ('progression', '0009_system_discipline'),
    ]

    operations = [migrations.RunPython(guess, migrations.RunPython.noop)]
