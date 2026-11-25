"""Settings → Payments tools: check the Stripe keys, and create the webhook for the club."""
from django.conf import settings
from django.urls import reverse

# Card payments, saved cards and Direct Debits for automatic payment, and their results. Direct Debits
# paid at Checkout clear (or fail) days later: checkout.session.async_payment_succeeded / _failed.
# Chargebacks: charge.dispute.*.
WEBHOOK_EVENTS = ['checkout.session.completed', 'checkout.session.expired', 'payment_intent.succeeded',
                  'payment_intent.payment_failed', 'payment_method.automatically_updated',
                  'checkout.session.async_payment_succeeded', 'checkout.session.async_payment_failed',
                  'charge.dispute.created', 'charge.dispute.updated', 'charge.dispute.closed']


# The ways to pay Dojo tells clubs and members about, in this order: (Stripe's name, ours).
PAYMENT_METHODS = (('card', 'Card'), ('apple_pay', 'Apple Pay'), ('google_pay', 'Google Pay'),
                   ('bacs_debit', 'Bacs Direct Debit'))


def payment_methods(secret_key):
    """
    Which PAYMENT_METHODS the club's Stripe account offers at checkout, from its default payment
    method configuration (Stripe → Settings → Payment methods): a list of (key, label, state), where
    state is 'on', 'pending' (switched on, but Stripe hasn't activated it yet) or 'off'. Methods
    Stripe doesn't offer in the account's country are left out. None if Stripe can't say.
    """
    import stripe

    from .payments import field
    try:
        configs = list(stripe.PaymentMethodConfiguration.list(api_key=secret_key, limit=20).auto_paging_iter())
    except stripe.error.StripeError:
        return None
    config = (next((c for c in configs if field(c, 'is_default') and field(c, 'active')), None)
              or next((c for c in configs if field(c, 'active')), None))
    if config is None:
        return None
    rows = []
    for key, label in PAYMENT_METHODS:
        method = field(config, key)
        if method is None:
            continue
        switched_on = field(field(method, 'display_preference'), 'value') == 'on'
        rows.append((key, label, 'on' if field(method, 'available') else 'pending' if switched_on else 'off'))
    return rows


def methods_text(keys):
    """'Card, Apple Pay or Google Pay': the methods that are on, for the pay page. '' if there are none."""
    labels = [label for key, label in PAYMENT_METHODS if key in keys]
    labels = ['Direct Debit' if label == 'Bacs Direct Debit' else label for label in labels]
    return ' or '.join([', '.join(labels[:-1]), labels[-1]]) if len(labels) > 1 else ''.join(labels)


def webhook_url(org):
    return settings.SITE_URL.rstrip('/') + reverse('stripe_webhook_org', kwargs={'org_slug': org.slug})


def check_connection(secret_key):
    """(worked, explanation)."""
    import stripe
    try:
        account = stripe.Account.retrieve(api_key=secret_key)
    except stripe.error.AuthenticationError:
        return False, 'Stripe didn\'t accept the secret key.'
    except stripe.error.StripeError as error:
        return False, f'Stripe replied: {error.user_message or error}'
    from .payments import field
    name = (field(field(field(account, 'settings'), 'dashboard'), 'display_name')
            or field(field(account, 'business_profile'), 'name') or field(account, 'id'))
    mode = 'test mode' if secret_key.startswith(('sk_test_', 'rk_test_')) else 'live mode'
    return True, f'Connected to {name} ({mode}).'


def create_webhook(org, secret_key):
    """Point Stripe at this club's webhook URL. Returns the signing secret; raises StripeError on failure."""
    import stripe
    url = webhook_url(org)
    for endpoint in stripe.WebhookEndpoint.list(api_key=secret_key, limit=100).auto_paging_iter():
        if endpoint.url == url:
            # Stripe only reveals a signing secret when the endpoint is created, so start afresh.
            stripe.WebhookEndpoint.delete(endpoint.id, api_key=secret_key)
    endpoint = stripe.WebhookEndpoint.create(
        api_key=secret_key, url=url, enabled_events=WEBHOOK_EVENTS,
        description=f'Dojo: marks {org.name} invoices paid',
    )
    return endpoint.secret
