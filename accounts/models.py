import secrets
from datetime import timedelta

from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone
from django.utils.crypto import salted_hmac

from . import totp

RECOVERY_CODE_COUNT = 10
# No 0/O, 1/I/L: recovery codes get read off paper.
RECOVERY_ALPHABET = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789'
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_MINUTES = 5


def normalise_recovery_code(code):
    return ''.join(ch for ch in str(code).upper() if ch.isalnum())


def hash_recovery_code(code):
    # Keyed with SECRET_KEY, so a copy of the database alone can't be used to guess codes.
    return salted_hmac('dojo.accounts.recovery_code', normalise_recovery_code(code)).hexdigest()


class TwoFactorDevice(models.Model):
    """A member of staff's authenticator app. One per person; removing it switches two-step sign-in off."""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='two_factor')
    secret = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    # The most recent code's time step: a code can only be used once.
    last_used_step = models.BigIntegerField(default=0)
    failed_attempts = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f'Authenticator for {self.user}'

    @property
    def is_locked(self):
        return bool(self.locked_until and self.locked_until > timezone.now())

    def verify(self, code):
        """Check a code from the app or a recovery code. Returns 'app', 'recovery' or None."""
        if self.is_locked:
            return None
        step = totp.matching_step(self.secret, code, after_step=self.last_used_step)
        if step is not None:
            self.last_used_step = step
            method = 'app'
        elif self._use_recovery_code(code):
            method = 'recovery'
        else:
            self.failed_attempts += 1
            if self.failed_attempts >= MAX_FAILED_ATTEMPTS:
                self.failed_attempts = 0
                self.locked_until = timezone.now() + timedelta(minutes=LOCKOUT_MINUTES)
            self.save(update_fields=['failed_attempts', 'locked_until'])
            return None
        self.failed_attempts = 0
        self.locked_until = None
        self.last_used_at = timezone.now()
        self.save(update_fields=['last_used_step', 'failed_attempts', 'locked_until', 'last_used_at'])
        return method

    def _use_recovery_code(self, code):
        if len(normalise_recovery_code(code)) != 10:
            return False
        return bool(
            self.user.recovery_codes.filter(code_hash=hash_recovery_code(code), used_at__isnull=True)
            .update(used_at=timezone.now())
        )

    def new_recovery_codes(self):
        """Replace the person's recovery codes. Returns the new codes; only their hashes are kept."""
        codes = [
            ''.join(secrets.choice(RECOVERY_ALPHABET) for _ in range(10))
            for _ in range(RECOVERY_CODE_COUNT)
        ]
        self.user.recovery_codes.all().delete()
        RecoveryCode.objects.bulk_create(
            RecoveryCode(user=self.user, code_hash=hash_recovery_code(code)) for code in codes
        )
        return [f'{code[:5]}-{code[5:]}' for code in codes]

    @property
    def recovery_codes_left(self):
        return self.user.recovery_codes.filter(used_at__isnull=True).count()


class RecoveryCode(models.Model):
    """A single-use code for signing in without the authenticator app."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='recovery_codes')
    code_hash = models.CharField(max_length=128)
    used_at = models.DateTimeField(null=True, blank=True)


def remove_two_factor(user):
    """Switch two-step sign-in off for `user`: their app and recovery codes are forgotten."""
    TwoFactorDevice.objects.filter(user=user).delete()
    RecoveryCode.objects.filter(user=user).delete()
