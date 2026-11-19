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
        rows.append({'member': person, 'url': url, 'current': person.pk == member.pk,
                     'short_name': (person.name.split() or [person.name])[0]})   # first name, for phones
    return rows


# (key matching the view's `active`, url name, label, icon, icon when selected)
_SECTIONS = [
    ('dashboard', 'member_portal', 'Overview', 'house', 'house-fill'),
    ('bookings', 'portal_bookings', 'Book', 'calendar2-check', 'calendar2-check-fill'),
    ('classes', 'portal_classes', 'Classes', 'people', 'people-fill'),
    ('syllabus', 'portal_syllabus', 'Syllabus', 'award', 'award-fill'),
    ('payments', 'portal_payments', 'Payments', 'credit-card', 'credit-card-fill'),
    ('plans', 'portal_plans', 'Plan', 'bookmark-star', 'bookmark-star-fill'),
    ('details', 'portal_my_details', 'My details', 'person', 'person-fill'),
    ('conduct', 'portal_code_of_conduct', 'Code of Conduct', 'shield-check', 'shield-fill-check'),
]

_PAGE_TITLES = {
    'member_portal': 'Overview',
    'portal_my_details': 'My details',
    'portal_syllabus': 'Syllabus',
    'portal_classes': 'My classes',
    'portal_bookings': 'Book a class',
    'portal_plans': 'Plan',
    'portal_payments': 'Payments',
    'portal_code_of_conduct': 'Code of Conduct',
    'portal_my_data': 'My data',
}


@register.simple_tag(takes_context=True)
def portal_sections(context, member, active=''):
    """
    The portal's sections this club has switched on, in order:
    {'nav': [every section], 'tabs': [up to four for the phone tab bar], 'more_active': bool}.
    Each section is {'key', 'label', 'icon', 'url', 'active'}; 'icon' is already the
    filled version for the section you're on.
    """
    org = member.organisation
    shown = {
        'bookings': org.has_bookable_classes,
        'syllabus': 'grades' in org.enabled_features,
        'payments': 'billing' in org.enabled_features,
        'plans': 'billing' in org.enabled_features,
    }
    nav = []
    for key, url_name, label, icon, icon_active in _SECTIONS:
        if not shown.get(key, True):
            continue
        nav.append({
            'key': key, 'label': label, 'active': key == active,
            'icon': icon_active if key == active else icon,
            'url': reverse(url_name, kwargs={'token': member.token}),
        })
    by_key = {s['key']: s for s in nav}
    # Phones: the most used sections as tabs, everything else one level deeper under More.
    preferred = ['dashboard', 'bookings' if 'bookings' in by_key else 'classes', 'payments', 'syllabus', 'details', 'classes']
    tabs = []
    for key in preferred:
        if key in by_key and by_key[key] not in tabs and len(tabs) < 4:
            tabs.append(by_key[key])
    return {'nav': nav, 'tabs': tabs, 'more_active': not any(t['active'] for t in tabs)}


@register.simple_tag(takes_context=True)
def portal_page_title(context):
    """The large title for the current portal page."""
    request = context.get('request')
    match = getattr(request, 'resolver_match', None)
    return _PAGE_TITLES.get(getattr(match, 'url_name', ''), 'Member portal')
