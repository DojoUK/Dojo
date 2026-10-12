from django import template

from dojo.mixins import has_capability

register = template.Library()


@register.simple_tag(takes_context=True)
def can(context, capability):
    """
    {% can 'manage_billing' as can_manage_billing %}

    Mirrors the view-side checks so templates only show links and controls
    the current user can actually use. Superusers have no org_membership and
    hold every capability; anyone else without a membership holds none.
    """
    request = context.get('request')
    membership = context.get('org_membership')
    if membership is None:
        return bool(request and request.user.is_superuser)
    return has_capability(membership, capability)
