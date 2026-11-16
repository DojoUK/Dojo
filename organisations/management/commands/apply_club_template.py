from django.core.management.base import BaseCommand, CommandError

from organisations import starter
from organisations.models import Organisation


class Command(BaseCommand):
    help = 'Add a starter template (e.g. bjj) to a club: its progression systems, grades and syllabus checklists. Safe to run twice.'

    def add_arguments(self, parser):
        parser.add_argument('template', nargs='?', help='Template key, e.g. bjj. Omit to list the templates.')
        parser.add_argument('--org', help='The club\'s slug. Optional if there is only one club.')

    def handle(self, *args, template=None, org=None, **options):
        if not template:
            for t in starter.available():
                self.stdout.write(f"{t['key']}: {t['name']} - {t['description']}")
            return
        try:
            starter.load(template)
        except ValueError as e:
            raise CommandError(str(e))
        orgs = Organisation.objects.filter(slug=org) if org else Organisation.objects.all()
        if orgs.count() != 1:
            raise CommandError('Say which club with --org <slug>.' if not org else f'No club with the slug "{org}".')
        club = orgs.get()
        created = starter.apply_template(club, template)
        self.stdout.write(self.style.SUCCESS(
            f'{club.name}: added {created["systems"]} progression system(s), {created["stages"]} grade(s), '
            f'{created["sections"]} syllabus section(s) and {created["items"]} checklist item(s).'
        ))
