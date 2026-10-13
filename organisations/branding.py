"""
Club logo: checking uploads, and keeping the logo readable on any background.

On upload Dojo measures how light or dark the logo's visible pixels are.
Wherever the logo is shown, that's compared with the background behind it;
if they'd blend together (a dark badge on the dark sidebar, say), the logo
gets a small contrasting backing.
"""
import re

from django.conf import settings
from PIL import Image, UnidentifiedImageError

ALLOWED_FORMATS = {'PNG': 'PNG', 'JPEG': 'JPG', 'WEBP': 'WebP'}
MAX_BYTES = 2 * 1024 * 1024
MAX_SIDE = 4000
# Below this difference in brightness (0-1) the logo is hard to see.
MIN_CONTRAST = 0.35


def validate_logo(upload):
    """An error message for an unacceptable logo upload, or None if it's fine."""
    if upload.size > MAX_BYTES:
        return 'The logo must be 2MB or smaller.'
    try:
        upload.seek(0)
        with Image.open(upload) as image:
            image.verify()
            fmt, size = image.format, image.size
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
        return 'That file isn\'t an image Dojo can use. Upload a PNG, JPG or WebP.'
    finally:
        upload.seek(0)
    if fmt not in ALLOWED_FORMATS:
        return 'Upload the logo as a PNG, JPG or WebP.'
    if max(size) > MAX_SIDE:
        return f'The logo is too large; keep it under {MAX_SIDE} pixels on each side.'
    return None


def image_luminance(file):
    """Average perceived brightness (0 dark – 1 light) of an image's visible pixels."""
    file.seek(0)
    with Image.open(file) as image:
        image = image.convert('RGBA')
        image.thumbnail((64, 64))
        pixels = [(r, g, b) for r, g, b, a in image.getdata() if a >= 64]
    file.seek(0)
    if not pixels:
        return 0.5
    return sum(0.2126 * r + 0.7152 * g + 0.0722 * b for r, g, b in pixels) / (255 * len(pixels))


def hex_luminance(colour):
    """Perceived brightness (0–1) of a #rrggbb colour; mid-grey if it can't be read."""
    match = re.fullmatch(r'#?([0-9a-fA-F]{6})', (colour or '').strip())
    if not match:
        return 0.5
    r, g, b = (int(match.group(1)[i:i + 2], 16) for i in (0, 2, 4))
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255


def backdrop_style(logo_luminance, background):
    """CSS for a contrasting backing behind the logo, or '' when it already stands out."""
    if logo_luminance is None:
        return ''
    background_luminance = hex_luminance(background)
    if abs(logo_luminance - background_luminance) >= MIN_CONTRAST:
        return ''
    backing = '#ffffff' if background_luminance < 0.5 else '#1f2937'
    return f'background:{backing};padding:3px;border-radius:6px;'


def absolute_logo_url(org):
    """The logo's full address, for emails; '' when the club has no logo."""
    if not org.logo:
        return ''
    return settings.SITE_URL.rstrip('/') + org.logo.url


def email_branding(org):
    """Template context for the email layout: the club's logo, colours and contact details."""
    from django.templatetags.static import static
    theme = org.theme()
    luminance = org.logo_luminance if org.logo else None
    return {
        'org_logo_url': absolute_logo_url(org),
        # A backing behind the logo when it would blend into the header (the club's colour).
        'org_logo_backdrop': backdrop_style(luminance, theme['sidebar_color']),
        'brand_colour': theme['sidebar_color'],
        'brand_accent': theme['accent_color'],
        'club_email': org.email,
        'club_website': org.website,
        'dojo_wordmark_url': settings.SITE_URL.rstrip('/') + static('img/dojo-wordmark.png'),
    }
