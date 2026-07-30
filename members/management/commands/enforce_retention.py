"""
Enforce the 3-year post-archive data retention policy: anonymises archived
members past the cutoff unless an admin has recorded a reason to keep them
(retention_notes) or already erased them.

Run with: python manage.py enforce_retention
Add --dry-run to list what would be anonymised without changing anything.
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from members.models import Member

RETENTION_PERIOD = timedelta(days=3 * 365)


class Command(BaseCommand):
    help = 'Anonymise archived members past the 3-year retention cutoff'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='List candidates without changing anything')

    def handle(self, *args, **options):
        cutoff = timezone.now() - RETENTION_PERIOD
        candidates = Member.objects.filter(
            is_active=False,
            archived_at__isnull=False,
            archived_at__lt=cutoff,
            anonymised_at__isnull=True,
            retention_notes='',
        )

        if not candidates.exists():
            self.stdout.write('No members past the retention cutoff without a retention override.')
            return

        for member in candidates:
            label = f'{member.name} (#{member.pk}, {member.organisation}) — archived {member.archived_at:%d %b %Y}'
            if options['dry_run']:
                self.stdout.write(f'Would anonymise: {label}')
            else:
                member.anonymise()
                self.stdout.write(self.style.SUCCESS(f'Anonymised: {label}'))

        if options['dry_run']:
            self.stdout.write(self.style.WARNING(f'{candidates.count()} member(s) would be anonymised. Re-run without --dry-run to apply.'))
        else:
            self.stdout.write(self.style.SUCCESS(f'Done — {candidates.count()} member(s) anonymised.'))
