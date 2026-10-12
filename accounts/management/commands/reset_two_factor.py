from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError

from accounts.models import remove_two_factor


class Command(BaseCommand):
    help = ('Turn off two-step sign-in for one person, e.g. an admin who has lost their phone '
            'and recovery codes. If their club requires it, they set it up again at next sign-in.')

    def add_arguments(self, parser):
        parser.add_argument('username')

    def handle(self, *args, **options):
        try:
            user = User.objects.get(username=options['username'])
        except User.DoesNotExist:
            raise CommandError(f"No user called {options['username']!r}.") from None
        remove_two_factor(user)
        self.stdout.write(self.style.SUCCESS(f'Two-step sign-in reset for {user.username}.'))
