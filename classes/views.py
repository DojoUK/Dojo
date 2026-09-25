import json
from datetime import date, timedelta

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from django.views.generic import DetailView, ListView

from dojo.mixins import ClassCoachMixin, OrgAdminMixin, OrgMixin
from members.models import Member

from .models import (
    Attendance, Class, ClassCoach, ClassHelper, ClassMember, Session, SessionCoach, SessionHelper,
    WaitingList,
)


DAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
DAYS_JSON = json.dumps(DAYS)


def _coaches_and_holiday_split(assigned_class, on_date):
    """
    Split the coaches assigned to a class into those available for a given
    session date and those excluded because they're on holiday that date.

    Returns (available_coaches_qs, excluded_coaches_list) — the excluded list
    holds ClassCoach instances so callers can show who's away and why.
    """
    from organisations.models import StaffHoliday

    all_coaches = (
        ClassCoach.objects.filter(assigned_class=assigned_class)
        .select_related('user')
        .order_by('user__first_name', 'user__last_name')
    )
    holiday_user_ids = set(
        StaffHoliday.objects.filter(
            member__organisation=assigned_class.organisation,
            start_date__lte=on_date,
            end_date__gte=on_date,
        ).values_list('member__user_id', flat=True)
    )
    if not holiday_user_ids:
        return all_coaches, []
    available = all_coaches.exclude(user_id__in=holiday_user_ids)
    excluded = [cc for cc in all_coaches if cc.user_id in holiday_user_ids]
    return available, excluded


def _attach_photo_consent(org, enrolled):
    """
    Sets `.member.photo_consent` on each ClassMember in `enrolled` (a list
    or queryset with `.member` selected) from the org's "Photo consent"
    boolean custom field, if one exists. Used so registers can show a
    camera emoji next to consenting members — visible to coaches too, not
    just admins, since it's the coach taking photos at the session.
    """
    from members.models import CustomField

    enrolled = list(enrolled)
    photo_consent_field = CustomField.objects.filter(
        organisation=org, field_type=CustomField.FieldType.BOOLEAN, name__iexact='Photo consent',
    ).first()
    for cm in enrolled:
        cm.member.photo_consent = bool(
            photo_consent_field and cm.member.custom_field_values.get(str(photo_consent_field.pk))
        )
    return enrolled


def _add_former_members_with_attendance(assigned_class, session, enrolled):
    """
    Extends `enrolled` (currently-enrolled ClassMembers for this class) with anyone
    who has an Attendance row for this specific session but has since been removed
    from the class — so past registers keep showing who was actually marked present
    or absent, even after they leave. Attendance is keyed directly to Member, not
    ClassMember, so this history already survives unenrolment in the database; it
    was just being filtered out of the register view. Only sessions that already
    have recorded attendance pull in former members, so upcoming/untaken registers
    are unaffected.
    """
    enrolled = list(enrolled)
    for cm in enrolled:
        cm.is_former_member = False

    current_member_ids = {cm.member_id for cm in enrolled}
    former_member_ids = list(
        Attendance.objects.filter(session=session)
        .exclude(member_id__in=current_member_ids)
        .values_list('member_id', flat=True)
    )
    if former_member_ids:
        for member in Member.objects.filter(pk__in=former_member_ids):
            ghost = ClassMember(assigned_class=assigned_class, member=member)
            ghost.is_former_member = True
            enrolled.append(ghost)
        enrolled.sort(key=lambda cm: (cm.member.name or '').lower())
    return enrolled


def _attach_coach_emergency_contacts(org, coaches):
    """
    Sets `.emergency_contact_name` / `.emergency_contact_phone` /
    `.emergency_contact_2_name` / `.emergency_contact_2_phone` on each
    ClassCoach in `coaches` (a list or queryset with `.user` selected), pulled
    from the matching OrganisationMember. Mirrors how member emergency
    contacts already flow straight into the register from the Member model,
    so coaches get the same treatment there.
    """
    from organisations.models import OrganisationMember

    coaches = list(coaches)
    user_ids = [cc.user_id for cc in coaches]
    om_by_user_id = {
        om.user_id: om
        for om in OrganisationMember.objects.filter(organisation=org, user_id__in=user_ids)
    }
    for cc in coaches:
        om = om_by_user_id.get(cc.user_id)
        cc.emergency_contact_name = om.emergency_contact_name if om else ''
        cc.emergency_contact_phone = om.emergency_contact_phone if om else ''
        cc.emergency_contact_2_name = om.emergency_contact_2_name if om else ''
        cc.emergency_contact_2_phone = om.emergency_contact_2_phone if om else ''
    return coaches


def _class_form_class(org=None):
    from django import forms
    from billing.models import BillingPolicy

    class ClassForm(forms.ModelForm):
        class Meta:
            model = Class
            fields = ['name', 'description', 'max_capacity', 'billing_policy']
            widgets = {'description': forms.Textarea(attrs={'rows': 3})}

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            for field in self.fields.values():
                field.widget.attrs['class'] = 'form-control'
            if org:
                self.fields['billing_policy'].queryset = BillingPolicy.objects.filter(
                    organisation=org, is_active=True
                )
            self.fields['billing_policy'].required = False
            self.fields['billing_policy'].empty_label = '— No policy —'

    return ClassForm


def _parse_schedule(post_data):
    schedule = []
    days = post_data.getlist('schedule_day')
    times = post_data.getlist('schedule_time')
    ends = post_data.getlist('schedule_end')
    for i, (day, time) in enumerate(zip(days, times)):
        try:
            d = int(day)
            if 0 <= d <= 6 and time:
                entry = {'day': d, 'time': time}
                end = ends[i] if i < len(ends) else ''
                if end:
                    entry['end'] = end
                schedule.append(entry)
        except (ValueError, TypeError):
            pass
    return schedule


class ClassListView(OrgAdminMixin, ListView):
    template_name = 'classes/list.html'
    context_object_name = 'classes'

    def get_queryset(self):
        return (
            Class.objects.filter(organisation=self.org)
            .prefetch_related('enrolments', 'coaches')
            .order_by('name')
        )


class ClassCreateView(OrgAdminMixin, View):
    def get(self, request, org_slug):
        form = _class_form_class(self.org)()
        return render(request, 'classes/form.html', {
            'org': self.org, 'org_membership': self.org_membership,
            'form': form, 'title': 'Add class', 'days': DAYS, 'days_json': DAYS_JSON, 'schedule': [],
        })

    def post(self, request, org_slug):
        FormClass = _class_form_class(self.org)
        form = FormClass(request.POST)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.organisation = self.org
            obj.schedule = _parse_schedule(request.POST)
            obj.save()
            messages.success(request, f'"{obj.name}" created.')
            return redirect('class_detail', org_slug=self.org.slug, pk=obj.pk)
        return render(request, 'classes/form.html', {
            'org': self.org, 'org_membership': self.org_membership,
            'form': form, 'title': 'Add class', 'days': DAYS, 'days_json': DAYS_JSON,
            'schedule': _parse_schedule(request.POST),
        })


class ClassUpdateView(OrgAdminMixin, View):
    def get_class(self, pk):
        return get_object_or_404(Class, pk=pk, organisation=self.org)

    def get(self, request, org_slug, pk):
        cls = self.get_class(pk)
        form = _class_form_class(self.org)(instance=cls)
        return render(request, 'classes/form.html', {
            'org': self.org, 'org_membership': self.org_membership,
            'form': form, 'title': f'Edit {cls.name}',
            'days': DAYS, 'days_json': DAYS_JSON, 'schedule': cls.schedule or [], 'cls': cls,
        })

    def post(self, request, org_slug, pk):
        cls = self.get_class(pk)
        FormClass = _class_form_class(self.org)
        form = FormClass(request.POST, instance=cls)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.schedule = _parse_schedule(request.POST)
            obj.save()
            messages.success(request, f'"{obj.name}" updated.')
            return redirect('class_detail', org_slug=self.org.slug, pk=obj.pk)
        return render(request, 'classes/form.html', {
            'org': self.org, 'org_membership': self.org_membership,
            'form': form, 'title': f'Edit {cls.name}',
            'days': DAYS, 'days_json': DAYS_JSON, 'schedule': _parse_schedule(request.POST), 'cls': cls,
        })


class ClassDetailView(OrgAdminMixin, DetailView):
    template_name = 'classes/detail.html'
    context_object_name = 'cls'

    def get_object(self):
        return get_object_or_404(Class, pk=self.kwargs['pk'], organisation=self.org)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        enrolled_ids = list(self.object.enrolments.values_list('member_id', flat=True))
        enrolled = list(
            Member.objects.filter(pk__in=enrolled_ids)
            .order_by('name')
        )
        from members.models import CustomField
        photo_consent_field = CustomField.objects.filter(
            organisation=self.org, field_type=CustomField.FieldType.BOOLEAN, name__iexact='Photo consent',
        ).first()
        for m in enrolled:
            m.photo_consent = bool(
                photo_consent_field and m.custom_field_values.get(str(photo_consent_field.pk))
            )
        from progression.models import MemberProgression, ProgressionSystem
        backwell_system = ProgressionSystem.objects.filter(
            organisation=self.org, name__iexact='Backwell Grading',
        ).first()
        latest_progression_by_member = {}
        if backwell_system:
            for mp in (
                MemberProgression.objects.filter(member_id__in=enrolled_ids, stage__system=backwell_system)
                .select_related('stage')
                .order_by('member_id', '-achieved_date')
            ):
                latest_progression_by_member.setdefault(mp.member_id, mp)
        for m in enrolled:
            m.latest_progression = latest_progression_by_member.get(m.pk)
        context['enrolled'] = enrolled
        context['available'] = (
            Member.objects.filter(organisation=self.org, is_active=True)
            .exclude(pk__in=enrolled_ids)
            .order_by('name')
        )
        coaches = list(self.object.coaches.select_related('user').order_by(
            'user__first_name', 'user__last_name', 'user__username'
        ))
        coach_user_ids = [c.user_id for c in coaches]
        from organisations.models import OrganisationMember
        om_by_user_id = {
            om.user_id: om
            for om in OrganisationMember.objects.filter(
                organisation=self.org, user_id__in=coach_user_ids
            )
        }
        for c in coaches:
            om = om_by_user_id.get(c.user_id)
            c.emergency_contact_name = om.emergency_contact_name if om else ''
            c.emergency_contact_phone = om.emergency_contact_phone if om else ''
        context['coaches'] = coaches
        context['available_coaches'] = (
            OrganisationMember.objects.filter(organisation=self.org)
            .exclude(user_id__in=coach_user_ids)
            .select_related('user')
        )
        helpers = list(self.object.helpers.select_related('member').order_by('member__name'))
        helper_member_ids = [h.member_id for h in helpers]
        context['helpers'] = helpers
        context['available_helpers'] = (
            Member.objects.filter(organisation=self.org, is_active=True)
            .exclude(pk__in=helper_member_ids)
            .order_by('name')
        )
        from django.db.models import Count, Q
        context['sessions'] = self.object.sessions.select_related('leader').annotate(
            present_count=Count('attendance', filter=Q(attendance__present=True))
        ).order_by('-date')[:10]
        context['days'] = DAYS
        context['waiting_list'] = self.object.waiting_list.select_related('member')
        return context


def _redirect_after_class_action(request, org, cls):
    """
    Where to send the browser after enrolling a member / adding a coach or
    helper. The quick-add controls on the register page post next=register
    plus the session_pk they were opened from, so the coach taking that
    register lands back where they were instead of the org-admin-only class
    page they may not even have access to. Falls back to the admin class
    page, which is what the original add forms there still rely on.
    """
    session_pk = request.POST.get('session_pk')
    if request.POST.get('next') == 'register' and session_pk:
        return redirect('session_register', org_slug=org.slug, pk=cls.pk, session_pk=session_pk)
    return redirect('class_detail', org_slug=org.slug, pk=cls.pk)


class EnrolMemberView(ClassCoachMixin, View):
    def post(self, request, org_slug, pk):
        cls = self.assigned_class
        member = get_object_or_404(Member, pk=request.POST.get('member_id'), organisation=self.org)
        already_enrolled = ClassMember.objects.filter(assigned_class=cls, member=member).exists()
        if already_enrolled:
            messages.info(request, f'{member.name} is already enrolled.')
        elif cls.is_full:
            WaitingList.objects.get_or_create(assigned_class=cls, member=member)
            messages.warning(request, f'{cls.name} is full — {member.name} added to the waiting list.')
        else:
            WaitingList.objects.filter(assigned_class=cls, member=member).delete()
            ClassMember.objects.create(assigned_class=cls, member=member)
            messages.success(request, f'{member.name} enrolled.')
        return _redirect_after_class_action(request, self.org, cls)


class UnenrolMemberView(OrgAdminMixin, View):
    def post(self, request, org_slug, pk, member_pk):
        cls = get_object_or_404(Class, pk=pk, organisation=self.org)
        member = get_object_or_404(Member, pk=member_pk, organisation=self.org)
        ClassMember.objects.filter(assigned_class=cls, member=member).delete()
        messages.success(request, f'{member.name} removed.')
        # Promote first person from waiting list if there is one
        next_up = WaitingList.objects.filter(assigned_class=cls).select_related('member').first()
        if next_up:
            ClassMember.objects.create(assigned_class=cls, member=next_up.member)
            next_up.delete()
            messages.info(request, f'{next_up.member.name} has been moved from the waiting list into the class.')
            self._notify_waitlist_promoted(request, next_up.member, cls)
        return redirect('class_detail', org_slug=self.org.slug, pk=cls.pk)


    def _notify_waitlist_promoted(self, request, member, cls):
        from django.conf import settings
        from django.core.mail import EmailMultiAlternatives
        has_guardians = member.guardians.exists()
        if has_guardians:
            guardian = member.guardians.filter(email__gt='').first()
            recipient = guardian.email if guardian else None
        else:
            recipient = member.email or None
        if not recipient:
            return
        org_name = self.org.name
        subject = f"A spot has opened up — {cls.name} ({org_name})"
        greeting = f"Dear guardian of {member.name}" if has_guardians else f"Hi {member.name}"
        contact_line = (
            f"To stop receiving non-essential emails like this, contact {self.org.email}."
            if self.org.email else
            f"To stop receiving non-essential emails like this, contact {org_name} directly."
        )
        body = (
            f"{greeting},\n\n"
            f"Great news! A spot has opened up in {cls.name} at {org_name} "
            f"and {member.name} has been moved off the waiting list and into the class.\n\n"
            f"No action is needed — you're all set.\n\n"
            f"Thanks,\n{org_name}\n\n"
            f"{contact_line}"
        )
        try:
            EmailMultiAlternatives(
                subject=subject, body=body,
                from_email=settings.DEFAULT_FROM_EMAIL, to=[recipient],
            ).send()
        except Exception:
            pass


class RemoveFromWaitingListView(OrgAdminMixin, View):
    def post(self, request, org_slug, pk, member_pk):
        cls = get_object_or_404(Class, pk=pk, organisation=self.org)
        WaitingList.objects.filter(assigned_class=cls, member_id=member_pk).delete()
        messages.success(request, 'Removed from waiting list.')
        return redirect('class_detail', org_slug=self.org.slug, pk=cls.pk)


class GenerateSessionsView(ClassCoachMixin, View):
    def post(self, request, org_slug, pk):
        cls = self.assigned_class

        # Whitelisted, not passed straight through — POST data shouldn't pick arbitrary URL names.
        redirect_to = 'class_detail' if request.POST.get('next') == 'class_detail' else 'coach_class_detail'

        if not cls.schedule:
            messages.warning(request, 'This class has no schedule set.')
            return redirect(redirect_to, org_slug=self.org.slug, pk=cls.pk)

        try:
            weeks = max(1, min(int(request.POST.get('weeks', 8)), 52))
        except (ValueError, TypeError):
            weeks = 8

        from_date = date.today()
        to_date = from_date + timedelta(weeks=weeks)
        created = 0
        current = from_date
        while current < to_date:
            for entry in cls.schedule:
                if current.weekday() == entry['day']:
                    _, was_new = Session.objects.get_or_create(
                        assigned_class=cls, date=current,
                        defaults={'leader': cls.default_leader},
                    )
                    if was_new:
                        created += 1
            current += timedelta(days=1)

        messages.success(request, f'{created} session{"s" if created != 1 else ""} generated.')
        return redirect(redirect_to, org_slug=self.org.slug, pk=cls.pk)


class AttendanceRegisterView(ClassCoachMixin, View):
    def _get_session(self, session_pk):
        return get_object_or_404(
            Session.objects.select_related('leader'), pk=session_pk, assigned_class=self.assigned_class,
        )

    def _unsigned_waiver_ids(self, enrolled):
        from documents.models import SignedWaiver, WaiverTemplate
        has_required_waivers = WaiverTemplate.objects.filter(
            organisation=self.org, is_active=True, is_required=True
        ).exists()
        if not has_required_waivers:
            return set()
        member_ids = [cm.member.pk for cm in enrolled]
        signed_ids = set(
            SignedWaiver.objects.filter(
                member_id__in=member_ids,
                template__organisation=self.org,
                template__is_required=True,
            ).values_list('member_id', flat=True)
        )
        return set(member_ids) - signed_ids

    def _render(self, request, session, enrolled, present_ids, coaches, present_coach_ids, notes, coaches_on_holiday=None, leader_id=..., helpers=None, present_helper_ids=None):
        coaches = _attach_coach_emergency_contacts(self.org, coaches)
        # Pre-select the class's usual leader when this session doesn't have one of
        # its own yet — still just a suggestion, since saving still requires them to
        # be marked present. Callers re-rendering after a failed submission pass the
        # leader the user actually picked instead, so it isn't lost from the form.
        if leader_id is ...:
            leader_id = session.leader_id or self.assigned_class.default_leader_id
        if helpers is None:
            helpers = self._get_helpers()
        return render(request, 'classes/register.html', {
            'org': self.org,
            'org_membership': self.org_membership,
            'cls': self.assigned_class,
            'session': session,
            'enrolled': enrolled,
            'present_ids': present_ids,
            'coaches': coaches,
            'present_coach_ids': present_coach_ids,
            'unsigned_waiver_ids': self._unsigned_waiver_ids(enrolled),
            'notes': notes,
            'coaches_on_holiday': coaches_on_holiday or [],
            'leader_id': leader_id,
            'helpers': helpers,
            'present_helper_ids': present_helper_ids or set(),
            'available_members': self._get_available_members(),
            'available_coaches': self._get_available_coaches(),
            'available_helpers': self._get_available_helpers(),
        })

    def _get_available_members(self):
        # Active org members not currently enrolled — offered on the register so
        # whoever's taking it can enrol someone on the spot, not just admins.
        return (
            Member.objects.filter(organisation=self.org, is_active=True)
            .exclude(enrolments__assigned_class=self.assigned_class)
            .order_by('name')
        )

    def _get_available_coaches(self):
        from organisations.models import OrganisationMember
        assigned_coach_user_ids = ClassCoach.objects.filter(
            assigned_class=self.assigned_class
        ).values_list('user_id', flat=True)
        return (
            OrganisationMember.objects.filter(organisation=self.org)
            .exclude(user_id__in=assigned_coach_user_ids)
            .select_related('user')
        )

    def _get_available_helpers(self):
        return (
            Member.objects.filter(organisation=self.org, is_active=True)
            .exclude(helping_classes__assigned_class=self.assigned_class)
            .order_by('name')
        )

    def _get_helpers(self):
        return (
            ClassHelper.objects.filter(assigned_class=self.assigned_class)
            .select_related('member')
            .order_by('member__name')
        )

    def get(self, request, org_slug, pk, session_pk):
        session = self._get_session(session_pk)
        enrolled = (
            ClassMember.objects.filter(assigned_class=self.assigned_class)
            .select_related('member')
            .order_by('member__name')
        )
        enrolled = _attach_photo_consent(self.org, enrolled)
        enrolled = _add_former_members_with_attendance(self.assigned_class, session, enrolled)
        present_ids = set(
            Attendance.objects.filter(session=session, present=True)
            .values_list('member_id', flat=True)
        )
        coaches, coaches_on_holiday = _coaches_and_holiday_split(self.assigned_class, session.date)
        present_coach_ids = set(
            SessionCoach.objects.filter(session=session, present=True)
            .values_list('coach_id', flat=True)
        )
        helpers = self._get_helpers()
        present_helper_ids = set(
            SessionHelper.objects.filter(session=session, present=True)
            .values_list('helper_id', flat=True)
        )
        return self._render(
            request, session, enrolled, present_ids, coaches, present_coach_ids, session.notes,
            coaches_on_holiday, helpers=helpers, present_helper_ids=present_helper_ids,
        )

    def post(self, request, org_slug, pk, session_pk):
        session = self._get_session(session_pk)
        enrolled = ClassMember.objects.filter(assigned_class=self.assigned_class).select_related('member')
        enrolled = _attach_photo_consent(self.org, enrolled)
        enrolled = _add_former_members_with_attendance(self.assigned_class, session, enrolled)
        present_ids = {int(x) for x in request.POST.getlist('present')}
        coaches, coaches_on_holiday = _coaches_and_holiday_split(self.assigned_class, session.date)
        coach_present_ids = {int(x) for x in request.POST.getlist('coach_present')}
        helpers = self._get_helpers()
        helper_present_ids = {int(x) for x in request.POST.getlist('helper_present')}
        notes = request.POST.get('notes', session.notes)

        leader_raw = request.POST.get('leader', '').strip()
        submitted_leader_id = None
        if leader_raw:
            try:
                submitted_leader_id = int(leader_raw)
            except ValueError:
                submitted_leader_id = None

        if coaches.exists() and not coach_present_ids:
            messages.error(request, 'At least one coach must be marked present to save the register.')
            return self._render(request, session, enrolled, present_ids, coaches, coach_present_ids, notes, coaches_on_holiday, leader_id=submitted_leader_id, helpers=helpers, present_helper_ids=helper_present_ids)

        if leader_raw and submitted_leader_id not in coach_present_ids:
            messages.error(request, 'The session leader must be one of the coaches marked present.')
            return self._render(request, session, enrolled, present_ids, coaches, coach_present_ids, notes, coaches_on_holiday, leader_id=submitted_leader_id, helpers=helpers, present_helper_ids=helper_present_ids)

        leader_id = submitted_leader_id

        for cm in enrolled:
            Attendance.objects.update_or_create(
                session=session,
                member=cm.member,
                defaults={'present': cm.member.pk in present_ids},
            )

        for cc in coaches:
            SessionCoach.objects.update_or_create(
                session=session,
                coach=cc.user,
                defaults={'present': cc.user.pk in coach_present_ids},
            )

        for ch in helpers:
            SessionHelper.objects.update_or_create(
                session=session,
                helper=ch.member,
                defaults={'present': ch.member_id in helper_present_ids},
            )

        session.notes = notes
        session.leader_id = leader_id
        session.save(update_fields=['notes', 'leader'])

        messages.success(request, f'Register saved for {session.date:%d %b %Y}.')
        return redirect('session_register', org_slug=self.org.slug, pk=self.assigned_class.pk, session_pk=session.pk)


class CoachClassListView(OrgMixin, ListView):
    template_name = 'classes/coach_list.html'
    context_object_name = 'classes'

    def get_queryset(self):
        if self.request.user.is_superuser or (
            self.org_membership and self.org_membership.role == 'org_admin'
        ):
            return Class.objects.filter(organisation=self.org).prefetch_related('sessions').order_by('name')
        return (
            Class.objects.filter(
                organisation=self.org,
                coaches__user=self.request.user,
            )
            .prefetch_related('sessions')
            .order_by('name')
        )


class CoachClassDetailView(ClassCoachMixin, View):
    def get(self, request, org_slug, pk):
        cls = self.assigned_class
        from datetime import date
        upcoming = cls.sessions.select_related('leader').filter(date__gte=date.today()).order_by('date')[:10]
        recent = cls.sessions.select_related('leader').filter(date__lt=date.today()).order_by('-date')[:5]
        enrolled = (
            ClassMember.objects.filter(assigned_class=cls)
            .select_related('member')
            .order_by('member__name')
        )
        coaches = ClassCoach.objects.filter(assigned_class=cls).select_related('user').order_by(
            'user__first_name', 'user__last_name', 'user__username'
        )
        return render(request, 'classes/coach_detail.html', {
            'org': self.org,
            'org_membership': self.org_membership,
            'cls': cls,
            'upcoming': upcoming,
            'recent': recent,
            'enrolled': enrolled,
            'coaches': coaches,
        })


class SetClassLeaderView(ClassCoachMixin, View):
    """Sets (or clears) Class.default_leader — the coach who normally runs this whole
    class series. Any coach assigned to the class can set this, not just admins, since
    it's meant to be a quick self-service "this is my class" designation. Carried onto
    newly generated sessions and used as the calendar fallback when an individual
    session has no leader of its own set."""

    def post(self, request, org_slug, pk):
        cls = self.assigned_class
        leader_raw = request.POST.get('default_leader', '').strip()

        # Whitelisted, not passed straight through — POST data shouldn't pick arbitrary URL names.
        redirect_to = 'class_detail' if request.POST.get('next') == 'class_detail' else 'coach_class_detail'

        if not leader_raw:
            cls.default_leader = None
            cls.save(update_fields=['default_leader'])
            messages.success(request, f'Cleared the default leader for {cls.name}.')
            return redirect(redirect_to, org_slug=self.org.slug, pk=cls.pk)

        try:
            leader_id = int(leader_raw)
        except ValueError:
            messages.error(request, 'Invalid coach selected.')
            return redirect(redirect_to, org_slug=self.org.slug, pk=cls.pk)

        coach = ClassCoach.objects.filter(assigned_class=cls, user_id=leader_id).select_related('user').first()
        if not coach:
            messages.error(request, 'That coach isn\'t assigned to this class.')
            return redirect(redirect_to, org_slug=self.org.slug, pk=cls.pk)

        cls.default_leader = coach.user
        cls.save(update_fields=['default_leader'])
        name = coach.user.get_full_name() or coach.user.username
        messages.success(request, f'{name} set as the default leader for {cls.name}.')
        return redirect(redirect_to, org_slug=self.org.slug, pk=cls.pk)


class AddCoachView(ClassCoachMixin, View):
    def post(self, request, org_slug, pk):
        from django.contrib.auth.models import User
        cls = self.assigned_class
        user_pk = request.POST.get('user_id')
        user = get_object_or_404(User, pk=user_pk)
        ClassCoach.objects.get_or_create(assigned_class=cls, user=user)
        messages.success(request, f'{user.get_full_name() or user.username} added as coach.')
        return _redirect_after_class_action(request, self.org, cls)


class RemoveCoachView(OrgAdminMixin, View):
    def post(self, request, org_slug, pk, coach_pk):
        cls = get_object_or_404(Class, pk=pk, organisation=self.org)
        coach = get_object_or_404(ClassCoach, pk=coach_pk, assigned_class=cls)
        name = coach.user.get_full_name() or coach.user.username
        coach.delete()
        messages.success(request, f'{name} removed as coach.')
        return redirect('class_detail', org_slug=self.org.slug, pk=cls.pk)


class AddHelperView(ClassCoachMixin, View):
    def post(self, request, org_slug, pk):
        cls = self.assigned_class
        member = get_object_or_404(Member, pk=request.POST.get('member_id'), organisation=self.org)
        _, created = ClassHelper.objects.get_or_create(assigned_class=cls, member=member)
        if created:
            messages.success(request, f'{member.name} added as a helper.')
        else:
            messages.info(request, f'{member.name} is already a helper for this class.')
        return _redirect_after_class_action(request, self.org, cls)


class RemoveHelperView(OrgAdminMixin, View):
    def post(self, request, org_slug, pk, helper_pk):
        cls = get_object_or_404(Class, pk=pk, organisation=self.org)
        helper = get_object_or_404(ClassHelper, pk=helper_pk, assigned_class=cls)
        name = helper.member.name
        helper.delete()
        messages.success(request, f'{name} removed as a helper.')
        return redirect('class_detail', org_slug=self.org.slug, pk=cls.pk)


class PrintRegisterView(ClassCoachMixin, View):
    def get(self, request, org_slug, pk, session_pk):
        session = get_object_or_404(Session, pk=session_pk, assigned_class=self.assigned_class)
        enrolled = ClassMember.objects.filter(
            assigned_class=self.assigned_class
        ).select_related('member').order_by('member__name')
        enrolled = _attach_photo_consent(self.org, enrolled)
        enrolled = _add_former_members_with_attendance(self.assigned_class, session, enrolled)
        present_ids = set(
            session.attendance.filter(present=True).values_list('member_id', flat=True)
        )
        coaches, coaches_on_holiday = _coaches_and_holiday_split(self.assigned_class, session.date)
        present_coach_ids = set(
            session.session_coaches.filter(present=True).values_list('coach_id', flat=True)
        )
        helpers = (
            ClassHelper.objects.filter(assigned_class=self.assigned_class)
            .select_related('member').order_by('member__name')
        )
        present_helper_ids = set(
            session.session_helpers.filter(present=True).values_list('helper_id', flat=True)
        )
        return render(request, 'classes/print_register.html', {
            'org': self.org,
            'cls': self.assigned_class,
            'session': session,
            'enrolled': enrolled,
            'present_ids': present_ids,
            'coaches': coaches,
            'present_coach_ids': present_coach_ids,
            'helpers': helpers,
            'present_helper_ids': present_helper_ids,
            'today': date.today(),
        })


class CancelSessionView(OrgAdminMixin, View):
    def post(self, request, org_slug, pk, session_pk):
        cls = get_object_or_404(Class, pk=pk, organisation=self.org)
        session = get_object_or_404(Session, pk=session_pk, assigned_class=cls)
        notify = request.POST.get('notify') == '1'

        was_cancelled = session.is_cancelled
        session.is_cancelled = not was_cancelled
        session.save(update_fields=['is_cancelled'])

        if session.is_cancelled:
            messages.success(request, f'Session on {session.date} marked as cancelled.')
            if notify:
                self._send_cancellation_emails(request, cls, session)
        else:
            messages.success(request, f'Session on {session.date} reinstated.')

        return redirect('class_detail', org_slug=self.org.slug, pk=cls.pk)

    def _send_cancellation_emails(self, request, cls, session):
        from django.core.mail import EmailMultiAlternatives
        from django.template.loader import render_to_string

        enrolled = (
            ClassMember.objects.filter(assigned_class=cls)
            .select_related('member')
            .prefetch_related('member__guardians')
        )
        sent = 0
        for cm in enrolled:
            member = cm.member
            has_guardians = member.guardians.exists()
            if has_guardians:
                guardian = member.guardians.filter(email__gt='').first()
                recipient = guardian.email if guardian else None
            else:
                recipient = member.email or None

            if not recipient:
                continue

            org_name = self.org.name
            subject = f'{org_name} — {cls.name} session cancelled ({session.date.strftime("%d %b %Y")})'
            context = {
                'org_name': org_name,
                'org_email': self.org.email,
                'class_name': cls.name,
                'session': session,
                'member': member,
                'has_guardians': has_guardians,
            }
            html_body = render_to_string('emails/session_cancelled.html', context)
            text_body = (
                f"{'Hi' if not has_guardians else 'Dear guardian of'} {member.name},\n\n"
                f"This is to let you know that the {cls.name} session on "
                f"{session.date.strftime('%d %b %Y')} has been cancelled.\n\n"
                f"— {org_name}"
            )
            msg = EmailMultiAlternatives(
                subject=subject, body=text_body,
                from_email=self.org.email or None,
                to=[recipient],
            )
            msg.attach_alternative(html_body, 'text/html')
            try:
                msg.send()
                sent += 1
            except Exception:
                pass

        if sent:
            messages.success(request, f'Cancellation notice sent to {sent} member{"s" if sent != 1 else ""}.')


class AttendanceAnalyticsView(OrgMixin, View):
    template_name = 'classes/attendance_analytics.html'

    def get(self, request, org_slug):
        from django.db.models import Count, Max, Q

        today = date.today()
        four_weeks_ago = today - timedelta(weeks=4)
        eight_weeks_ago = today - timedelta(weeks=8)
        two_weeks_ago = today - timedelta(weeks=2)

        class_pk = request.GET.get('class')
        sort = request.GET.get('sort', 'status')

        members = Member.objects.filter(organisation=self.org, is_active=True)

        if class_pk:
            members = members.filter(enrolments__assigned_class_id=class_pk)

        members = members.annotate(
            last_attended=Max(
                'attendance__session__date',
                filter=Q(attendance__present=True),
            ),
            sessions_4w=Count(
                'attendance',
                filter=Q(attendance__present=True, attendance__session__date__gte=four_weeks_ago),
            ),
            sessions_8w=Count(
                'attendance',
                filter=Q(attendance__present=True, attendance__session__date__gte=eight_weeks_ago),
            ),
        )

        def status_order(m):
            if not m.last_attended:
                return (3, m.name)
            if m.last_attended >= two_weeks_ago:
                return (0, m.name)
            if m.last_attended >= four_weeks_ago:
                return (1, m.name)
            return (2, m.name)

        sort_map = {
            'name': lambda m: m.name,
            'last_seen': lambda m: m.last_attended or date(2000, 1, 1),
            '-last_seen': lambda m: m.last_attended or date(2000, 1, 1),
            'recent': lambda m: -m.sessions_4w,
        }

        member_list = list(members)

        if sort == '-last_seen':
            member_list.sort(key=sort_map[sort], reverse=True)
        elif sort in sort_map:
            member_list.sort(key=sort_map[sort])
        else:
            member_list.sort(key=status_order)

        # Attach status label
        for m in member_list:
            if not m.last_attended:
                m.attendance_status = 'never'
            elif m.last_attended >= two_weeks_ago:
                m.attendance_status = 'active'
            elif m.last_attended >= four_weeks_ago:
                m.attendance_status = 'at_risk'
            else:
                m.attendance_status = 'absent'

        count_active = sum(1 for m in member_list if m.attendance_status == 'active')
        count_at_risk = sum(1 for m in member_list if m.attendance_status == 'at_risk')
        count_absent = sum(1 for m in member_list if m.attendance_status in ('absent', 'never'))

        # Total sessions run in the last 4 weeks across all org classes
        total_sessions_4w = Session.objects.filter(
            assigned_class__organisation=self.org,
            date__gte=four_weeks_ago,
            is_cancelled=False,
        ).count()

        classes = Class.objects.filter(organisation=self.org).order_by('name')

        # Attendance trend chart — last N non-cancelled, already-run sessions
        # of one class, filterable independently of the member-table class
        # filter above (falls back to it, then to the first class, so
        # there's something to show on first load).
        trend_class_pk = request.GET.get('trend_class') or class_pk
        if not trend_class_pk and classes.exists():
            trend_class_pk = classes.first().pk

        try:
            trend_count = int(request.GET.get('trend_count', 10))
        except (TypeError, ValueError):
            trend_count = 10
        if trend_count not in (10, 25, 50):
            trend_count = 10

        trend_labels, trend_data, trend_average = [], [], None
        if trend_class_pk:
            trend_sessions = list(
                Session.objects.filter(
                    assigned_class_id=trend_class_pk,
                    assigned_class__organisation=self.org,
                    is_cancelled=False,
                    date__lte=today,
                )
                .annotate(present_count=Count('attendance', filter=Q(attendance__present=True)))
                .order_by('-date')[:trend_count]
            )
            trend_sessions.reverse()  # oldest first, so the line reads left-to-right chronologically
            trend_labels = [s.date.strftime('%d %b') for s in trend_sessions]
            trend_data = [s.present_count for s in trend_sessions]
            if trend_data:
                trend_average = round(sum(trend_data) / len(trend_data), 1)

        return render(request, self.template_name, {
            'org': self.org,
            'org_membership': self.org_membership,
            'members': member_list,
            'count_active': count_active,
            'count_at_risk': count_at_risk,
            'count_absent': count_absent,
            'total_sessions_4w': total_sessions_4w,
            'classes': classes,
            'selected_class': class_pk,
            'sort': sort,
            'today': today,
            'four_weeks_ago': four_weeks_ago,
            'trend_selected_class': str(trend_class_pk) if trend_class_pk else '',
            'trend_count': trend_count,
            'trend_labels': json.dumps(trend_labels),
            'trend_data': json.dumps(trend_data),
            'trend_average': trend_average,
            'trend_has_data': bool(trend_data),
        })


class AttendanceExportView(OrgMixin, View):
    def get(self, request, org_slug):
        import csv
        from django.http import HttpResponse
        from .models import Session, Attendance

        date_from_raw = request.GET.get('date_from', '')
        date_to_raw = request.GET.get('date_to', '')
        class_pk = request.GET.get('class', '')

        sessions = Session.objects.filter(
            assigned_class__organisation=self.org,
            is_cancelled=False,
        ).select_related('assigned_class').order_by('date')

        if date_from_raw:
            try:
                sessions = sessions.filter(date__gte=date.fromisoformat(date_from_raw))
            except ValueError:
                pass
        if date_to_raw:
            try:
                sessions = sessions.filter(date__lte=date.fromisoformat(date_to_raw))
            except ValueError:
                pass
        if class_pk:
            sessions = sessions.filter(assigned_class_id=class_pk)

        attendance = (
            Attendance.objects.filter(session__in=sessions)
            .select_related('member', 'session', 'session__assigned_class')
            .order_by('session__date', 'session__assigned_class__name', 'member__name')
        )

        response = HttpResponse(content_type='text/csv')
        label_parts = []
        if date_from_raw:
            label_parts.append(date_from_raw)
        if date_to_raw:
            label_parts.append(date_to_raw)
        suffix = '-'.join(label_parts) or 'all'
        response['Content-Disposition'] = f'attachment; filename="{self.org.slug}-attendance-{suffix}.csv"'

        writer = csv.writer(response)
        writer.writerow(['Date', 'Class', 'Member', 'Present'])
        for a in attendance:
            writer.writerow([
                a.session.date.isoformat(),
                a.session.assigned_class.name,
                a.member.name,
                'Yes' if a.present else 'No',
            ])
        return response
