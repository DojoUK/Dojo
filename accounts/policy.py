from organisations.models import Organisation

# Session key holding the pk of the authenticator the person proved this session.
# Removing or replacing the authenticator makes it stale, so they're asked again.
VERIFIED_SESSION_KEY = 'two_factor_verified'


def two_factor_required(user):
    """Whether any club `user` works for requires them to use two-step sign-in."""
    for membership in user.organisation_memberships.select_related('organisation'):
        if membership.organisation.requires_two_factor(membership):
            return True
    if user.is_superuser:
        # Superusers can reach every club, so they follow the strictest admin rule.
        return any(org.two_factor_policy != Organisation.TWO_FACTOR_OPTIONAL
                   for org in Organisation.objects.only('settings'))
    return False


def device_for(user):
    from .models import TwoFactorDevice
    return TwoFactorDevice.objects.filter(user=user).first()


def is_verified(request, device):
    return device is not None and request.session.get(VERIFIED_SESSION_KEY) == device.pk


def mark_verified(request, device):
    request.session[VERIFIED_SESSION_KEY] = device.pk
