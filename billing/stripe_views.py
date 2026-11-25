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

        elif event_type == 'checkout.session.async_payment_succeeded':
            self._handle_async_payment(obj, succeeded=True)

        elif event_type == 'checkout.session.async_payment_failed':
            self._handle_async_payment(obj, succeeded=False)

        elif event_type == 'checkout.session.expired':
            self._handle_checkout_expired(obj)

        elif event_type == 'payment_intent.succeeded' and self.organisation is not None:
            from .autopay import payment_intent_succeeded
            payment_intent_succeeded(self.organisation, obj)

        elif event_type == 'payment_intent.payment_failed' and self.organisation is not None:
            from .autopay import payment_intent_failed
            payment_intent_failed(self.organisation, obj)

        elif event_type == 'payment_method.automatically_updated' and self.organisation is not None:
            from .autopay import payment_method_updated
            payment_method_updated(self.organisation, obj)

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

    def _checkout_invoice(self, session):
        """The Dojo invoice a Checkout session is paying, or None."""
        invoice_pk = str(field(field(session, 'metadata'), 'invoice_pk', ''))
        if not invoice_pk.isdigit():
            return None
        invoices = Invoice.objects.filter(pk=invoice_pk)
        if self.organisation is not None:
            invoices = invoices.filter(organisation=self.organisation)
        return invoices.first()

    def _handle_checkout_completed(self, session):
        invoice = self._checkout_invoice(session)
        if invoice is not None:
            if field(session, 'payment_status', 'paid') == 'unpaid':
                # Paid by Direct Debit: Stripe has the mandate, and reports the result in about 3 working
                # days (checkout.session.async_payment_succeeded or _failed). Until then it isn't paid.
                Invoice.objects.filter(pk=invoice.pk, status='unpaid').update(autopay_status='processing',
                                                                              autopay_error='')
            else:
                self._record_checkout_payment(invoice, session)

        # If this was a subscription checkout, store subscription_id on member
        if field(session, 'mode') == 'subscription' and field(session, 'subscription'):
            self._store_subscription_on_member(session)

    def _record_checkout_payment(self, invoice, session):
        if invoice.status != 'paid':
            amount_total = field(session, 'amount_total')
            Payment.objects.create(
                invoice=invoice,
                amount=(Decimal(amount_total) / 100) if amount_total is not None else invoice.balance_due,
                method=Payment.Method.STRIPE,
                stripe_payment_id=field(session, 'payment_intent', ''),
                notes=f"Stripe Checkout session {field(session, 'id')}",
                paid_at=timezone.now(),
            )
            invoice.status = 'paid'
            invoice.autopay_status = 'paid' if invoice.autopay_status == 'processing' else invoice.autopay_status
            invoice.save(update_fields=['status', 'autopay_status'])
        if invoice.status == 'paid':
            # A single session booked by a member with no plan is confirmed only now.
            from classes.booking import confirm_paid
            confirm_paid(invoice)

    def _handle_async_payment(self, session, succeeded):
        """A Direct Debit started at Stripe Checkout has cleared, or failed."""
        invoice = self._checkout_invoice(session)
        if invoice is not None:
            if succeeded:
                self._record_checkout_payment(invoice, session)
            elif invoice.status == 'unpaid':
                from .autopay import payment_failed
                payment_failed(invoice, 'The Direct Debit didn\'t go through. Your bank may have more details.')
        if field(session, 'mode') == 'subscription' and field(session, 'subscription'):
            from members.models import Member
            Member.objects.filter(stripe_subscription_id=field(session, 'subscription')).update(
                subscription_status='active' if succeeded else 'incomplete')

    def _handle_checkout_expired(self, session):
        """A checkout page timed out unpaid: let go of the single session held for it."""
        try:
            invoice_pk = session.metadata['invoice_pk']
        except (AttributeError, KeyError, TypeError):
            return
        invoices = Invoice.objects.filter(pk=invoice_pk)
        if self.organisation is not None:
            invoices = invoices.filter(organisation=self.organisation)
        invoice = invoices.first()
        if invoice is not None:
            from classes.booking import release_for_invoice
            release_for_invoice(invoice)

    def _store_subscription_on_member(self, session):
        from members.models import Member
        try:
            member_pk = session.metadata['member_pk']
            member = Member.objects.get(pk=member_pk)
            member.stripe_subscription_id = session.subscription
            # A Direct Debit takes about 3 working days: active once it clears (_handle_async_payment).
            member.subscription_status = 'incomplete' if field(session, 'payment_status', 'paid') == 'unpaid' else 'active'
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
            period_date = datetime.datetime.fromtimestamp(period_end, tz=datetime.timezone.utc)
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
            method=Payment.Method.STRIPE,  # 'Stripe (subscription)' didn't fit the 10-character column
            stripe_payment_id=getattr(stripe_invoice, 'payment_intent', '') or '',
            notes=f"Automatic subscription payment — {stripe_invoice.id}",
            paid_at=timezone.now(),
        )
