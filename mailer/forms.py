"""The email settings form, shared by the setup wizard and Settings → Email.

Each provider's inputs are named email_<provider>_<setting>, so switching
provider on the page never mixes up their values. Saved passwords and keys are
never sent back to the browser: leaving one blank keeps what's stored.
"""
from django.core.exceptions import ValidationError
from django.core.validators import validate_email

from . import providers


def _valid_email(value):
    try:
        validate_email(value)
        return True
    except ValidationError:
        return False


def provider_rows(email_settings=None, post=None):
    """Everything the form template needs, for every provider."""
    current = email_settings.provider if email_settings else providers.DEFAULT
    saved_secrets = email_settings.get_secrets() if email_settings else {}
    rows = []
    for provider in providers.PROVIDERS:
        fields = []
        for setting in provider.settings:
            input_name = f'email_{provider.key}_{setting.name}'
            if setting.secret:
                value = ''
                saved = provider.key == current and bool(saved_secrets.get(setting.name))
            else:
                if post is not None:
                    value = post.get(input_name, setting.default)
                elif email_settings and provider.key == current:
                    value = email_settings.options.get(setting.name, setting.default)
                else:
                    value = setting.default
                saved = False
            fields.append({'setting': setting, 'input_name': input_name, 'value': value, 'saved': saved})
        rows.append({'provider': provider, 'fields': fields})
    selected = post.get('email_provider', current) if post is not None else current
    return {
        'email_provider_rows': rows,
        'email_selected': selected,
        'email_from_name': post.get('email_from_name', '') if post is not None else getattr(email_settings, 'from_name', ''),
        'email_from_email': post.get('email_from_email', '') if post is not None else getattr(email_settings, 'from_email', ''),
        'email_reply_to': post.get('email_reply_to', '') if post is not None else getattr(email_settings, 'reply_to', ''),
    }


def apply_post(email_settings, post):
    """Read the form into `email_settings` (unsaved). Returns a list of errors; nothing changes if any."""
    errors = []
    key = post.get('email_provider', providers.DEFAULT)
    if key not in providers.BY_KEY:
        return ['Choose how Dojo should send email.']
    provider = providers.BY_KEY[key]

    from_name = post.get('email_from_name', '').strip()
    from_email = post.get('email_from_email', '').strip()
    reply_to = post.get('email_reply_to', '').strip()
    if key != 'log' and not from_email:
        errors.append('Enter the address emails come from.')
    for label, value in (('From address', from_email), ('Reply-to address', reply_to)):
        if value and not _valid_email(value):
            errors.append(f'{label} isn\'t a valid email address.')

    stored = email_settings.get_secrets()
    keep_stored = email_settings.provider == key
    options = {}
    secrets = {'dkim_private_key': stored['dkim_private_key']} if stored.get('dkim_private_key') else {}
    for setting in provider.settings:
        raw = post.get(f'email_{key}_{setting.name}', '').strip()
        if setting.secret:
            value = raw or (stored.get(setting.name, '') if keep_stored else '')
            secrets[setting.name] = value
        else:
            value = raw or setting.default
            if setting.kind == 'select' and value not in dict(setting.choices):
                errors.append(f'{provider.label}: choose a {setting.label.lower()}.')
            if setting.kind == 'number' and value and not value.isdigit():
                errors.append(f'{provider.label}: {setting.label.lower()} must be a number.')
            if setting.kind == 'email' and value and not _valid_email(value):
                errors.append(f'{provider.label}: {setting.label.lower()} isn\'t a valid email address.')
            options[setting.name] = value
        if setting.required and not value:
            errors.append(f'{provider.label}: enter the {setting.label.lower()}.')
    if errors:
        return errors

    email_settings.provider = key
    email_settings.from_name = from_name
    email_settings.from_email = from_email
    email_settings.reply_to = reply_to
    email_settings.options = options
    email_settings.set_secrets(secrets)
    return []
