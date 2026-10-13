"""Settings → Payments tools: check the Stripe keys, and create the webhook for the club."""
from django.conf import settings
from django.urls import reverse

# Card payments, saved cards and Direct Debits for automatic payment, and their results.
WEBHOOK_EVENTS = ['checkout.session.completed', 'payment_intent.succeeded', 'payment_intent.payment_failed']


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
