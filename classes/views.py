import json
from datetime import date, timedelta

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from django.views.generic import DetailView, ListView

from dojo.mixins import ClassCapabilityMixin, ClassCoachMixin, OrgAdminMixin, OrgMixin
from members.models import Member

from .models import (
    Attendance, Class, ClassCoach, ClassHelper, ClassMember, Session, SessionCoach, SessionHelper,
    WaitingList,
)
from .register import (
    attach_member_contacts, attach_register_flags, attach_staff_contacts, register_enrolments,
    with_former_members,
)
from mailer import blocks as email_blocks
from mailer import compose


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


def _photo_consent_field(org):
    """The club's "Photo consent" yes/no custom field, if it has one."""
    from members.models import CustomField
    return CustomField.objects.filter(
        organisation=org, field_type=CustomField.FieldType.BOOLEAN, name__iexact='Photo consent',
    ).first()


def _attach_member_photo_consent(org, members):
    """
    Sets `.photo_consent` on each member from the club's "Photo consent"
    yes/no field, so registers and class pages can show a camera next to
    members who've given it. Visible to coaches too, since it's the coach
    taking photos at the session. If the club has put that field on
    registers (DojoUK's register badges), the badge shows instead. Returns the members.
    """
    field = _photo_consent_field(org)
    if field and field.show_on_registers:
        field = None  # already shown as a register badge (Settings → Custom fields), so no camera too
    for member in members:
        member.photo_consent = bool(field and (member.custom_field_values or {}).get(str(field.pk)))
    return members


def _attach_photo_consent(org, enrolled):
    """`_attach_member_photo_consent` for a list of ClassMember rows. Returns them as a list."""
    enrolled = list(enrolled)
    _attach_member_photo_consent(org, [cm.member for cm in enrolled])
    return enrolled


def _class_form_class(org=None):
    from django import forms
    from billing.models import BillingPolicy

    class ClassForm(forms.ModelForm):
        class Meta:
            model = Class
            fields = ['name', 'description', 'is_active', 'min_age', 'max_age', 'min_stage', 'max_stage',
                      'max_capacity', 'billing_policy']
            widgets = {'description': forms.Textarea(attrs={'rows': 3})}

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            for name, field in self.fields.items():
                field.widget.attrs['class'] = 'form-check-input' if name == 'is_active' else 'form-control'
            for name in ('min_age', 'max_age'):
                self.fields[name].widget.attrs.update({'min': 0, 'max': 120})
            if org and org.has_feature('grades'):
                from progression.models import ProgressionStage
                stages = ProgressionStage.objects.filter(system__organisation=org).select_related('system')
                for name in ('min_stage', 'max_stage'):
                    self.fields[name].queryset = stages
                    self.fields[name].empty_label = '— No limit —'
                    self.fields[name].widget.attrs['class'] = 'form-select'
            else:
                del self.fields['min_stage'], self.fields['max_stage']
            if org and not org.has_feature('waiting_lists'):
                del self.fields['max_capacity']
            if org and not org.has_feature('billing'):
                del self.fields['billing_policy']
            if 'billing_policy' in self.fields:
                if org:
                    self.fields['billing_policy'].queryset = BillingPolicy.objects.filter(
                        organisation=org, is_active=True
                    )
                self.fields['billing_policy'].required = False
                self.fields['billing_policy'].empty_label = '— No policy —'

        def clean(self):
            data = super().clean()
            low, high = data.get('min_age'), data.get('max_age')
            if low is not None and high is not None and low > high:
                self.add_error('max_age', 'The oldest age can\'t be below the youngest age.')
            lo_s, hi_s = data.get('min_stage'), data.get('max_stage')
            if lo_s and hi_s:
                if lo_s.system_id != hi_s.system_id:
                    self.add_error('max_stage', 'Choose both grades from the same progression system.')
                elif lo_s.order > hi_s.order:
                    self.add_error('max_stage', 'The highest grade can\'t be below the lowest grade.')
            return data

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
        enrolled = attach_register_flags(self.org, list(Member.objects.filter(pk__in=enrolled_ids).order_by('name')))
        _attach_member_photo_consent(self.org, enrolled)
        if self.org.has_feature('grades'):
            from progression.gradings import attach_current_grades
            attach_current_grades(enrolled)
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
        context['waiting_list'] = (
            self.object.waiting_list.select_related('member')
            if self.org.has_feature('waiting_lists') else []
        )
        return context


def _roster_choices(view, cls):
    """
    Who can be added to a class from the coach's class page and the
    register: {'available_members', 'available_coaches'}, both empty unless
    this person may enrol members.
    """
    if not view.can('manage_class_members'):
        return {'available_members': [], 'available_coaches': [], 'available_helpers': []}
    from organisations.models import OrganisationMember
    return {
        # Class helpers (members who help run the class), from this fork.
        'available_helpers': (
            Member.objects.filter(organisation=view.org, is_active=True)
            .exclude(helping_classes__assigned_class=cls)
            .order_by('name')
        ),
        'available_members': (
            Member.objects.filter(organisation=view.org, is_active=True)
            .exclude(enrolments__assigned_class=cls)
            .order_by('name')
        ),
        'available_coaches': (
            OrganisationMember.objects.filter(organisation=view.org)
            .exclude(user__coached_classes__assigned_class=cls)
            .select_related('user')
            .order_by('user__first_name', 'user__last_name', 'user__username')
        ),
    }


def _back_to_class(view, request, cls):
    """
    Where a class action returns to: the register it was taken from (the
    quick-add controls post next=register and the session), otherwise the
    class page this person can see.
    """
    session_pk = request.POST.get('session_pk', '')
    if request.POST.get('next') == 'register' and session_pk.isdigit():
        if Session.objects.filter(pk=session_pk, assigned_class=cls).exists():
            return redirect('session_register', org_slug=view.org.slug, pk=cls.pk, session_pk=session_pk)
    if view.is_org_admin:
        return redirect('class_detail', org_slug=view.org.slug, pk=cls.pk)
    return redirect('coach_class_detail', org_slug=view.org.slug, pk=cls.pk)


class EnrolMemberView(ClassCapabilityMixin, View):
    required_capabilities = ('manage_class_members',)

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
        return _back_to_class(self, request, cls)


class SetAutoEnrolView(ClassCapabilityMixin, View):
    """Staff choose whether an enrolled member is on every register for the class (on), or only on the
    registers of sessions they book themselves (off)."""
    required_capabilities = ('manage_class_members',)

    def post(self, request, org_slug, pk, member_pk):
        cls = self.assigned_class
        enrolment = get_object_or_404(ClassMember, assigned_class=cls, member_id=member_pk, member__organisation=self.org)
        enrolment.auto_enrol = request.POST.get('auto_enrol') == '1'
        enrolment.save(update_fields=['auto_enrol'])
        if enrolment.auto_enrol:
            messages.success(request, f'{enrolment.member.name} is now on every {cls.name} register.')
        else:
            messages.success(request, f'{enrolment.member.name} will only be on registers for sessions they book.')
        if request.POST.get('next') == 'member':
            return redirect('member_detail', org_slug=self.org.slug, pk=member_pk)
        return _back_to_class(self, request, cls)


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
        has_guardians = member.guardians.exists()
        if has_guardians:
            guardian = member.guardians.filter(email__gt='').first()
            recipient = guardian.email if guardian else None
        else:
            recipient = member.email or None
        if not recipient:
            return
        try:
            compose.send(self.org, 'waiting_list_place', [recipient],
                         {**compose.member_values(member, to_guardian=has_guardians), 'class_name': cls.name})
        except Exception:  # the move itself has happened; a failed email mustn't undo it
            pass


class RemoveFromWaitingListView(OrgAdminMixin, View):
    required_feature = 'waiting_lists'

    def post(self, request, org_slug, pk, member_pk):
        cls = get_object_or_404(Class, pk=pk, organisation=self.org)
        WaitingList.objects.filter(assigned_class=cls, member_id=member_pk).delete()
        messages.success(request, 'Removed from waiting list.')
        return redirect('class_detail', org_slug=self.org.slug, pk=cls.pk)


def _parse_weeks(value, default=8):
    try:
        return max(1, min(int(value), 52))
    except (ValueError, TypeError):
        return default


def generate_sessions(cls, weeks):
    """Create the class's sessions for the next `weeks` weeks (existing ones are kept). Returns how many were new."""
    from_date = date.today()
    to_date = from_date + timedelta(weeks=weeks)
    created = 0
    current = from_date
    while current < to_date:
        for entry in cls.schedule:
            if current.weekday() == entry['day']:
                _, was_new = Session.objects.get_or_create(
                    assigned_class=cls, date=current, defaults={'leader': cls.default_leader},
                )
                if was_new:
                    created += 1
        current += timedelta(days=1)
    return created


class BulkGenerateSessionsView(OrgAdminMixin, View):
    """Pick several classes and generate their registers (sessions) in one go."""

    def _rows(self):
        today = date.today()
        rows = []
        for cls in Class.objects.filter(organisation=self.org).order_by('name'):
            upcoming = cls.sessions.filter(date__gte=today, is_cancelled=False).order_by('date')
            rows.append({'cls': cls, 'upcoming': upcoming.count(), 'last': upcoming.last(), 'has_schedule': bool(cls.schedule)})
        return rows

    def get(self, request, org_slug):
        return render(request, 'classes/bulk_generate.html', {
            'org': self.org, 'org_membership': self.org_membership, 'rows': self._rows(), 'weeks': 6,
        })

    def post(self, request, org_slug):
        weeks = _parse_weeks(request.POST.get('weeks'), 6)
        chosen = Class.objects.filter(organisation=self.org, pk__in=request.POST.getlist('classes'))
        chosen = [cls for cls in chosen if cls.schedule]
        if not chosen:
            messages.warning(request, 'Choose at least one class that has a weekly schedule.')
            return redirect('class_bulk_generate', org_slug=self.org.slug)
        total = sum(generate_sessions(cls, weeks) for cls in chosen)
        messages.success(
            request,
            f'{total} session{"s" if total != 1 else ""} generated across {len(chosen)} '
            f'class{"es" if len(chosen) != 1 else ""} for the next {weeks} week{"s" if weeks != 1 else ""}.',
        )
        return redirect('class_list', org_slug=self.org.slug)


class GenerateSessionsView(ClassCapabilityMixin, View):
    required_capabilities = ('generate_sessions',)

    def post(self, request, org_slug, pk):
        cls = self.assigned_class
        if not cls.schedule:
            messages.warning(request, 'This class has no schedule set.')
            return _back_to_class(self, request, cls)

        created = generate_sessions(cls, _parse_weeks(request.POST.get('weeks', 8)))

        messages.success(request, f'{created} session{"s" if created != 1 else ""} generated.')
        return _back_to_class(self, request, cls)


class AttendanceRegisterView(ClassCoachMixin, View):
    def _get_session(self, session_pk):
        return get_object_or_404(
            Session.objects.select_related('leader'), pk=session_pk, assigned_class=self.assigned_class,
        )

    def _unsigned_waiver_ids(self, enrolled):
        from documents.models import SignedWaiver, WaiverTemplate
        if not self.org.has_feature('documents'):
            return set()
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

    def _enrolled(self, session):
        """Register rows: the class's members plus anyone marked for this session who has since left."""
        enrolled = with_former_members(self.assigned_class, session, register_enrolments(self.assigned_class, session))
        members = [cm.member for cm in enrolled]
        attach_register_flags(self.org, members)
        attach_member_contacts(self.org, members)
        _attach_member_photo_consent(self.org, members)
        return enrolled

    def _helpers(self):
        """The class's helpers: members who help run it, marked on the register like coaches."""
        helpers = list(
            ClassHelper.objects.filter(assigned_class=self.assigned_class)
            .select_related('member')
            .order_by('member__name')
        )
        attach_member_contacts(self.org, [h.member for h in helpers])
        return helpers

    def _render(self, request, session, enrolled, present_ids, coaches, present_coach_ids, notes,
                coaches_on_holiday=None, leader_id=None, helpers=None, present_helper_ids=None):
        coaches = attach_staff_contacts(self.org, coaches)
        if leader_id is None:
            # The session's own leader, or the class's usual one as a suggestion.
            leader_id = session.leader_id or self.assigned_class.default_leader_id
        return render(request, 'classes/register.html', {
            **_roster_choices(self, self.assigned_class),
            'org': self.org,
            'org_membership': self.org_membership,
            'cls': self.assigned_class,
            'session': session,
            'enrolled': enrolled,
            'enrolled_count': sum(not cm.is_former_member for cm in enrolled),
            'present_ids': present_ids,
            'coaches': coaches,
            'present_coach_ids': present_coach_ids,
            'unsigned_waiver_ids': self._unsigned_waiver_ids(enrolled),
            'notes': notes,
            'coaches_on_holiday': coaches_on_holiday or [],
            'leader_id': leader_id,
            'helpers': self._helpers() if helpers is None else helpers,
            'present_helper_ids': present_helper_ids or set(),
        })

    def get(self, request, org_slug, pk, session_pk):
        session = self._get_session(session_pk)
        enrolled = self._enrolled(session)
        present_ids = set(
            Attendance.objects.filter(session=session, present=True)
            .values_list('member_id', flat=True)
        )
        coaches, coaches_on_holiday = _coaches_and_holiday_split(self.assigned_class, session.date)
        present_coach_ids = set(
            SessionCoach.objects.filter(session=session, present=True)
            .values_list('coach_id', flat=True)
        )
        present_helper_ids = set(
            SessionHelper.objects.filter(session=session, present=True)
            .values_list('helper_id', flat=True)
        )
        return self._render(
            request, session, enrolled, present_ids, coaches, present_coach_ids, session.notes,
            coaches_on_holiday, present_helper_ids=present_helper_ids,
        )

    def post(self, request, org_slug, pk, session_pk):
        session = self._get_session(session_pk)
        enrolled = self._enrolled(session)
        present_ids = {int(x) for x in request.POST.getlist('present')}
        coaches, coaches_on_holiday = _coaches_and_holiday_split(self.assigned_class, session.date)
        coach_present_ids = {int(x) for x in request.POST.getlist('coach_present')}
        helpers = self._helpers()
        helper_present_ids = {int(x) for x in request.POST.getlist('helper_present') if x.isdigit()}
        notes = request.POST.get('notes', session.notes)
        leader_raw = request.POST.get('leader', '').strip()
        leader_id = int(leader_raw) if leader_raw.isdigit() else None
        again = {'helpers': helpers, 'present_helper_ids': helper_present_ids}

        if coaches.exists() and not coach_present_ids:
            messages.error(request, 'At least one coach must be marked present to save the register.')
            return self._render(request, session, enrolled, present_ids, coaches, coach_present_ids, notes,
                                coaches_on_holiday, leader_id=leader_id or 0, **again)

        if leader_id is not None and leader_id not in coach_present_ids:
            messages.error(request, 'The session leader must be one of the coaches marked present.')
            return self._render(request, session, enrolled, present_ids, coaches, coach_present_ids, notes,
                                coaches_on_holiday, leader_id=leader_id, **again)
        # Present coaches come from this class's own coach list.
        if leader_id is not None and not coaches.filter(user_id=leader_id).exists():
            leader_id = None

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
        enrolled = list(
            ClassMember.objects.filter(assigned_class=cls)
            .select_related('member')
            .order_by('member__name')
        )
        members = attach_register_flags(self.org, [cm.member for cm in enrolled])
        if self.org.has_feature('grades'):
            from progression.gradings import attach_current_grades
            attach_current_grades(members)
        return render(request, 'classes/coach_detail.html', {
            **_roster_choices(self, cls),
            'org': self.org,
            'org_membership': self.org_membership,
            'cls': cls,
            'upcoming': upcoming,
            'recent': recent,
            'enrolled': enrolled,
            'can_generate_sessions': self.can('generate_sessions'),
            'can_manage_class_members': self.can('manage_class_members'),
            'class_coaches': cls.coaches.select_related('user').order_by('user__first_name', 'user__last_name'),
        })


class AddCoachView(ClassCapabilityMixin, View):
    required_capabilities = ('manage_class_members',)

    def post(self, request, org_slug, pk):
        from organisations.models import OrganisationMember
        cls = self.assigned_class
        user_pk = request.POST.get('user_id', '')
        # Only this club's staff can coach its classes.
        om = get_object_or_404(
            OrganisationMember.objects.select_related('user'),
            user_id=user_pk if user_pk.isdigit() else None, organisation=self.org,
        )
        ClassCoach.objects.get_or_create(assigned_class=cls, user=om.user)
        messages.success(request, f'{om.user.get_full_name() or om.user.username} added as coach.')
        return _back_to_class(self, request, cls)


class SetClassLeaderView(ClassCoachMixin, View):
    """
    Sets or clears the class's default leader: the coach who normally runs
    it. Any coach of the class can set it, as a "this is my class" choice.
    It's carried onto newly generated sessions and pre-selected on registers.
    """

    def post(self, request, org_slug, pk):
        cls = self.assigned_class
        leader_raw = request.POST.get('default_leader', '').strip()
        if not leader_raw:
            cls.default_leader = None
            cls.save(update_fields=['default_leader'])
            messages.success(request, f'Cleared the usual leader for {cls.name}.')
            return _back_to_class(self, request, cls)
        coach = (
            ClassCoach.objects.filter(assigned_class=cls, user_id=leader_raw if leader_raw.isdigit() else None)
            .select_related('user').first()
        )
        if coach is None:
            messages.error(request, "That person doesn't coach this class.")
            return _back_to_class(self, request, cls)
        cls.default_leader = coach.user
        cls.save(update_fields=['default_leader'])
        name = coach.user.get_full_name() or coach.user.username
        messages.success(request, f'{name} now leads {cls.name} by default.')
        return _back_to_class(self, request, cls)


class RemoveCoachView(OrgAdminMixin, View):
    def post(self, request, org_slug, pk, coach_pk):
        cls = get_object_or_404(Class, pk=pk, organisation=self.org)
        coach = get_object_or_404(ClassCoach, pk=coach_pk, assigned_class=cls)
        name = coach.user.get_full_name() or coach.user.username
        coach.delete()
        if cls.default_leader_id == coach.user_id:
            cls.default_leader = None
            cls.save(update_fields=['default_leader'])
        messages.success(request, f'{name} removed as coach.')
        return redirect('class_detail', org_slug=self.org.slug, pk=cls.pk)


class AddHelperView(ClassCapabilityMixin, View):
    """Adds a class helper. Same permission as enrolling members (Enrol members)."""
    required_capabilities = ('manage_class_members',)

    def post(self, request, org_slug, pk):
        cls = self.assigned_class
        member = get_object_or_404(Member, pk=request.POST.get('member_id'), organisation=self.org)
        _, created = ClassHelper.objects.get_or_create(assigned_class=cls, member=member)
        if created:
            messages.success(request, f'{member.name} added as a helper.')
        else:
            messages.info(request, f'{member.name} is already a helper for this class.')
        return _back_to_class(self, request, cls)


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
        enrolled = with_former_members(self.assigned_class, session, register_enrolments(self.assigned_class, session))
        members = [cm.member for cm in enrolled]
        attach_register_flags(self.org, members)
        attach_member_contacts(self.org, members)
        _attach_member_photo_consent(self.org, members)
        present_ids = set(
            session.attendance.filter(present=True).values_list('member_id', flat=True)
        )
        coaches, _ = _coaches_and_holiday_split(self.assigned_class, session.date)
        coaches = attach_staff_contacts(self.org, coaches)
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
            'enrolled_count': sum(not cm.is_former_member for cm in enrolled),
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

            session_date = f'{session.date:%A} {session.date.day} {session.date:%B %Y}'
            try:
                compose.send(self.org, 'session_cancelled', [recipient],
                             {**compose.member_values(member, to_guardian=has_guardians),
                              'class_name': cls.name, 'session_date': session_date},
                             {'session_box': email_blocks.session_box(cls.name, session_date)})
                sent += 1
            except Exception:  # one bad address mustn't stop the other notices
                pass

        if sent:
            messages.success(request, f'Cancellation notice sent to {sent} member{"s" if sent != 1 else ""}.')


class AttendanceScopeMixin(OrgMixin):
    """
    Attendance reporting is open to all staff, but coaches only see the
    classes they're assigned to unless granted `view_all_attendance`.
    """
    def visible_classes(self):
        classes = Class.objects.filter(organisation=self.org)
        if not self.can('view_all_attendance'):
            classes = classes.filter(pk__in=self.coached_class_ids())
        return classes

    def selected_class_pk(self):
        """The ?class= filter, ignored unless it's one of the visible classes."""
        class_pk = self.request.GET.get('class', '')
        if class_pk.isdigit() and self.visible_classes().filter(pk=class_pk).exists():
            return class_pk
        return ''


class AttendanceAnalyticsView(AttendanceScopeMixin, View):
    template_name = 'classes/attendance_analytics.html'

    def get(self, request, org_slug):
        from django.db.models import Count, Max, Q

        today = date.today()
        four_weeks_ago = today - timedelta(weeks=4)
        eight_weeks_ago = today - timedelta(weeks=8)
        two_weeks_ago = today - timedelta(weeks=2)

        class_pk = self.selected_class_pk()
        sort = request.GET.get('sort', 'status')
        classes = self.visible_classes().order_by('name')

        # Filter by subquery rather than joining enrolments, so a member in
        # several visible classes isn't counted more than once below.
        enrolled_ids = ClassMember.objects.filter(
            assigned_class_id__in=[class_pk] if class_pk else classes.values('pk'),
        ).values('member_id')
        members = Member.objects.filter(organisation=self.org, is_active=True)
        if class_pk or not self.can('view_all_attendance'):
            members = members.filter(pk__in=enrolled_ids)

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

        # Total sessions run in the last 4 weeks across the visible classes
        total_sessions_4w = Session.objects.filter(
            assigned_class__in=classes,
            date__gte=four_weeks_ago,
            is_cancelled=False,
        ).count()

        trend = self.trend(request, classes, class_pk, today)

        return render(request, self.template_name, {
            **trend,
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
        })

    TREND_COUNTS = (10, 25, 50)

    def trend(self, request, classes, class_pk, today):
        """
        Members present at each of a class's last N sessions that have run
        (not cancelled, register taken), oldest first, and their average. The class can be
        chosen separately from the table's filter; it defaults to that, then
        to the first class this person can see.
        """
        from django.db.models import Count, Q
        trend_pk = request.GET.get('trend_class', '')
        if not (trend_pk.isdigit() and classes.filter(pk=trend_pk).exists()):
            trend_pk = class_pk or (str(classes.first().pk) if classes.exists() else '')
        try:
            count = int(request.GET.get('trend_count', 10))
        except ValueError:
            count = 10
        if count not in self.TREND_COUNTS:
            count = 10
        sessions = []
        if trend_pk:
            sessions = list(
                Session.objects.filter(assigned_class_id=trend_pk, is_cancelled=False, date__lte=today)
                .annotate(
                    present_count=Count('attendance', filter=Q(attendance__present=True)),
                    marked_count=Count('attendance'),
                )
                .filter(marked_count__gt=0)  # a register nobody has taken yet isn't "nobody came"
                .order_by('-date')[:count]
            )
            sessions.reverse()
        data = [s.present_count for s in sessions]
        return {
            'trend_selected_class': trend_pk,
            'trend_count': count,
            'trend_counts': self.TREND_COUNTS,
            'trend_labels': json.dumps([s.date.strftime('%d %b') for s in sessions]),
            'trend_data': json.dumps(data),
            'trend_average': round(sum(data) / len(data), 1) if data else None,
            'trend_has_data': bool(data),
        }


class AttendanceExportView(AttendanceScopeMixin, View):
    def get(self, request, org_slug):
        import csv
        from django.http import HttpResponse
        from .models import Session, Attendance, SessionCoach, SessionHelper

        date_from_raw = request.GET.get('date_from', '')
        date_to_raw = request.GET.get('date_to', '')
        class_pk = self.selected_class_pk()

        sessions = Session.objects.filter(
            assigned_class__in=self.visible_classes(),
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

        # Which kinds of attendance to include; everything unless the form narrowed it down.
        roles = set(request.GET.getlist('roles')) & {'member', 'coach', 'helper'} or {'member', 'coach', 'helper'}

        # (date, class, role order, name, role label, present)
        rows = []
        if 'member' in roles:
            for a in Attendance.objects.filter(session__in=sessions).select_related(
                'member', 'session', 'session__assigned_class',
            ):
                rows.append((a.session.date, a.session.assigned_class.name, 0, a.member.name, 'Member', a.present))
        if 'coach' in roles:
            for c in SessionCoach.objects.filter(session__in=sessions).select_related(
                'coach', 'session', 'session__assigned_class',
            ):
                name = c.coach.get_full_name() or c.coach.username
                rows.append((c.session.date, c.session.assigned_class.name, 1, name, 'Coach', c.present))
        if 'helper' in roles:
            for h in SessionHelper.objects.filter(session__in=sessions).select_related(
                'helper', 'session', 'session__assigned_class',
            ):
                rows.append((h.session.date, h.session.assigned_class.name, 2, h.helper.name, 'Helper', h.present))
        rows.sort(key=lambda r: (r[0], r[1], r[2], r[3].lower()))

        response = HttpResponse(content_type='text/csv')
        label_parts = []
        if date_from_raw:
            label_parts.append(date_from_raw)
        if date_to_raw:
            label_parts.append(date_to_raw)
        suffix = '-'.join(label_parts) or 'all'
        response['Content-Disposition'] = f'attachment; filename="{self.org.slug}-attendance-{suffix}.csv"'

        writer = csv.writer(response)
        writer.writerow(['Date', 'Class', 'Name', 'Role', 'Present'])
        for day, class_name, _, name, role, present in rows:
            writer.writerow([day.isoformat(), class_name, name, role, 'Yes' if present else 'No'])
        return response
