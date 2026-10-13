from datetime import date

from django.contrib import messages
from django.db.models import Count, Min, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from dojo.mixins import CapabilityMixin
from members.models import Member

from . import gradings
from .models import Grading, GradingCandidate, GradingSession, ProgressionSystem


def _parse_date(raw):
    try:
        return date.fromisoformat((raw or '').strip())
    except ValueError:
        return None


class GradingMixin(CapabilityMixin):
    """Gradings: org admins and staff granted `manage_gradings`, while the feature is on."""
    required_feature = 'gradings'
    required_capabilities = ('manage_gradings',)

    def get_grading(self):
        return get_object_or_404(
            Grading.objects.select_related('system'), pk=self.kwargs['pk'], organisation=self.org,
        )

    def get_session(self, grading):
        return get_object_or_404(GradingSession, pk=self.kwargs['session_pk'], grading=grading)

    def grading_systems(self):
        return ProgressionSystem.objects.filter(organisation=self.org, uses_gradings=True)

    def back_to(self, session):
        """A single-session grading's page is its session page; otherwise go to the session itself."""
        if session.grading.sessions.count() == 1:
            return redirect('grading_detail', org_slug=self.org.slug, pk=session.grading_id)
        return redirect('grading_session', org_slug=self.org.slug, pk=session.grading_id, session_pk=session.pk)

    def render_session(self, request, session, single):
        grading = session.grading
        candidates = list(
            session.candidates.select_related('member', 'target_stage', 'progression')
        )
        held = gradings.current_progressions(
            grading.system, [c.member_id for c in candidates], before_grading=grading,
        )
        for candidate in candidates:
            candidate.current = held.get(candidate.member_id)
        return render(request, 'progression/gradings/session.html', {
            'org': self.org,
            'org_membership': self.org_membership,
            'grading': grading,
            'session': session,
            'single': single,
            'candidates': candidates,
            'stages': grading.system.stages.order_by('order', 'name'),
            'results': GradingCandidate.Result.choices,
            'eligible': gradings.eligibility_rows(session),
        })


class GradingListView(GradingMixin, View):
    def get(self, request, org_slug):
        grading_list = (
            Grading.objects.filter(organisation=self.org)
            .select_related('system')
            .annotate(
                session_count=Count('sessions', distinct=True),
                candidate_count=Count('sessions__candidates', distinct=True),
                starts=Min('sessions__date'),
            )
            .order_by('-starts', '-created_at')
        )
        return render(request, 'progression/gradings/list.html', {
            'org': self.org,
            'org_membership': self.org_membership,
            'gradings': grading_list,
            'systems': self.grading_systems(),
            'today': date.today(),
        })

    def post(self, request, org_slug):
        name = request.POST.get('name', '').strip()
        session_date = _parse_date(request.POST.get('date'))
        system = self.grading_systems().filter(pk=request.POST.get('system_id') or None).first()
        if not name or not session_date or not system:
            messages.error(request, 'Name, date and progression system are required.')
            return redirect('grading_list', org_slug=self.org.slug)
        grading = Grading.objects.create(organisation=self.org, name=name, system=system)
        GradingSession.objects.create(grading=grading, date=session_date)
        messages.success(request, f'{grading.name} created. Now add the people being graded.')
        return redirect('grading_detail', org_slug=self.org.slug, pk=grading.pk)


class GradingDetailView(GradingMixin, View):
    """
    With one session, the grading page is that session's page; with
    several, it lists the sessions.
    """
    def get(self, request, org_slug, pk):
        grading = self.get_grading()
        sessions = list(grading.sessions.annotate(
            people=Count('candidates'),
            passed=Count('candidates', filter=Q(candidates__result=GradingCandidate.Result.PASSED)),
        ))
        if len(sessions) == 1:
            return self.render_session(request, sessions[0], single=True)
        return render(request, 'progression/gradings/detail.html', {
            'org': self.org,
            'org_membership': self.org_membership,
            'grading': grading,
            'sessions': sessions,
        })


class GradingEditView(GradingMixin, View):
    def post(self, request, org_slug, pk):
        grading = self.get_grading()
        name = request.POST.get('name', '').strip()
        if not name:
            messages.error(request, 'Name is required.')
        else:
            grading.name = name
            grading.notes = request.POST.get('notes', '').strip()
            grading.save(update_fields=['name', 'notes'])
            messages.success(request, 'Grading updated.')
        return redirect('grading_detail', org_slug=self.org.slug, pk=grading.pk)


class GradingDeleteView(GradingMixin, View):
    def post(self, request, org_slug, pk):
        grading = self.get_grading()
        if GradingCandidate.objects.filter(session__grading=grading, progression__isnull=False).exists():
            messages.error(request, "This grading has passes recorded. Change those results first, so members' grades stay right.")
            return redirect('grading_detail', org_slug=self.org.slug, pk=grading.pk)
        grading.delete()
        messages.success(request, f'{grading.name} deleted.')
        return redirect('grading_list', org_slug=self.org.slug)


class SessionAddView(GradingMixin, View):
    def post(self, request, org_slug, pk):
        grading = self.get_grading()
        session_date = _parse_date(request.POST.get('date'))
        if not session_date:
            messages.error(request, 'Date is required.')
            return redirect('grading_detail', org_slug=self.org.slug, pk=grading.pk)
        session = GradingSession.objects.create(
            grading=grading, date=session_date, name=request.POST.get('name', '').strip(),
        )
        messages.success(request, f'Session "{session.display_name}" added.')
        return redirect('grading_session', org_slug=self.org.slug, pk=grading.pk, session_pk=session.pk)


class SessionEditView(GradingMixin, View):
    def post(self, request, org_slug, pk, session_pk):
        grading = self.get_grading()
        session = self.get_session(grading)
        session_date = _parse_date(request.POST.get('date'))
        if not session_date:
            messages.error(request, 'Date is required.')
            return self.back_to(session)
        session.name = request.POST.get('name', '').strip()
        session.save(update_fields=['name'])
        if session_date != session.date:
            gradings.move_session_date(session, session_date)
        messages.success(request, 'Session updated.')
        return self.back_to(session)


class SessionDeleteView(GradingMixin, View):
    def post(self, request, org_slug, pk, session_pk):
        grading = self.get_grading()
        session = self.get_session(grading)
        if grading.sessions.count() == 1:
            messages.error(request, 'A grading needs at least one session. Delete the grading instead.')
            return self.back_to(session)
        if session.candidates.filter(progression__isnull=False).exists():
            messages.error(request, "This session has passes recorded. Change those results first, so members' grades stay right.")
            return self.back_to(session)
        session.delete()
        messages.success(request, f'Session "{session.display_name}" deleted.')
        return redirect('grading_detail', org_slug=self.org.slug, pk=grading.pk)


class SessionView(GradingMixin, View):
    def get(self, request, org_slug, pk, session_pk):
        grading = self.get_grading()
        session = self.get_session(grading)
        if grading.sessions.count() == 1:
            return redirect('grading_detail', org_slug=self.org.slug, pk=grading.pk)
        return self.render_session(request, session, single=False)


class CandidatesAddView(GradingMixin, View):
    def post(self, request, org_slug, pk, session_pk):
        grading = self.get_grading()
        session = self.get_session(grading)
        member_ids = [x for x in request.POST.getlist('member_ids') if x.isdigit()]
        members = Member.objects.filter(organisation=self.org, is_active=True, pk__in=member_ids)
        added = gradings.add_candidates(session, members)
        messages.success(request, f'{added} {"person" if added == 1 else "people"} added.')
        return self.back_to(session)


class CandidateRemoveView(GradingMixin, View):
    def post(self, request, org_slug, pk, session_pk, candidate_pk):
        grading = self.get_grading()
        session = self.get_session(grading)
        candidate = get_object_or_404(GradingCandidate, pk=candidate_pk, session=session)
        if candidate.progression_id:
            messages.error(request, f"{candidate.member.name} has a pass recorded. Change their result first, so their grade stays right.")
        else:
            candidate.delete()
            messages.success(request, f'{candidate.member.name} removed.')
        return self.back_to(session)


class ResultsSaveView(GradingMixin, View):
    def post(self, request, org_slug, pk, session_pk):
        grading = self.get_grading()
        session = self.get_session(grading)
        stages = {str(s.pk): s for s in grading.system.stages.all()}
        valid_results = {value for value, _ in GradingCandidate.Result.choices}
        problems = []
        for candidate in session.candidates.select_related('member', 'progression'):
            result = request.POST.get(f'result_{candidate.pk}', candidate.result)
            if result not in valid_results:
                result = candidate.result
            stage = stages.get(request.POST.get(f'stage_{candidate.pk}', ''))
            notes = request.POST.get(f'notes_{candidate.pk}', candidate.notes).strip()
            if result == GradingCandidate.Result.PASSED and stage is None:
                problems.append(candidate.member.name)
                continue
            gradings.record_result(candidate, result, stage, notes)
        if problems:
            messages.error(request, f'Choose a grade for: {", ".join(problems)}. Their row was not saved.')
        else:
            messages.success(request, 'Results saved.')
        return self.back_to(session)
