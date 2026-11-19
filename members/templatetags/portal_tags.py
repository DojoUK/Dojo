from django import template
from django.urls import NoReverseMatch, reverse

from members import family

register = template.Library()


@register.simple_tag(takes_context=True)
def portal_family(context, member):
    """
    The people to offer in the portal's switcher: [{'member', 'url', 'current'}], empty unless the member
    is in a family with someone else. Each link opens the same page for that person where it can.
    """
    people = family.family_members(member)
    if len(people) < 2:
        return []
    request = context.get('request')
    match = getattr(request, 'resolver_match', None)
    rows = []
    for person in people:
        url = reverse('member_portal', kwargs={'token': person.token})
        if match is not None and match.url_name and set(match.kwargs) == {'token'}:
            try:
                url = reverse(match.url_name, kwargs={'token': person.token})
            except NoReverseMatch:
                pass
        rows.append({'member': person, 'url': url, 'current': person.pk == member.pk})
    return rows
