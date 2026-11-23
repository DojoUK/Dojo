from django import template
from django.utils.html import format_html_join, mark_safe

register = template.Library()


@register.simple_tag
def stars(rating):
    """Five star icons for a rating (to the nearest half star), for show; give the number in text or aria-label too."""
    try:
        halves = round(float(rating or 0) * 2)
    except (TypeError, ValueError):
        halves = 0
    icons = []
    for star in range(1, 6):
        if halves >= star * 2:
            icons.append('bi-star-fill')
        elif halves == star * 2 - 1:
            icons.append('bi-star-half')
        else:
            icons.append('bi-star')
    return format_html_join('', '<i class="bi {}" aria-hidden="true"></i>', ((i,) for i in icons)) or mark_safe('')
