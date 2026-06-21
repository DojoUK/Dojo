import json
from datetime import date, timedelta

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from django.views.generic import DetailView, ListView

from dojo.mixins import ClassCoachMixin, OrgAdminMixin, OrgMixin
from members.models import Member

from .models import Attendance, Class, ClassCoach, ClassMember, Session


DAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
DAYS_JSON = json.dumps(DAYS)


def _class_form_class():
    from django import forms

    class ClassForm(forms.ModelForm):
        class Meta:
            model = Class
            fields = ['name', 'description']
            widgets = {'description': forms.Textarea(attrs={'rows': 3})}

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            for field in self.fields.values():
                field.widget.attrs['class'] = 'form-control'

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
        form = _class_form_class()()
        return render(request, 'classes/form.html', {
            'org': self.org, 'org_membership': self.org_membership,
            'form': form, 'title': 'Add class', 'days': DAYS, 'days_json': DAYS_JSON, 'schedule': [],
        })

    def post(self, request, org_slug):
        FormClass = _class_form_class()
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
        form = _class_form_class()(instance=cls)
        return render(request, 'classes/form.html', {
            'org': self.org, 'org_membership': self.org_membership,
            'form': form, 'title': f'Edit {cls.name}',
            'days': DAYS, 'days_json': DAYS_JSON, 'schedule': cls.schedule or [], 'cls': cls,
        })

    def post(self, request, org_slug, pk):
        cls = self.get_class(pk)
        FormClass = _class_form_class()
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
        context['enrolled'] = (
            Member.objects.filter(pk__in=enrolled_ids)
            .order_by('name')
        )
        context['available'] = (
            Member.objects.filter(organisation=self.org, is_active=True)
            .exclude(pk__in=enrolled_ids)
            .order_by('name')
        )
        coaches = self.object.coaches.select_related('user')
        coach_user_ids = coaches.values_list('user_id', flat=True)
        context['coaches'] = coaches
        from organisations.models import OrganisationMember
        context['available_coaches'] = (
            OrganisationMember.objects.filter(organisation=self.org)
            .exclude(user_id__in=coach_user_ids)
            .select_related('user')
        )
        context['sessions'] = self.object.sessions.order_by('-date')[:10]
        context['days'] = DAYS
        return context


class EnrolMemberView(OrgAdminMixin, View):
    def post(self, request, org_slug, pk):
        cls = get_object_or_404(Class, pk=pk, organisation=self.org)
        member = get_object_or_404(Member, pk=request.POST.get('member_id'), organisation=self.org)
        ClassMember.objects.get_or_create(assigned_class=cls, member=member)
        messages.success(request, f'{member.name} enrolled.')
        return redirect('class_detail', org_slug=self.org.slug, pk=cls.pk)


class UnenrolMemberView(OrgAdminMixin, View):
    def post(self, request, org_slug, pk, member_pk):
        cls = get_object_or_404(Class, pk=pk, organisation=self.org)
        member = get_object_or_404(Member, pk=member_pk, organisation=self.org)
        ClassMember.objects.filter(assigned_class=cls, member=member).delete()
        messages.success(request, f'{member.name} removed.')
        return redirect('class_detail', org_slug=self.org.slug, pk=cls.pk)


class GenerateSessionsView(OrgAdminMixin, View):
    def post(self, request, org_slug, pk):
        cls = get_object_or_404(Class, pk=pk, organisation=self.org)
        if not cls.schedule:
            messages.warning(request, 'This class has no schedule set.')
            return redirect('class_detail', org_slug=self.org.slug, pk=cls.pk)

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
                    _, was_new = Session.objects.get_or_create(assigned_class=cls, date=current)
                    if was_new:
                        created += 1
            current += timedelta(days=1)

        messages.success(request, f'{created} session{"s" if created != 1 else ""} generated.')
        return redirect('class_detail', org_slug=self.org.slug, pk=cls.pk)


class AttendanceRegisterView(ClassCoachMixin, View):
    def _get_session(self, session_pk):
        return get_object_or_404(Session, pk=session_pk, assigned_class=self.assigned_class)

    def get(self, request, org_slug, pk, session_pk):
        session = self._get_session(session_pk)
        enrolled = (
            ClassMember.objects.filter(assigned_class=self.assigned_class)
            .select_related('member')
            .order_by('member__name')
        )
        present_ids = set(
            Attendance.objects.filter(session=session, present=True)
            .values_list('member_id', flat=True)
        )
        return render(request, 'classes/register.html', {
            'org': self.org,
            'org_membership': self.org_membership,
            'cls': self.assigned_class,
            'session': session,
            'enrolled': enrolled,
            'present_ids': present_ids,
        })

    def post(self, request, org_slug, pk, session_pk):
        session = self._get_session(session_pk)
        enrolled = ClassMember.objects.filter(assigned_class=self.assigned_class).select_related('member')
        present_ids = {int(x) for x in request.POST.getlist('present')}

        for cm in enrolled:
            Attendance.objects.update_or_create(
                session=session,
                member=cm.member,
                defaults={'present': cm.member.pk in present_ids},
            )

        session.notes = request.POST.get('notes', session.notes)
        session.save(update_fields=['notes'])

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
        upcoming = cls.sessions.filter(date__gte=date.today()).order_by('date')[:10]
        recent = cls.sessions.filter(date__lt=date.today()).order_by('-date')[:5]
        enrolled = (
            ClassMember.objects.filter(assigned_class=cls)
            .select_related('member')
            .order_by('member__name')
        )
        return render(request, 'classes/coach_detail.html', {
            'org': self.org,
            'org_membership': self.org_membership,
            'cls': cls,
            'upcoming': upcoming,
            'recent': recent,
            'enrolled': enrolled,
        })


class AddCoachView(OrgAdminMixin, View):
    def post(self, request, org_slug, pk):
        from django.contrib.auth.models import User
        cls = get_object_or_404(Class, pk=pk, organisation=self.org)
        user_pk = request.POST.get('user_id')
        user = get_object_or_404(User, pk=user_pk)
        ClassCoach.objects.get_or_create(assigned_class=cls, user=user)
        messages.success(request, f'{user.get_full_name() or user.username} added as coach.')
        return redirect('class_detail', org_slug=self.org.slug, pk=cls.pk)


class RemoveCoachView(OrgAdminMixin, View):
    def post(self, request, org_slug, pk, coach_pk):
        cls = get_object_or_404(Class, pk=pk, organisation=self.org)
        coach = get_object_or_404(ClassCoach, pk=coach_pk, assigned_class=cls)
        name = coach.user.get_full_name() or coach.user.username
        coach.delete()
        messages.success(request, f'{name} removed as coach.')
        return redirect('class_detail', org_slug=self.org.slug, pk=cls.pk)


class AttendanceAnalyticsView(OrgAdminMixin, View):
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
        })
