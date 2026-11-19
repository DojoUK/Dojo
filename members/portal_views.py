import json
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View

from billing.models import Invoice
from billing.payments import pay_token, stripe_keys
from billing.payments import settings_for as payments_settings_for
from dojo.mixins import require_features

from .models import Member, MemberLeavingRequest


def _portal_member(token, *features):
    """The active member for a portal token, 404 unless the member portal and
    any other given features are switched on for their organisation."""
    member = get_object_or_404(Member.objects.select_related('organisation'), token=token, is_active=True)
    require_features(member.organisation, ('member_portal',) + features)
    return member


def _document_categories():
    from documents.models import Document
    return Document.Category.choices


def _visible_invoices(member):
    """The member's invoices, newest first. Invoices an admin hasn't checked and sent yet stay hidden."""
    return member.invoices.exclude(run__status='awaiting_review').order_by('-created_at')


def _family_invoices(member):
    """Invoices for everyone in the member's family (just the member's own if they have no family), newest first."""
    from .family import family_members
    people = family_members(member, active_only=False)
    return (Invoice.objects.filter(member__in=people).exclude(run__status='awaiting_review')
            .select_related('member').order_by('-created_at')), len(people) > 1


def _with_pay_pages(outstanding):
    """Each unpaid invoice's button opens its payment page: card, bank transfer and in-person options."""
    for inv in outstanding:
        inv.pay_page = reverse('invoice_pay', kwargs={'token': pay_token(inv)})
    return outstanding


def _payment_options(request, member):
    """Card payments, automatic payments and the member's old-style monthly subscription."""
    org = member.organisation
    from billing import autopay
    stripe_enabled = org.has_feature('billing') and stripe_keys(org) is not None
    autopay_enabled = autopay.enabled(org)
    autopay_just_set_up = None
    if autopay_enabled and request.GET.get('autopay_session'):
        autopay_just_set_up = autopay.finish_setup(org, request.GET['autopay_session'])
    payment_settings = payments_settings_for(org)
    return {
        'stripe_enabled': stripe_enabled,
        'subscription_enabled': stripe_enabled and bool(member.monthly_fee),
        'autopay_enabled': autopay_enabled,
        'autopay': autopay.active_autopay(member),
        'autopay_just_set_up': autopay_just_set_up,
        'autopay_expected': bool(payment_settings and payment_settings.auto_pay == 'encouraged'),
    }


class PortalLoginRequestView(View):
    """
    Public, org-scoped "log in with your email" entry point for the member
    portal. There's no separate password to manage — matching the magic-link
    approach, entering an email just re-sends the member's existing portal
    link. Always shows the same generic confirmation regardless of whether
    the email matched anything, so this can't be used to probe membership.
    """
    def _org(self, org_slug):
        from organisations.models import Organisation
        org = get_object_or_404(Organisation, slug=org_slug)
        require_features(org, 'member_portal')
        return org

    def get(self, request, org_slug):
        return render(request, 'portal/login.html', {'org': self._org(org_slug)})

    def post(self, request, org_slug):
        from .emails import send_portal_login_link_email

        org = self._org(org_slug)
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


def _up_next(member, limit=3):
    """The next few sessions the member is down for, soonest first: single bookings (pending ones
    flagged) and their enrolled classes' sessions, leaving out any they've said they can't make."""
    from datetime import timedelta
    from django.utils import timezone
    from classes import booking
    from classes.models import ClassMember, Session, SessionAbsence, SessionBooking

    now = timezone.now()
    today = timezone.localdate()
    horizon = today + timedelta(days=21)
    rows = []
    for b in (SessionBooking.objects.filter(member=member, session__date__gte=today, session__date__lte=horizon,
                                            session__is_cancelled=False)
              .select_related('session__assigned_class')):
        rows.append({'session': b.session, 'kind': 'booking', 'pending': b.is_pending})
    booked = {row['session'].pk for row in rows}
    enrolled = ClassMember.objects.filter(member=member, auto_enrol=True).values_list('assigned_class_id', flat=True)
    absent = set(SessionAbsence.objects.filter(member=member, session__date__gte=today).values_list('session_id', flat=True))
    for s in (Session.objects.filter(assigned_class_id__in=list(enrolled), is_cancelled=False,
                                     date__gte=today, date__lte=horizon)
              .exclude(pk__in=absent | booked).select_related('assigned_class')):
        rows.append({'session': s, 'kind': 'enrolled', 'pending': False})
    for row in rows:
        row['start'] = booking.session_start(row['session'])
        row['time'] = booking.session_time_text(row['session'])
    # Keep a session that's started in the last 90 minutes (it's on now), and today's untimed ones.
    rows = [row for row in rows
            if row['start'] >= now - timedelta(minutes=90) or (row['session'].date == today and not row['time'])]
    rows.sort(key=lambda row: (row['start'], row['session'].assigned_class.name))
    return rows[:limit]


class PortalView(View):
    """The portal's Dashboard — what needs the member's attention (outstanding
    invoices, autopay status, credit/arrears) plus quick links into the other
    sections. Detailed content lives on its own page (see PortalClassesView,
    PortalPaymentHistoryView, etc.) rather than all being stacked here."""
    def get(self, request, token):
        member = _portal_member(token)
        org = member.organisation

        invoices = _visible_invoices(member)
        outstanding = _with_pay_pages([inv for inv in invoices if inv.status not in ('paid', 'cancelled')])

        from progression.models import MemberProgression
        current_grade = (
            MemberProgression.objects
            .filter(member=member)
            .select_related('stage__system')
            .order_by('-achieved_date')
            .first()
        )

        leaving_request = member.leaving_requests.filter(status=MemberLeavingRequest.Status.PENDING).first()

        return render(request, 'portal/index.html', {
            'active': 'dashboard',
            'member': member,
            'org': org,
            'guardian': member.guardians.filter(email__gt='').first(),
            'has_guardians': member.guardians.exists(),
            'outstanding': outstanding,
            'current_grade': current_grade,
            'outstanding_total': sum(inv.amount for inv in outstanding),
            'credit_total': sum((inv.available_credit for inv in invoices), Decimal('0')),
            'leaving_request': leaving_request,
            'session_counts': member.session_counts(),
            'up_next': _up_next(member),
            **_payment_options(request, member),
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
        member = _portal_member(token)
        org = member.organisation
        photo_consent_field = self._photo_consent_field(org)
        photo_consent = bool(
            photo_consent_field and member.custom_field_values.get(str(photo_consent_field.pk))
        )
        leaving_request = member.leaving_requests.filter(status=MemberLeavingRequest.Status.PENDING).first()
        return render(request, 'portal/my_details.html', {
            'active': 'details',
            'member': member,
            'org': org,
            'photo_consent_field': photo_consent_field,
            'photo_consent': photo_consent,
            'documents': member.documents.order_by('-uploaded_at') if org.has_feature('documents') else [],
            'document_categories': _document_categories(),
            'leaving_request': leaving_request,
        })

    def post(self, request, token):
        member = _portal_member(token)

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
        member = _portal_member(token)

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
            'session_counts': member.session_counts(),
        })


def _plan_context(member, quota, today):
    """What the portal shows about a member's plan: shared by the Book and Plan pages."""
    from billing.models import BillingPolicy
    from classes import booking
    org = member.organisation
    needs_plan = booking.needs_plan(member)
    plans = []
    if org.has_feature('billing'):
        plans = list(BillingPolicy.objects.filter(organisation=org, is_active=True, member_selectable=True)
                     .order_by('name'))
    policy = member.billing_policy
    class_limited = bool(policy and policy.limits_classes)
    classes_used = booking.enrolled_class_count(member) if class_limited else None
    return {
        'plan': {
            'policy': policy, 'limited': quota.limited, 'limit': quota.limit,
            'used': quota.used(), 'period': quota.period, 'period_label': quota.label(today),
            'remaining': max(0, quota.limit - quota.used()) if quota.limited else None,
            # Class-limited plans: how many classes they're enrolled on, out of how many allowed.
            'class_limited': class_limited, 'class_limit': policy.class_limit if class_limited else None,
            'classes_used': classes_used,
            'classes_left': max(0, policy.class_limit - classes_used) if class_limited else None,
        },
        'plans': plans,
        'needs_plan': needs_plan,
        'single_price': org.single_session_price if needs_plan else None,
        'single_payable': needs_plan and booking.single_session_payable(org),
        # Past a session limit: extra sessions can still be booked, paid for at the single-session price.
        'extra_payable': booking.extra_sessions_payable(member),
        'extra_price': org.single_session_price,
    }


class PortalPlansView(View):
    """Choose or change a plan: its own section, with how much of the plan has been used."""

    def get(self, request, token):
        from django.utils import timezone

        from classes import booking

        from .family import family_members
        member = _portal_member(token, 'billing')
        today = timezone.localdate()
        return render(request, 'portal/plans.html', {
            'active': 'plans',
            'member': member,
            'org': member.organisation,
            'family_size': len(family_members(member)),
            **_plan_context(member, booking.Quota(member, today), today),
        })


class PortalBookingView(View):
    """The timetable of active classes, with enrol / single-session booking, and the member's own bookings."""
    DAYS_AHEAD = 60

    def get(self, request, token):
        member = _portal_member(token)
        org = member.organisation

        from datetime import timedelta

        from django.utils import timezone

        from classes import booking
        from classes.models import Class, ClassMember, Session, SessionAbsence, SessionBooking

        today = timezone.localdate()
        booking.release_expired()
        enrolments = {cm.assigned_class_id: cm for cm in ClassMember.objects.filter(member=member)}
        booked_ids = set(
            SessionBooking.objects.filter(member=member, session__date__gte=today).values_list('session_id', flat=True)
        )
        pending_ids = set(
            SessionBooking.objects.filter(member=member, session__date__gte=today, status='pending')
            .values_list('session_id', flat=True)
        )
        needs_plan = booking.needs_plan(member)

        absent_ids = set(
            SessionAbsence.objects.filter(member=member, session__date__gte=today).values_list('session_id', flat=True)
        )

        # Classes this member can't book (outside the age range / grades) aren't shown at all,
        # unless they're already enrolled.
        classes = [
            cls for cls in Class.objects.filter(organisation=org, is_active=True)
            .select_related('min_stage', 'max_stage').order_by('name')
            if cls.pk in enrolments or not cls.ineligible_reason(member)
        ]
        auto_ids = {pk for pk, cm in enrolments.items() if cm.auto_enrol}
        quota = booking.Quota(member, today)
        bookable = [cls for cls in classes if not cls.ineligible_reason(member)]

        # Whole-class enrolment cards.
        policy = member.billing_policy
        classes_full = bool(policy and policy.limits_classes and len(auto_ids) >= policy.class_limit)
        cards = []
        for cls in classes:
            limit_msg = ''
            if cls.pk not in auto_ids and not needs_plan:
                if classes_full:
                    limit_msg = (f'Your {policy.name} plan allows {policy.limit_text().lower()}, and you\'re '
                                 f'enrolled in {len(auto_ids)}. Leave a class to join this one, or book its sessions one at a time.')
                else:
                    try:
                        quota.check(list(cls.sessions.filter(is_cancelled=False, date__gte=today)), f'enrol in {cls.name}')
                    except booking.BookingError as e:
                        limit_msg = str(e)
            cards.append({'cls': cls, 'auto': cls.pk in auto_ids, 'full': cls.is_full,
                          'spots_left': cls.spots_left, 'limit_msg': limit_msg,
                          'needs_plan': needs_plan and cls.pk not in auto_ids})

        # Day-by-day breakdown of the sessions that can be booked.
        horizon = today + timedelta(days=self.DAYS_AHEAD)
        upcoming = (
            Session.objects.filter(assigned_class__in=bookable, is_cancelled=False, date__gte=today, date__lte=horizon)
            .select_related('assigned_class__organisation').order_by('date')
        )
        by_date = {}
        for s in upcoming:
            if booking.changes_closed(s):
                continue
            by_date.setdefault(s.date, []).append({
                'session': s, 'cls': s.assigned_class, 'time': booking.session_time_text(s),
                'enrolled': s.assigned_class_id in auto_ids, 'absent': s.pk in absent_ids, 'booked': s.pk in booked_ids,
                'pending': s.pk in pending_ids, 'full': booking.session_is_full(s), 'over_limit': not quota.can_add(s),
            })
        days = [
            {'date': day, 'iso': day.isoformat(), 'entries': sorted(entries, key=lambda e: (e['time'] or '99', e['cls'].name))}
            for day, entries in sorted(by_date.items())
        ]
        calendar_days = {d['iso']: len(d['entries']) for d in days}
        # Each day's colour in the calendar: blue if the member is booked in, otherwise green if any
        # session still has space, red if every session is full, grey if their plan's limit is all that stops them.
        def day_status(entries):
            if any(e['booked'] or (e['enrolled'] and not e['absent']) for e in entries):
                return 'booked'
            if any(not e['full'] and not e['over_limit'] for e in entries):
                return 'open'
            return 'full' if any(e['full'] for e in entries) else 'limit'
        calendar_status = {d['iso']: day_status(d['entries']) for d in days}

        my_enrolments = (
            ClassMember.objects.filter(member=member).select_related('assigned_class').order_by('assigned_class__name')
        )
        # Everything the member is down for: single bookings, plus each upcoming session of the
        # classes they're enrolled on (which they can opt out of).
        my_sessions = []
        for b in SessionBooking.objects.filter(member=member, session__date__gte=today, session__is_cancelled=False) \
                .select_related('session__assigned_class__organisation'):
            my_sessions.append({'session': b.session, 'kind': 'booking', 'absent': False, 'pending': b.is_pending,
                                'can_change': b.is_pending or not booking.changes_closed(b.session)})
        enrolled_classes = [pk for pk in auto_ids]
        for s in Session.objects.filter(assigned_class_id__in=enrolled_classes, is_cancelled=False,
                                        date__gte=today, date__lte=horizon) \
                .select_related('assigned_class__organisation'):
            my_sessions.append({'session': s, 'kind': 'enrolled', 'absent': s.pk in absent_ids,
                                'can_change': not booking.changes_closed(s)})
        my_sessions.sort(key=lambda row: (row['session'].date, row['session'].assigned_class.name))
        for row in my_sessions:
            row['time'] = booking.session_time_text(row['session'])
        session_classes = sorted({row['session'].assigned_class for row in my_sessions}, key=lambda c: c.name)

        return render(request, 'portal/bookings.html', {
            'active': 'bookings',
            **_plan_context(member, quota, today),
            'just_paid': request.GET.get('paid') == '1',
            'member': member,
            'org': org,
            'cards': cards,
            'days': days,
            'calendar_days': calendar_days,
            'calendar_status': calendar_status,
            'days_ahead': self.DAYS_AHEAD,
            'my_enrolments': my_enrolments,
            'my_sessions': my_sessions,
            'session_classes': session_classes,
            'cutoff_hours': org.booking_cutoff_hours,
        })


class PortalBookingActionView(View):
    """Enrol on a class, leave a class, book a session or cancel a session booking (POST `action`)."""

    def post(self, request, token):
        member = _portal_member(token)
        org = member.organisation

        from classes import booking
        from classes.models import Class, Session

        action = request.POST.get('action', '')
        try:
            if action in ('enrol', 'leave'):
                cls = get_object_or_404(Class, pk=request.POST.get('class_id'), organisation=org)
                if action == 'enrol':
                    booking.enrol(member, cls)
                    messages.success(request, f'You\'re enrolled in {cls.name}. A confirmation email is on its way.')
                else:
                    booking.cancel_enrolment(member, cls)
                    messages.success(request, f'You\'ve left {cls.name}. A confirmation email is on its way.')
            elif action == 'choose_plan':
                from django.http import Http404

                from billing.models import BillingPolicy
                if not org.has_feature('billing'):
                    raise Http404
                policy = get_object_or_404(
                    BillingPolicy, pk=request.POST.get('policy_id'), organisation=org,
                    is_active=True, member_selectable=True,
                )
                from members.family import FamilyPlanFull, assign_plan
                try:
                    changed = assign_plan(member, policy)
                except FamilyPlanFull as e:
                    raise booking.BookingError(str(e))
                if len(changed) > 1:
                    others = ', '.join(m.name for m in changed if m.pk != member.pk)
                    messages.success(request, f'{policy.name} is a family plan ({policy.limit_text().lower()}): '
                                              f'{member.name} and {others} are now on it.')
                else:
                    messages.success(request, f'{member.name} is now on the {policy.name} plan '
                                              f'({policy.limit_text().lower()}).')
                # Moving onto a class-limited plan doesn't take anyone off a class, but they can't join
                # another until they're under the limit.
                if policy.limits_classes:
                    for m in changed:
                        enrolled = booking.enrolled_class_count(m)
                        if enrolled > policy.class_limit:
                            messages.warning(request, f'{m.name} is enrolled in {enrolled} classes, more than '
                                                      f'{policy.name} allows. They stay on them, but can\'t join '
                                                      f'another until they\'ve left enough to be under {policy.class_limit}.')
            elif action in ('book', 'cancel_booking', 'skip_session', 'rejoin_session'):
                session = get_object_or_404(
                    Session.objects.select_related('assigned_class__organisation'),
                    pk=request.POST.get('session_id'), assigned_class__organisation=org,
                )
                if action == 'book':
                    made = booking.book_session(member, session)
                    if made.is_pending:
                        # No plan: pay for the session first. It's confirmed when Stripe's webhook says it's paid.
                        back = request.build_absolute_uri(reverse('portal_bookings', kwargs={'token': token}))
                        return redirect(booking.start_payment(made, back), permanent=False)
                    messages.success(request, 'You\'re booked in. A confirmation email is on its way.')
                elif action == 'skip_session':
                    booking.skip_session(member, session)
                    messages.success(request, 'You\'re marked as not attending. A confirmation email is on its way.')
                elif action == 'rejoin_session':
                    booking.rejoin_session(member, session)
                    messages.success(request, 'You\'re back on the register for that session. A confirmation email is on its way.')
                else:
                    paid = booking.cancel_session_booking(member, session)
                    if paid is not None:
                        messages.success(request, 'Your booking is cancelled. You paid for this session, and '
                                                  'refunds aren\'t automatic: please contact the club.')
                    else:
                        messages.success(request, 'Your booking is cancelled.')
            else:
                messages.error(request, 'That wasn\'t something we could do.')
        except booking.BookingError as e:
            messages.error(request, str(e))
        return redirect('portal_plans' if action == 'choose_plan' else 'portal_bookings', token=token)


class PortalCodeOfConductView(View):
    """Static Code of Conduct page — same content for every member/org, just needs the shared portal chrome."""
    def get(self, request, token):
        member = _portal_member(token)
        return render(request, 'portal/code_of_conduct.html', {
            'active': 'conduct',
            'member': member,
            'org': member.organisation,
        })


class PortalPaymentHistoryView(View):
    """Full invoice history — outstanding (with pay buttons) and paid — plus automatic payments."""
    def get(self, request, token):
        member = _portal_member(token, 'billing')

        invoices, is_family = _family_invoices(member)
        outstanding = _with_pay_pages([inv for inv in invoices if inv.status not in ('paid', 'cancelled')])
        paid = [inv for inv in invoices if inv.status == 'paid']

        return render(request, 'portal/payments.html', {
            'active': 'payments',
            'show_member': is_family,
            'member': member,
            'org': member.organisation,
            'outstanding': outstanding,
            'paid': paid,
            'outstanding_total': sum(inv.amount for inv in outstanding),
            **_payment_options(request, member),
        })


class RequestStopTrainingView(View):
    """
    Records a member's self-reported intention to stop training. This does
    NOT archive them — it's flagged for staff to review and action on the
    staff side (see MemberDetailView / member_list "Leaving" filter).
    """
    def post(self, request, token):
        member = _portal_member(token)
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
        member = _portal_member(token, 'grades')
        from progression.models import MemberProgression, MemberSyllabusProgress, split_syllabus_columns

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
            groups = [
                {'subsection': g['subsection'], 'items': [{'item': i, 'done': i.pk in done_ids} for i in g['items']]}
                for g in section.grouped_items()
            ] if section else []
            syllabus_cards.append({
                'stage': p.stage,
                'section': section,
                'groups': groups,
                'columns': split_syllabus_columns(groups),
            })

        return render(request, 'portal/syllabus.html', {
            'active': 'syllabus',
            'member': member,
            'org': member.organisation,
            'syllabus_cards': syllabus_cards,
            'progressions': progressions,
        })


# Members' own uploads: a portal link is the only key, so keep them small and sensible.
PORTAL_UPLOAD_MAX_BYTES = 10 * 1024 * 1024
PORTAL_UPLOAD_EXTENSIONS = {'.pdf', '.jpg', '.jpeg', '.png', '.heic', '.doc', '.docx'}
PORTAL_UPLOAD_LIMIT = 20


class PortalDocumentUploadView(View):
    """A member adds a document (e.g. a medical letter) to their own record. Staff see it marked "From member"."""

    def post(self, request, token):
        import os
        import secrets

        from documents.models import Document

        member = _portal_member(token, 'documents')
        f = request.FILES.get('file')
        # The upload form is on the member's My details page.
        back = redirect(reverse('portal_my_details', kwargs={'token': token}) + '#documents')
        if not f:
            messages.error(request, 'Choose a file to upload.')
            return back
        ext = os.path.splitext(f.name)[1].lower()
        if ext not in PORTAL_UPLOAD_EXTENSIONS:
            messages.error(request, 'Upload a PDF, photo (JPG, PNG or HEIC) or Word document.')
            return back
        if f.size > PORTAL_UPLOAD_MAX_BYTES:
            messages.error(request, 'That file is too big. The limit is 10 MB.')
            return back
        if member.documents.filter(uploaded_by_member=True).count() >= PORTAL_UPLOAD_LIMIT:
            messages.error(request, f'You can upload up to {PORTAL_UPLOAD_LIMIT} documents. Contact the club to add more.')
            return back
        category = request.POST.get('category', Document.Category.OTHER)
        if category not in Document.Category.values:
            category = Document.Category.OTHER
        name = (request.POST.get('name', '').strip() or f.name)[:255]
        # Stored under a random name: the member's own file name (often theirs or
        # their child's) stays only in the document's name, never in a path.
        f.name = f'{secrets.token_urlsafe(16)}{ext}'
        Document.objects.create(member=member, name=name, category=category, file=f, uploaded_by_member=True)
        messages.success(request, f'"{name}" uploaded. The club can see it now.')
        return back


class PortalDocumentDownloadView(View):
    """A member downloads one of the documents on their own record."""

    def get(self, request, token, pk):
        from django.http import FileResponse, Http404
        from documents.models import Document

        member = _portal_member(token, 'documents')
        doc = get_object_or_404(Document, pk=pk, member=member)
        try:
            return FileResponse(doc.file.open('rb'), as_attachment=True, filename=doc.download_name)
        except FileNotFoundError:
            raise Http404 from None


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
        member = _portal_member(token)

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
        member = _portal_member(token)

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

        member = _portal_member(token, 'billing')
        from .family import family_members
        invoice = get_object_or_404(Invoice, pk=invoice_pk, member__in=family_members(member, active_only=False),
                                    status='unpaid')

        keys = stripe_keys(member.organisation)
        if keys is None:
            return redirect('member_portal', token=token)

        site_url = settings.SITE_URL.rstrip('/')
        portal_url = f"{site_url}/p/{token}/"

        session = stripe.checkout.Session.create(
            api_key=keys.secret,
            line_items=[{
                'price_data': {
                    'currency': 'gbp',
                    'unit_amount': int(invoice.amount * 100),
                    'product_data': {
                        'name': f"{member.organisation.name} — {invoice.period}",
                        'description': f"Membership fee for {invoice.member.name}",
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


# Monthly Stripe subscriptions, started from the portal. DojoUK replaced these
# with automatic payments (below) and removed them; this fork keeps both.
# They use the same Stripe keys as card payments (Settings → Payments, or the
# STRIPE_* environment variables for a club that hasn't set any there).

class CreateSubscriptionView(View):
    def post(self, request, token):
        import stripe

        member = _portal_member(token, 'billing')
        keys = stripe_keys(member.organisation)
        if keys is None or not member.monthly_fee:
            return redirect('member_portal', token=token)

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
                api_key=keys.secret,
                email=customer_email,
                name=member.name,
                metadata={'member_pk': str(member.pk)},
            )
            member.stripe_customer_id = customer.id
            member.save(update_fields=['stripe_customer_id'])
            customer_id = customer.id

        session = stripe.checkout.Session.create(
            api_key=keys.secret,
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

        member = _portal_member(token, 'billing')
        keys = stripe_keys(member.organisation)
        if not member.stripe_customer_id or keys is None:
            return redirect('member_portal', token=token)

        site_url = settings.SITE_URL.rstrip('/')
        portal_url = f"{site_url}/p/{token}/"

        session = stripe.billing_portal.Session.create(
            api_key=keys.secret,
            customer=member.stripe_customer_id,
            return_url=portal_url,
        )

        return redirect(session.url)


class CancelSubscriptionView(View):
    def post(self, request, token):
        import stripe

        member = _portal_member(token, 'billing')
        keys = stripe_keys(member.organisation)
        if not member.stripe_subscription_id or keys is None:
            return redirect('member_portal', token=token)

        stripe.Subscription.modify(
            member.stripe_subscription_id,
            api_key=keys.secret,
            cancel_at_period_end=True,
        )
        member.subscription_status = 'cancelling'
        member.save(update_fields=['subscription_status'])

        return redirect('member_portal', token=token)


class AutoPaySetupView(View):
    """Sends the member to Stripe to save a card or set up a Direct Debit for automatic payments."""

    def post(self, request, token):
        from billing import autopay
        member = _portal_member(token, 'billing')
        portal_url = request.build_absolute_uri(reverse('member_portal', kwargs={'token': token}))
        if not autopay.enabled(member.organisation):
            return redirect('member_portal', token=token)
        import stripe
        try:
            return redirect(autopay.start_setup(member, portal_url))
        except stripe.error.StripeError:
            return redirect(reverse('member_portal', kwargs={'token': token}) + '?payment_error=1')


class AutoPayOffView(View):
    def post(self, request, token):
        from billing import autopay
        member = _portal_member(token, 'billing')
        autopay.turn_off(member)
        return redirect(reverse('member_portal', kwargs={'token': token}) + '?autopay_off=1')
