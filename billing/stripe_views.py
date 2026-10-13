import json
from decimal import Decimal

import stripe
from django.conf import settings
from django.http import HttpResponse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from .models import Invoice, Payment
from .payments import field


class StripeWebhookView(View):
    @method_decorator(csrf_exempt)
    def dispatch(self, *args, **kwargs):
        return super().dispatch(*args, **kwargs)

    def post(self, request, org_slug=None):
        payload = request.body
        sig_header = request.META.get('HTTP_STRIPE_SIGNATURE', '')
        # Each club's own endpoint is checked with its own signing secret (Settings → Payments);
        # the plain endpoint is for keys set in the STRIPE_* environment variables.
        self.organisation = None
        if org_slug:
            from organisations.models import Organisation

            from .payments import stripe_keys
            self.organisation = Organisation.objects.filter(slug=org_slug).first()
            keys = stripe_keys(self.organisation) if self.organisation else None
            webhook_secret = keys.webhook_secret if keys else ''
        else:
            if not settings.STRIPE_SECRET_KEY:
                return HttpResponse(status=400)
            stripe.api_key = settings.STRIPE_SECRET_KEY
            webhook_secret = settings.STRIPE_WEBHOOK_SECRET

        if not webhook_secret:
            return HttpResponse(status=400)

        try:
            event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
        except (ValueError, stripe.error.SignatureVerificationError):
            return HttpResponse(status=400)

        event_type = event['type']
        obj = event['data']['object']

        if event_type == 'checkout.session.completed' and field(obj, 'mode') == 'setup':
            self._handle_autopay_setup(obj)

        elif event_type == 'checkout.session.completed':
            self._handle_checkout_completed(obj)

        elif event_type == 'payment_intent.succeeded' and self.organisation is not None:
            from .autopay import payment_intent_succeeded
            payment_intent_succeeded(self.organisation, obj)

        elif event_type == 'payment_intent.payment_failed' and self.organisation is not None:
            from .autopay import payment_intent_failed
            payment_intent_failed(self.organisation, obj)

        elif event_type in ('customer.subscription.created', 'customer.subscription.updated'):
            self._handle_subscription_updated(obj)

        elif event_type == 'customer.subscription.deleted':
            self._handle_subscription_deleted(obj)

        elif event_type == 'invoice.payment_succeeded':
            self._handle_subscription_invoice_paid(obj)

        return HttpResponse(status=200)

    def _handle_autopay_setup(self, session):
        """A member saved a card or set up a Direct Debit for automatic payments."""
        if self.organisation is None or not field(session, 'setup_intent'):
            return
        import stripe

        from .autopay import save_from_setup_intent
        from .payments import stripe_keys
        keys = stripe_keys(self.organisation)
        setup_intent = stripe.SetupIntent.retrieve(session['setup_intent'], api_key=keys.secret,
                                                   expand=['payment_method'])
        save_from_setup_intent(self.organisation, setup_intent)

    def _handle_checkout_completed(self, session):
        try:
            invoice_pk = session.metadata['invoice_pk']
        except (AttributeError, KeyError, TypeError):
            invoice_pk = None

        if invoice_pk:
            invoices = Invoice.objects.all()
            if self.organisation is not None:
                invoices = invoices.filter(organisation=self.organisation)
            try:
                invoice = invoices.get(pk=invoice_pk)
                if invoice.status != 'paid':
                    amount_total = getattr(session, 'amount_total', None)
                    Payment.objects.create(
                        invoice=invoice,
                        amount=(Decimal(amount_total) / 100) if amount_total is not None else invoice.balance_due,
                        method=Payment.Method.STRIPE,
                        stripe_payment_id=getattr(session, 'payment_intent', ''),
                        notes=f"Stripe Checkout session {session.id}",
                        paid_at=timezone.now(),
                    )
                    invoice.status = 'paid'
                    invoice.save(update_fields=['status'])
            except Invoice.DoesNotExist:
                pass

        # If this was a subscription checkout, store subscription_id on member
        if getattr(session, 'mode', None) == 'subscription' and getattr(session, 'subscription', None):
            self._store_subscription_on_member(session)

    def _store_subscription_on_member(self, session):
        from members.models import Member
        try:
            member_pk = session.metadata['member_pk']
            member = Member.objects.get(pk=member_pk)
            member.stripe_subscription_id = session.subscription
            member.subscription_status = 'active'
            member.save(update_fields=['stripe_subscription_id', 'subscription_status'])
        except (AttributeError, KeyError, TypeError, Member.DoesNotExist):
            pass

    def _handle_subscription_updated(self, subscription):
        from members.models import Member
        try:
            member = Member.objects.get(stripe_subscription_id=subscription.id)
            member.subscription_status = subscription.status
            member.save(update_fields=['subscription_status'])
        except Member.DoesNotExist:
            pass

    def _handle_subscription_deleted(self, subscription):
        from members.models import Member
        try:
            member = Member.objects.get(stripe_subscription_id=subscription.id)
            member.stripe_subscription_id = ''
            member.subscription_status = 'cancelled'
            member.save(update_fields=['stripe_subscription_id', 'subscription_status'])
        except Member.DoesNotExist:
            pass

    def _handle_subscription_invoice_paid(self, stripe_invoice):
        # Only process subscription invoices (not manual one-off payments)
        if not getattr(stripe_invoice, 'subscription', None):
            return
        from members.models import Member
        try:
            member = Member.objects.get(stripe_subscription_id=stripe_invoice.subscription)
        except Member.DoesNotExist:
            return

        amount_gbp = stripe_invoice.amount_paid / 100
        billing_reason = getattr(stripe_invoice, 'billing_reason', '')
        period_end = getattr(stripe_invoice, 'period_end', None)

        import datetime
        if period_end:
            period_date = datetime.datetime.fromtimestamp(period_end, tz=timezone.utc)
            period_str = period_date.strftime('%B %Y')
        else:
            period_str = timezone.now().strftime('%B %Y')

        # Create a Django invoice + payment so it shows in billing history
        invoice = Invoice.objects.create(
            member=member,
            organisation=member.organisation,
            period=f"Subscription — {period_str}",
            amount=amount_gbp,
            due_date=timezone.localdate(),
            status='paid',
        )
        Payment.objects.create(
            invoice=invoice,
            amount=amount_gbp,
            method='Stripe (subscription)',
            stripe_payment_id=getattr(stripe_invoice, 'payment_intent', '') or '',
            notes=f"Automatic subscription payment — {stripe_invoice.id}",
            paid_at=timezone.now(),
        )
