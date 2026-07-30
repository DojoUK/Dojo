"""
Auto-rotate member portal tokens older than the retention window, so a leaked
or forwarded link can't grant standing access indefinitely. The member (or
their guardian) is emailed the new link — the old one stops working the
moment this runs.

Run with: python manage.py rotate_stale_portal_tokens
Add --dry-run to list what would be rotated without changing anything.
Add --days N to override the default 180-day window.
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from members.models import Member, generate_token

DEFAULT_TOKEN_LIFETIME_DAYS = 180


class Command(BaseCommand):
    help = 'Rotate portal tokens older than the retention window and email members the new link'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='List candidates without changing anything')
        parser.add_argument('--days', type=int, default=DEFAULT_TOKEN_LIFETIME_DAYS, help='Token lifetime in days (default: 180)')

    def handle(self, *args, **options):
        from members import emails

        cutoff = timezone.now() - timedelta(days=options['days'])
        candidates = Member.objects.filter(is_active=True, token_created_at__lt=cutoff)

        if not candidates.exists():
            self.stdout.write('No portal tokens past the rotation window.')
            return

        count = 0
        for member in candidates:
            label = f'{member.name} (#{member.pk}, {member.organisation}) — token issued {member.token_created_at:%d %b %Y}'
            if options['dry_run']:
                self.stdout.write(f'Would rotate: {label}')
                continue

            member.token = generate_token()
            member.token_created_at = timezone.now()
            member.save(update_fields=['token', 'token_created_at'])
            count += 1

            try:
                ok, result = emails.send_portal_link_refreshed_email(member)
                if ok:
                    self.stdout.write(self.style.SUCCESS(f'Rotated and emailed {result}: {label}'))
                else:
                    self.stdout.write(self.style.WARNING(f'Rotated but could not email ({result}): {label}'))
            except Exception as e:
                self.stdout.write(self.style.WARNING(f'Rotated but email failed ({e}): {label}'))

        if options['dry_run']:
            self.stdout.write(self.style.WARNING(f'{candidates.count()} member(s) would be rotated. Re-run without --dry-run to apply.'))
        else:
            self.stdout.write(self.style.SUCCESS(f'Done — {count} member(s) rotated.'))
