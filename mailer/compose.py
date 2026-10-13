"""
Turning a club's email wording (or full HTML) into the email that's sent.

Fields like {first_name} are simple swaps, filled with escaped values: club
admins can't reach into Dojo's data or templates from here. Blocks like
{pay_options} insert ready-made pieces that Dojo builds (see mailer.blocks).
"""
import html
import re

from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils.html import strip_tags

from . import catalogue

FIELD = re.compile(r'\{([a-z_]+)\}')
LINK = re.compile(r'\[([^\]\n]+)\]\(([^)\s]+)\)')
BUTTON = re.compile(r'\[button:\s*([^\]\n]+)\]\(([^)\s]+)\)')
BOLD = re.compile(r'\*\*(.+?)\*\*')
PARAGRAPH_STYLE = 'color:#374151;font-size:15px;line-height:1.6;margin:0 0 20px;'


def _fill(text, values, escape):
    def swap(match):
        name = match.group(1)
        if name not in values:
            return match.group(0)  # unknown fields stay visible, so typos are easy to spot
        value = str(values[name])
        return html.escape(value) if escape else value
    return FIELD.sub(swap, text)


def _safe_url(url):
    url = url.strip()
    return url if re.match(r'(https?://|mailto:)', url, re.IGNORECASE) else '#'


def button_html(text, url, accent):
    return render_to_string('emails/blocks/button.html', {'text': text, 'url': url, 'accent': accent})


def _inline_html(text, values, accent):
    """One paragraph of wording: escaped, then **bold**, links, fields and line breaks."""
    out = html.escape(text, quote=False)

    def link(match):
        url = _safe_url(_fill(html.unescape(match.group(2)), values, escape=False))
        return f'<a href="{html.escape(url)}" style="color:{accent};">{match.group(1)}</a>'
    out = LINK.sub(link, out)
    out = BOLD.sub(r'<strong>\1</strong>', out)
    out = _fill(out, values, escape=True)
    return out.replace('\n', '<br>')


def _paragraphs(body):
    return [part.strip() for part in re.split(r'\n\s*\n', body.strip()) if part.strip()]


def wording_to_html(body, values, blocks, accent):
    parts = []
    for paragraph in _paragraphs(body):
        alone = FIELD.fullmatch(paragraph)
        if alone and alone.group(1) in blocks:
            parts.append(blocks[alone.group(1)]['html'])
            continue
        button = BUTTON.fullmatch(paragraph)
        if button:
            text = html.escape(_fill(button.group(1), values, escape=False))
            url = _safe_url(_fill(button.group(2), values, escape=False))
            parts.append(button_html(text, url, accent))
            continue
        parts.append(f'<p style="{PARAGRAPH_STYLE}">{_inline_html(paragraph, values, accent)}</p>')
    return '\n'.join(parts)


def wording_to_text(body, values, blocks):
    parts = []
    for paragraph in _paragraphs(body):
        alone = FIELD.fullmatch(paragraph)
        if alone and alone.group(1) in blocks:
            parts.append(blocks[alone.group(1)]['text'])
            continue
        text = BUTTON.sub(lambda m: f'{m.group(1)}: {m.group(2)}', paragraph)
        text = LINK.sub(lambda m: f'{m.group(1)} ({m.group(2)})', text)
        text = BOLD.sub(r'\1', text)
        parts.append(_fill(text, values, escape=False))
    return '\n\n'.join(part for part in parts if part)


def html_to_text(document):
    text = re.sub(r'(?is)<(script|style|head)\b.*?</\1>', '', document)
    text = re.sub(r'(?i)<br\s*/?>|</p>|</tr>|</h\d>|</div>', '\n', text)
    text = html.unescape(strip_tags(text))
    lines = [line.strip() for line in text.splitlines()]
    return re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip()


def layout_options(org):
    """The club's choices for the email layout (Settings → Email → Customise emails)."""
    stored = (org.settings or {}).get('email_layout') or {}
    return {
        'header_colour': stored.get('header_colour', ''),
        'show_logo': stored.get('show_logo', True),
        'header_text': stored.get('header_text', ''),
        'footer_text': stored.get('footer_text', ''),
    }


def club_values(org):
    return {'club_name': org.name, 'club_email': org.email or '', 'club_website': org.website or ''}


def member_values(member, to_guardian=None):
    """greeting, first_name and member_name for `member`. By default the email goes to a guardian
    when the member has no address of their own."""
    first_name = member.name.split(' ')[0]
    if to_guardian is None:
        to_guardian = member.guardians.exists() and not member.email
    return {
        'greeting': f'Dear guardian of {member.name}' if to_guardian else f'Hi {first_name}',
        'first_name': first_name,
        'member_name': member.name,
    }


def _template_for(org, key):
    from .models import EmailTemplate
    return EmailTemplate.objects.filter(organisation=org, key=key).first()


def render(org, key, values, blocks=None, *, subject=None, mode=None, body=None, html_document=None):
    """(subject, html, text) for one of `org`'s emails.

    The club's saved version is used unless subject/mode/body/html_document are given
    (as the editor's preview does).
    """
    from organisations.branding import email_branding
    email_type = catalogue.BY_KEY[key]
    blocks = blocks or {}
    values = {**club_values(org), **values}
    if subject is None:
        saved = _template_for(org, key)
        subject = saved.subject if saved else email_type.default_subject
        mode = saved.mode if saved else 'wording'
        body = saved.body if saved else email_type.default_body
        html_document = saved.html if saved else ''
    subject_text = ' '.join(_fill(subject, values, escape=False).split())

    branding = email_branding(org)
    options = layout_options(org)
    accent = branding['brand_accent']
    if mode == 'html' and html_document:
        document = _fill(html_document, values, escape=True)
        document = FIELD.sub(lambda m: blocks[m.group(1)]['html'] if m.group(1) in blocks else m.group(0), document)
        return subject_text, document, html_to_text(document)

    content = wording_to_html(body, values, blocks, accent)
    document = render_to_string('emails/base.html', {
        'org_name': org.name, **branding, 'content_html': content, 'subject': subject_text, 'layout': options,
    })
    footer = options['footer_text'] or (
        f'Sent by {org.name}.' + (f' Questions? Just reply, or email {org.email}.' if org.email else ''))
    text = f'{wording_to_text(body, values, blocks)}\n\n--\n{footer}\nPowered by Dojo'
    return subject_text, document, text


def send(org, key, to, values, blocks=None):
    """Send one of `org`'s emails to `to` (a list of addresses) with its chosen service."""
    from . import connection_for
    subject, document, text = render(org, key, values, blocks)
    message = EmailMultiAlternatives(subject=subject, body=text, to=to, connection=connection_for(org))
    message.attach_alternative(document, 'text/html')
    message.send()
    return message
