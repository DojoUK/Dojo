import json

from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from billing.models import Invoice
from .models import Member


class PortalView(View):
    def get(self, request, token):
        member = get_object_or_404(Member, token=token, is_active=True)
        org = member.organisation

        guardian = member.guardians.filter(email__gt='').first()
        has_guardians = member.guardians.exists()

        invoices = member.invoices.order_by('-created_at')
        outstanding = [inv for inv in invoices if inv.status != 'paid']
        paid = [inv for inv in invoices if inv.status == 'paid']

        from progression.models import MemberProgression
        progressions = (
            MemberProgression.objects
            .filter(member=member)
            .select_related('stage__system')
            .order_by('-achieved_date')
        )
        current_grade = progressions.first()

        from classes.models import Attendance, ClassMember
        enrolments = (
            ClassMember.objects.filter(member=member)
            .select_related('assigned_class')
            .order_by('assigned_class__name')
        )
        recent_attendance = (
            Attendance.objects.filter(member=member, present=True)
            .select_related('session__assigned_class')
            .order_by('-session__date')[:10]
        )

        outstanding_total = sum(inv.amount for inv in outstanding)

        stripe_enabled = bool(settings.STRIPE_PUBLIC_KEY and settings.STRIPE_SECRET_KEY)
        subscription_enabled = stripe_enabled and bool(member.monthly_fee)

        return render(request, 'portal/index.html', {
            'member': member,
            'org': org,
            'guardian': guardian,
            'has_guardians': has_guardians,
            'outstanding': outstanding,
            'paid': paid,
            'current_grade': current_grade,
            'progressions': progressions,
            'outstanding_total': outstanding_total,
            'stripe_enabled': stripe_enabled,
            'subscription_enabled': subscription_enabled,
            'enrolments': enrolments,
            'recent_attendance': recent_attendance,
        })


class DownloadDataView(View):
    """Self-service subject access / portability export (Art. 15/20) — everything held on this member,
    except internal coach/admin notes, which are excluded here and available on request from the club."""
    def get(self, request, token):
        member = get_object_or_404(Member, token=token, is_active=True)

        from classes.models import Attendance
        from documents.models import Document, SignedWaiver
        from progression.models import MemberProgression

        data = {
            'profile': {
                'name': member.name,
                'date_of_birth': member.date_of_birth,
                'email': member.email,
                'phone': member.phone,
                'emergency_contact_name': member.emergency_contact_name,
                'emergency_contact_phone': member.emergency_contact_phone,
                'emergency_contact_2_name': member.emergency_contact_2_name,
                'emergency_contact_2_phone': member.emergency_contact_2_phone,
                'address_line1': member.address_line1,
                'address_line2': member.address_line2,
                'joined_date': member.joined_date,
                'licence_number': member.licence_number,
                'licence_expiry': member.licence_expiry,
                'medical_info': member.medical_info,
                'monthly_fee': member.monthly_fee,
                'subscription_status': member.subscription_status,
            },
            'guardians': [
                {'name': g.name, 'email': g.email, 'phone': g.phone, 'relationship': g.relationship}
                for g in member.guardians.all()
            ],
            'progression': [
                {'stage': p.stage.name, 'achieved_date': p.achieved_date, 'notes': p.notes}
                for p in MemberProgression.objects.filter(member=member).select_related('stage')
            ],
            'attendance': [
                {'date': a.session.date, 'class': a.session.assigned_class.name, 'present': a.present}
                for a in Attendance.objects.filter(member=member).select_related('session__assigned_class')
            ],
            'invoices': [
                {
                    'period': inv.period, 'amount': str(inv.amount), 'discount_amount': str(inv.discount_amount),
                    'due_date': inv.due_date, 'status': inv.status, 'created_at': inv.created_at,
                    'payments': [
                        {'method': p.get_method_display(), 'amount': str(p.amount), 'paid_at': p.paid_at}
                        for p in inv.payments.all()
                    ],
                }
                for inv in member.invoices.all()
            ],
            'documents': [
                {'name': d.name, 'category': d.get_category_display(), 'uploaded_at': d.uploaded_at}
                for d in Document.objects.filter(member=member)
            ],
            'signed_waivers': [
                {'template': w.template.name, 'signer_name': w.signer_name, 'signed_at': w.signed_at}
                for w in SignedWaiver.objects.filter(member=member).select_related('template')
            ],
            'note': (
                'This export covers the personal data Dojo holds on your member record. '
                'It does not include internal coach/admin notes — contact the club directly if you need those too.'
            ),
        }

        response = HttpResponse(
            json.dumps(data, indent=2, default=str, ensure_ascii=False),
            content_type='application/json',
        )
        response['Content-Disposition'] = f'attachment; filename="{member.name}-data-export.json"'
        return response


class CreateCheckoutView(View):
    def post(self, request, token, invoice_pk):
        import stripe

        member = get_object_or_404(Member, token=token, is_active=True)
        invoice = get_object_or_404(Invoice, pk=invoice_pk, member=member, status='unpaid')

        if not (settings.STRIPE_PUBLIC_KEY and settings.STRIPE_SECRET_KEY):
            return redirect('member_portal', token=token)

        stripe.api_key = settings.STRIPE_SECRET_KEY

        site_url = settings.SITE_URL.rstrip('/')
        portal_url = f"{site_url}/p/{token}/"

        session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'gbp',
                    'unit_amount': int(invoice.amount * 100),
                    'product_data': {
                        'name': f"{member.organisation.name} — {invoice.period}",
                        'description': f"Membership fee for {member.name}",
                    },
                },
                'quantity': 1,
            }],
            mode='payment',
            success_url=portal_url + '?paid=1',
            cancel_url=portal_url,
            customer_email=(
                member.guardians.filter(email__gt='').first().email
                if member.guardians.exists()
                else member.email or None
            ),
            metadata={'invoice_pk': str(invoice.pk)},
        )

        return redirect(session.url)


class CreateSubscriptionView(View):
    def post(self, request, token):
        import stripe

        member = get_object_or_404(Member, token=token, is_active=True)

        if not (settings.STRIPE_PUBLIC_KEY and settings.STRIPE_SECRET_KEY):
            return redirect('member_portal', token=token)
        if not member.monthly_fee:
            return redirect('member_portal', token=token)

        stripe.api_key = settings.STRIPE_SECRET_KEY

        site_url = settings.SITE_URL.rstrip('/')
        portal_url = f"{site_url}/p/{token}/"

        # Create or reuse Stripe Customer
        if member.stripe_customer_id:
            customer_id = member.stripe_customer_id
        else:
            customer_email = (
                member.guardians.filter(email__gt='').first().email
                if member.guardians.exists()
                else member.email or None
            )
            customer = stripe.Customer.create(
                email=customer_email,
                name=member.name,
                metadata={'member_pk': str(member.pk)},
            )
            member.stripe_customer_id = customer.id
            member.save(update_fields=['stripe_customer_id'])
            customer_id = customer.id

        session = stripe.checkout.Session.create(
            customer=customer_id,
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'gbp',
                    'unit_amount': int(member.monthly_fee * 100),
                    'product_data': {
                        'name': f"{member.organisation.name} — Monthly membership",
                        'description': f"Monthly membership for {member.name}",
                    },
                    'recurring': {'interval': 'month'},
                },
                'quantity': 1,
            }],
            mode='subscription',
            success_url=portal_url + '?subscribed=1',
            cancel_url=portal_url,
            metadata={'member_pk': str(member.pk)},
        )

        return redirect(session.url)


class BillingPortalView(View):
    def post(self, request, token):
        import stripe

        member = get_object_or_404(Member, token=token, is_active=True)

        if not member.stripe_customer_id or not settings.STRIPE_SECRET_KEY:
            return redirect('member_portal', token=token)

        stripe.api_key = settings.STRIPE_SECRET_KEY

        site_url = settings.SITE_URL.rstrip('/')
        portal_url = f"{site_url}/p/{token}/"

        session = stripe.billing_portal.Session.create(
            customer=member.stripe_customer_id,
            return_url=portal_url,
        )

        return redirect(session.url)


class CancelSubscriptionView(View):
    def post(self, request, token):
        import stripe

        member = get_object_or_404(Member, token=token, is_active=True)

        if not member.stripe_subscription_id:
            return redirect('member_portal', token=token)

        stripe.api_key = settings.STRIPE_SECRET_KEY
        stripe.Subscription.modify(
            member.stripe_subscription_id,
            cancel_at_period_end=True,
        )
        member.subscription_status = 'cancelling'
        member.save(update_fields=['subscription_status'])

        return redirect('member_portal', token=token)
