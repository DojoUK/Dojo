"""
Starter templates: a ready-made set of progression systems (and anything else a template adds)
for a new club, chosen by sport at setup, from Settings or with `manage.py apply_club_template`.

Each template is a JSON file in organisations/starter_templates/. Applying one never deletes or
changes anything: systems and stages that already exist (matched by name) are left alone, so it is
safe to apply twice and to apply to a club that already has data.
"""
import json
from pathlib import Path

from django.db import transaction

TEMPLATE_DIR = Path(__file__).resolve().parent / 'starter_templates'


def available():
    """[{'key', 'name', 'description'}] for every template, by name."""
    found = []
    for path in sorted(TEMPLATE_DIR.glob('*.json')):
        data = json.loads(path.read_text(encoding='utf-8'))
        found.append({'key': data['key'], 'name': data['name'], 'description': data.get('description', '')})
    return sorted(found, key=lambda t: t['name'])


def load(key):
    path = TEMPLATE_DIR / f'{key}.json'
    if not key.replace('-', '_').isidentifier() or not path.is_file():
        raise ValueError(f'No starter template called "{key}".')
    return json.loads(path.read_text(encoding='utf-8'))


@transaction.atomic
def apply_template(org, key):
    """
    Add the template's progression systems and syllabus checklists to the club.
    Returns {'systems', 'stages', 'sections', 'items'}: how many of each were new.
    """
    from progression.models import (
        ProgressionStage, ProgressionSystem, SyllabusItem, SyllabusSection, SyllabusSubsection,
    )

    data = load(key)
    created = {'systems': 0, 'stages': 0, 'sections': 0, 'items': 0}

    wanted = data.get('enable_features', [])
    if wanted:
        features = dict(org.features or {})
        for feature in wanted:
            features[feature] = True
        org.set_features(features)
        org.save(update_fields=['features'])

    # Syllabus checklists first, so grades can point at them.
    sections = {}
    next_section = SyllabusSection.objects.filter(organisation=org).count()
    for section_data in data.get('syllabus_sections', []):
        section, section_new = SyllabusSection.objects.get_or_create(
            organisation=org, name=section_data['name'],
            defaults={'content': section_data.get('content', ''), 'order': next_section},
        )
        sections[section.name] = section
        if section_new:
            created['sections'] += 1
            next_section += 1
        item_order = 0
        for sub_order, sub_data in enumerate(section_data.get('subsections', [])):
            subsection, _ = SyllabusSubsection.objects.get_or_create(
                section=section, name=sub_data['name'], defaults={'order': sub_order},
            )
            for item_data in sub_data['items']:
                _, item_new = SyllabusItem.objects.get_or_create(
                    section=section, subsection=subsection, name=item_data['name'],
                    defaults={'description': item_data.get('description', ''), 'link': item_data.get('link', ''),
                              'order': item_order},
                )
                item_order += 1
                if item_new:
                    created['items'] += 1

    next_order = ProgressionSystem.objects.filter(organisation=org).count()
    for system_data in data.get('progression_systems', []):
        system, system_new = ProgressionSystem.objects.get_or_create(
            organisation=org, name=system_data['name'],
            defaults={
                'order': next_order,
                'uses_gradings': system_data.get('uses_gradings', False),
                'assign_to_new_members': system_data.get('assign_to_new_members', False),
            },
        )
        if system_new:
            created['systems'] += 1
            next_order += 1
        first_is_default = system_data.get('first_stage_is_default', True)
        for order, stage_data in enumerate(system_data['stages']):
            stage, stage_new = ProgressionStage.objects.get_or_create(
                system=system, name=stage_data['name'],
                defaults={'order': order, 'colour': stage_data.get('colour', ''), 'is_default': order == 0 and first_is_default and system_new},
            )
            if stage_new:
                created['stages'] += 1
            wanted_section = sections.get(stage_data.get('syllabus', ''))
            if wanted_section and stage.syllabus_section_id is None:
                stage.syllabus_section = wanted_section
                stage.save(update_fields=['syllabus_section'])
    return created
