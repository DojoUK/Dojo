import json

from django.conf import settings
from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from billing.models import Invoice
from .models import Member, MemberLeavingRequest


class PortalLoginRequestView(View):
    """
    Public, org-scoped "log in with your email" entry point for the member
    portal. There's no separate password to manage — matching the magic-link
    approach, entering an email just re-sends the member's existing portal
    link. Always shows the same generic confirmation regardless of whether
    the email matched anything, so this can't be used to probe membership.
    """
    def get(self, request, org_slug):
        from organisations.models import Organisation
        org = get_object_or_404(Organisation, slug=org_slug)
        return render(request, 'portal/login.html', {'org': org})

    def post(self, request, org_slug):
        from organisations.models import Organisation
        from .emails import send_portal_login_link_email

        org = get_object_or_404(Organisation, slug=org_slug)
        email = request.POST.get('email', '').strip()

        if email:
            member = Member.objects.filter(
                organisation=org, is_active=True, email__iexact=email,
            ).first()
            if not member:
                # Also check guardian emails, for junior members signed up via a parent.
                member = Member.objects.filter(
                    organisation=org, is_active=True, guardians__email__iexact=email,
                ).first()
            if member:
                send_portal_login_link_email(member)

        return render(request, 'portal/login.html', {'org': org, 'submitted': True})


class PortalView(View):
    """The portal's Dashboard — what needs the member's attention (outstanding
    invoices, autopay status, credit/arrears) plus quick links into the other
    sections. Detailed content lives on its own page (see PortalClassesView,
    PortalPaymentHistoryView, etc.) rather than all being stacked here."""
    def get(self, request, token):
        member = get_object_or_404(Member, token=token, is_active=True)
        org = member.organisation

        has_guardians = member.guardians.exists()

        invoices = member.invoices.order_by('-created_at')
        outstanding = [inv for inv in invoices if inv.status != 'paid']

        from progression.models import MemberProgression
        current_grade = (
            MemberProgression.objects
            .filter(member=member)
            .select_related('stage__system')
            .order_by('-achieved_date')
            .first()
        )

        outstanding_total = sum(inv.amount for inv in outstanding)

        from decimal import Decimal
        credit_total = sum((inv.available_credit for inv in invoices), Decimal('0'))

        stripe_enabled = bool(settings.STRIPE_PUBLIC_KEY and settings.STRIPE_SECRET_KEY)
        subscription_enabled = stripe_enabled and bool(member.monthly_fee)

        leaving_request = member.leaving_requests.filter(status=MemberLeavingRequest.Status.PENDING).first()

        return render(request, 'portal/index.html', {
            'active': 'dashboard',
            'member': member,
            'org': org,
            'has_guardians': has_guardians,
            'outstanding': outstanding,
            'current_grade': current_grade,
            'outstanding_total': outstanding_total,
            'credit_total': credit_total,
            'stripe_enabled': stripe_enabled,
            'subscription_enabled': subscription_enabled,
            'leaving_request': leaving_request,
        })


class PortalMyDetailsView(View):
    """
    Self-service edit of personal/emergency/medical details and the Photo
    consent custom field, plus the member's documents and the "stopping
    training" flag — everything about managing the account itself lives on
    this one page.
    """
    def _photo_consent_field(self, org):
        from .models import CustomField
        return CustomField.objects.filter(
            organisation=org, field_type=CustomField.FieldType.BOOLEAN, name__iexact='Photo consent',
        ).first()

    def get(self, request, token):
        from documents.models import Document

        member = get_object_or_404(Member, token=token, is_active=True)
        photo_consent_field = self._photo_consent_field(member.organisation)
        photo_consent = bool(
            photo_consent_field and member.custom_field_values.get(str(photo_consent_field.pk))
        )
        leaving_request = member.leaving_requests.filter(status=MemberLeavingRequest.Status.PENDING).first()
        return render(request, 'portal/my_details.html', {
            'active': 'details',
            'member': member,
            'org': member.organisation,
            'photo_consent_field': photo_consent_field,
            'photo_consent': photo_consent,
            'documents': member.documents.order_by('-uploaded_at'),
            'document_categories': Document.Category.choices,
            'leaving_request': leaving_request,
        })

    def post(self, request, token):
        member = get_object_or_404(Member, token=token, is_active=True)

        member.email = request.POST.get('email', '').strip()
        member.phone = request.POST.get('phone', '').strip()
        member.emergency_contact_name = request.POST.get('emergency_contact_name', '').strip()
        member.emergency_contact_phone = request.POST.get('emergency_contact_phone', '').strip()
        member.emergency_contact_2_name = request.POST.get('emergency_contact_2_name', '').strip()
        member.emergency_contact_2_phone = request.POST.get('emergency_contact_2_phone', '').strip()
        member.medical_info = request.POST.get('medical_info', '').strip()

        photo_consent_field = self._photo_consent_field(member.organisation)
        if photo_consent_field:
            values = dict(member.custom_field_values or {})
            values[str(photo_consent_field.pk)] = 'photo_consent' in request.POST
            member.custom_field_values = values

        member.save(update_fields=[
            'email', 'phone', 'emergency_contact_name', 'emergency_contact_phone',
            'emergency_contact_2_name', 'emergency_contact_2_phone', 'medical_info',
            'custom_field_values',
        ])
        messages.success(request, 'Your details have been updated.')
        return redirect('portal_my_details', token=token)


class PortalClassesView(View):
    """Classes the member is enrolled in, plus their recent attendance."""
    def get(self, request, token):
        member = get_object_or_404(Member, token=token, is_active=True)

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

        return render(request, 'portal/classes.html', {
            'active': 'classes',
            'member': member,
            'org': member.organisation,
            'enrolments': enrolments,
            'recent_attendance': recent_attendance,
        })


class PortalCodeOfConductView(View):
    """Static Code of Conduct page — same content for every member/org, just needs the shared portal chrome."""
    def get(self, request, token):
        member = get_object_or_404(Member, token=token, is_active=True)
        return render(request, 'portal/code_of_conduct.html', {
            'active': 'conduct',
            'member': member,
            'org': member.organisation,
        })


class PortalPaymentHistoryView(View):
    """Full invoice history — outstanding (with pay buttons) and paid."""
    def get(self, request, token):
        member = get_object_or_404(Member, token=token, is_active=True)

        invoices = member.invoices.order_by('-created_at')
        outstanding = [inv for inv in invoices if inv.status != 'paid']
        paid = [inv for inv in invoices if inv.status == 'paid']
        outstanding_total = sum(inv.amount for inv in outstanding)
        stripe_enabled = bool(settings.STRIPE_PUBLIC_KEY and settings.STRIPE_SECRET_KEY)

        return render(request, 'portal/payments.html', {
            'active': 'payments',
            'member': member,
            'org': member.organisation,
            'outstanding': outstanding,
            'paid': paid,
            'outstanding_total': outstanding_total,
            'stripe_enabled': stripe_enabled,
        })


class RequestStopTrainingView(View):
    """
    Records a member's self-reported intention to stop training. This does
    NOT archive them — it's flagged for staff to review and action on the
    staff side (see MemberDetailView / member_list "Leaving" filter).
    """
    def post(self, request, token):
        member = get_object_or_404(Member, token=token, is_active=True)
        already_pending = MemberLeavingRequest.objects.filter(
            member=member, status=MemberLeavingRequest.Status.PENDING,
        ).exists()
        if not already_pending:
            MemberLeavingRequest.objects.create(
                member=member, reason=request.POST.get('reason', '').strip(),
            )
        messages.success(
            request,
            "We've let the club know you're planning to stop training — they'll be in touch, "
            "and your account stays as-is until then.",
        )
        return redirect('portal_my_details', token=token)


class PortalSyllabusView(View):
    """Syllabus checklist auto-filtered to the member's current stage in each progression system they're in. Read-only — only staff can tick items."""
    def get(self, request, token):
        member = get_object_or_404(Member, token=token, is_active=True)
        from progression.models import MemberProgression, MemberSyllabusProgress

        progressions = (
            MemberProgression.objects.filter(member=member)
            .select_related('stage__system', 'stage__syllabus_section')
            .order_by('-achieved_date')
        )
        current_by_system = {}
        for p in progressions:
            current_by_system.setdefault(p.stage.system_id, p)

        syllabus_cards = []
        for p in current_by_system.values():
            section = p.stage.syllabus_section
            items = list(section.items.all()) if section else []
            done_ids = set(
                MemberSyllabusProgress.objects.filter(
                    member=member, item__in=items, completed=True
                ).values_list('item_id', flat=True)
            )
            syllabus_cards.append({
                'stage': p.stage,
                'section': section,
                'items': [{'item': i, 'done': i.pk in done_ids} for i in items],
            })

        return render(request, 'portal/syllabus.html', {
            'active': 'syllabus',
            'member': member,
            'org': member.organisation,
            'syllabus_cards': syllabus_cards,
            'progressions': progressions,
        })


class PortalDocumentUploadView(View):
    """Member uploads a file to their own profile — visible to staff same as anything staff upload."""
    def post(self, request, token):
        from documents.models import Document

        member = get_object_or_404(Member, token=token, is_active=True)
        f = request.FILES.get('file')
        if not f:
            messages.error(request, 'No file selected.')
            return redirect('portal_my_details', token=token)

        name = request.POST.get('name', '').strip() or f.name
        category = request.POST.get('category', Document.Category.OTHER)
        valid_categories = {c for c, _ in Document.Category.choices}
        if category not in valid_categories:
            category = Document.Category.OTHER

        Document.objects.create(
            member=member, name=name, category=category,
            file=f, uploaded_by_member=True,
        )
        messages.success(request, f'"{name}" uploaded.')
        return redirect('portal_my_details', token=token)


class PortalDocumentDownloadView(View):
    def get(self, request, token, pk):
        from django.http import FileResponse, Http404
        from documents.models import Document

        member = get_object_or_404(Member, token=token, is_active=True)
        doc = get_object_or_404(Document, pk=pk, member=member)
        try:
            return FileResponse(doc.file.open('rb'), as_attachment=True, filename=doc.name)
        except FileNotFoundError:
            raise Http404


class PortalDataSummaryView(View):
    """
    Self-service subject access (Art. 15) as a page the member can actually
    read — same underlying data as DownloadDataView's JSON export, but laid
    out with headings and tables so it opens in any browser with no JSON
    viewer needed, and can be saved as a PDF via the browser's own print
    dialog. The raw JSON export is still linked from here for anyone who
    specifically wants a machine-readable copy (Art. 20 portability).
    """
    def get(self, request, token):
        member = get_object_or_404(Member, token=token, is_active=True)

        from classes.models import Attendance
        from documents.models import Document, SignedWaiver
        from progression.models import MemberProgression

        progressions = (
            MemberProgression.objects.filter(member=member)
            .select_related('stage__system')
            .order_by('-achieved_date')
        )
        attendance = (
            Attendance.objects.filter(member=member)
            .select_related('session__assigned_class')
            .order_by('-session__date')
        )
        invoices = member.invoices.order_by('-created_at').prefetch_related('payments')
        documents = Document.objects.filter(member=member)
        signed_waivers = SignedWaiver.objects.filter(member=member).select_related('template')

        return render(request, 'portal/my_data.html', {
            'active': 'details',
            'member': member,
            'org': member.organisation,
            'guardians': member.guardians.all(),
            'progressions': progressions,
            'attendance': attendance,
            'invoices': invoices,
            'documents': documents,
            'signed_waivers': signed_waivers,
        })


class DownloadDataView(View):
    """Raw JSON portability export (Art. 20) — everything held on this member,
    except internal coach/admin notes, which are excluded here and available on request from the club.
    Most members are better served by PortalDataSummaryView; this is for anyone who specifically wants
    a machine-readable copy."""
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
