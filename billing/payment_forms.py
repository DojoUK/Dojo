"""The "how members pay" form, shared by the setup wizard and Settings → Payments.

Saved Stripe secrets are never sent back to the browser: leaving one blank
keeps what's stored.
"""
import re


def form_context(payment_settings=None, post=None):
    ps = payment_settings
    if post is not None:
        def value(name, default=''):
            return post.get(f'pay_{name}', default)
        ticked = {m: bool(post.get(f'pay_{m}')) for m in ('bank_transfer', 'card', 'cash')}
    else:
        def value(name, default=''):
            return getattr(ps, name, default) if ps else default
        ticked = {m: bool(ps and getattr(ps, f'accept_{m}')) for m in ('bank_transfer', 'card', 'cash')}
    secrets = ps.get_secrets() if ps else {}
    return {
        'pay_ticked': ticked,
        'pay_values': {
            'bank_account_name': value('bank_account_name'),
            'bank_sort_code': value('bank_sort_code'),
            'bank_account_number': value('bank_account_number'),
            'reference_prefix': value('reference_prefix', 'INV') or 'INV',
            'instructions': value('instructions'),
            'stripe_publishable_key': value('stripe_publishable_key'),
            'auto_pay': value('auto_pay', 'off') or 'off',
        },
        'pay_auto_pay_choices': _auto_pay_choices(),
        'pay_saved': {
            'stripe_secret_key': bool(secrets.get('stripe_secret_key')),
            'stripe_webhook_secret': bool(secrets.get('stripe_webhook_secret')),
        },
    }


def _auto_pay_choices():
    from .models import PaymentSettings
    return PaymentSettings.AutoPayPolicy.choices


def _mode(key):
    return 'test' if '_test_' in key else 'live'


def apply_post(ps, post):
    """Read the form into `ps` (unsaved). Returns a list of errors; nothing changes if any."""
    errors = []
    accept = {m: bool(post.get(f'pay_{m}')) for m in ('bank_transfer', 'card', 'cash')}

    def get(name):
        return post.get(f'pay_{name}', '').strip()


    account_name = get('bank_account_name')
    sort_digits = re.sub(r'[\s-]', '', get('bank_sort_code'))
    account_number = re.sub(r'\s', '', get('bank_account_number'))
    if accept['bank_transfer']:
        if not re.fullmatch(r'\d{6}', sort_digits):
            errors.append('Bank transfer: the sort code should be 6 digits, like 12-34-56.')
        if not re.fullmatch(r'\d{8}', account_number):
            errors.append('Bank transfer: the account number should be 8 digits.')
    prefix = get('reference_prefix').upper() or 'INV'
    if not re.fullmatch(r'[A-Z0-9]{1,10}', prefix):
        errors.append('The reference prefix can only use letters and numbers (up to 10).')

    stored = ps.get_secrets()
    publishable = get('stripe_publishable_key')
    secret = get('stripe_secret_key') or stored.get('stripe_secret_key', '')
    webhook_secret = get('stripe_webhook_secret') or stored.get('stripe_webhook_secret', '')
    if accept['card']:
        if not publishable.startswith('pk_'):
            errors.append('Card payments: the publishable key starts with pk_ (Stripe → Developers → API keys).')
        if not secret.startswith(('sk_', 'rk_')):
            errors.append('Card payments: the secret key starts with sk_ (or rk_ for a restricted key).')
        elif publishable.startswith('pk_') and _mode(publishable) != _mode(secret):
            errors.append('Card payments: one key is a test key and the other is live. Use a matching pair.')
        if webhook_secret and not webhook_secret.startswith('whsec_'):
            errors.append('Card payments: the webhook signing secret starts with whsec_.')
    auto_pay = get('auto_pay') or 'off'
    if auto_pay not in dict(_auto_pay_choices()):
        errors.append('Choose whether members can pay automatically.')
    elif auto_pay != 'off' and not accept['card']:
        errors.append('Paying automatically needs card payments (Stripe) switched on.')
    if errors:
        return errors

    ps.auto_pay = auto_pay
    ps.accept_bank_transfer = accept['bank_transfer']
    ps.accept_card = accept['card']
    ps.accept_cash = accept['cash']
    ps.bank_account_name = account_name
    ps.bank_sort_code = f'{sort_digits[:2]}-{sort_digits[2:4]}-{sort_digits[4:]}' if sort_digits else ''
    ps.bank_account_number = account_number
    ps.reference_prefix = prefix
    ps.instructions = get('instructions')
    ps.stripe_publishable_key = publishable
    ps.set_secrets({**stored, 'stripe_secret_key': secret, 'stripe_webhook_secret': webhook_secret})
    return []
