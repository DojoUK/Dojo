from django import template
from django.utils.html import format_html

from organisations.branding import backdrop_style

register = template.Library()


@register.simple_tag
def org_logo(org, background, height=32, max_width=160, css_class=''):
    """
    {% org_logo org '#1E3A5F' 32 160 %}

    The club's logo, scaled to fit without cropping, with a contrasting backing
    when it would blend into `background` (a #rrggbb colour). Empty if no logo.
    """
    if not org or not org.logo:
        return ''
    return format_html(
        '<img src="{}" alt="{}" class="{}" style="max-height:{}px;max-width:{}px;width:auto;height:auto;'
        'object-fit:contain;flex-shrink:0;{}">',
        org.logo.url, org.name, css_class, height, max_width, backdrop_style(org.logo_luminance, background),
    )


@register.simple_tag
def only_organisation():
    """The organisation, when this server hosts exactly one (for the login page); else None."""
    from organisations.models import Organisation
    orgs = list(Organisation.objects.all()[:2])
    return orgs[0] if len(orgs) == 1 else None
