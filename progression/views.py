import csv
import io
from datetime import date

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from dojo.mixins import OrgAdminMixin
from .models import MemberProgression, ProgressionStage, ProgressionSystem


class ProgressionSettingsView(OrgAdminMixin, View):
    def get(self, request, org_slug):
        systems = (
            ProgressionSystem.objects
            .filter(organisation=self.org)
            .prefetch_related('stages')
        )
        return render(request, 'progression/settings.html', {
            'systems': systems,
            'org': self.org,
            'org_membership': self.org_membership,
        })


class AddSystemView(OrgAdminMixin, View):
    def post(self, request, org_slug):
        name = request.POST.get('name', '').strip()
        if not name:
            messages.error(request, 'System name is required.')
            return redirect('progression_settings', org_slug=org_slug)
        if ProgressionSystem.objects.filter(organisation=self.org, name=name).exists():
            messages.error(request, f'A system called "{name}" already exists.')
            return redirect('progression_settings', org_slug=org_slug)
        last = ProgressionSystem.objects.filter(organisation=self.org).order_by('order').last()
        ProgressionSystem.objects.create(
            organisation=self.org,
            name=name,
            order=(last.order + 1) if last else 0,
        )
        messages.success(request, f'"{name}" system created.')
        return redirect('progression_settings', org_slug=org_slug)


class DeleteSystemView(OrgAdminMixin, View):
    def post(self, request, org_slug, pk):
        system = get_object_or_404(ProgressionSystem, pk=pk, organisation=self.org)
        if MemberProgression.objects.filter(stage__system=system).exists():
            messages.error(request, f'Cannot delete "{system.name}" — members have records in it.')
            return redirect('progression_settings', org_slug=org_slug)
        system.delete()
        messages.success(request, f'"{system.name}" deleted.')
        return redirect('progression_settings', org_slug=org_slug)


class ToggleAutoAssignView(OrgAdminMixin, View):
    def post(self, request, org_slug, pk):
        system = get_object_or_404(ProgressionSystem, pk=pk, organisation=self.org)
        system.assign_to_new_members = not system.assign_to_new_members
        system.save(update_fields=['assign_to_new_members'])
        return redirect('progression_settings', org_slug=org_slug)


class AddStageView(OrgAdminMixin, View):
    def post(self, request, org_slug, system_pk):
        system = get_object_or_404(ProgressionSystem, pk=system_pk, organisation=self.org)
        name = request.POST.get('name', '').strip()
        colour = request.POST.get('colour', '').strip()
        if not name:
            messages.error(request, 'Stage name is required.')
            return redirect('progression_settings', org_slug=org_slug)
        if system.stages.filter(name=name).exists():
            messages.error(request, f'"{name}" already exists in {system.name}.')
            return redirect('progression_settings', org_slug=org_slug)
        last = system.stages.order_by('order').last()
        system.stages.create(
            name=name,
            colour=colour,
            order=(last.order + 1) if last else 0,
        )
        messages.success(request, f'"{name}" added to {system.name}.')
        return redirect('progression_settings', org_slug=org_slug)


class DeleteStageView(OrgAdminMixin, View):
    def post(self, request, org_slug, system_pk, pk):
        system = get_object_or_404(ProgressionSystem, pk=system_pk, organisation=self.org)
        stage = get_object_or_404(ProgressionStage, pk=pk, system=system)
        if stage.achievements.exists():
            messages.error(request, f'Cannot delete "{stage.name}" — members have been awarded it.')
            return redirect('progression_settings', org_slug=org_slug)
        stage.delete()
        messages.success(request, f'"{stage.name}" deleted.')
        return redirect('progression_settings', org_slug=org_slug)


class MoveStageView(OrgAdminMixin, View):
    def post(self, request, org_slug, system_pk, pk):
        system = get_object_or_404(ProgressionSystem, pk=system_pk, organisation=self.org)
        stage = get_object_or_404(ProgressionStage, pk=pk, system=system)
        direction = request.POST.get('direction')
        stages = list(system.stages.order_by('order'))
        idx = next((i for i, s in enumerate(stages) if s.pk == stage.pk), None)
        if idx is None:
            return redirect('progression_settings', org_slug=org_slug)
        if direction == 'up' and idx > 0:
            stages[idx], stages[idx - 1] = stages[idx - 1], stages[idx]
        elif direction == 'down' and idx < len(stages) - 1:
            stages[idx], stages[idx + 1] = stages[idx + 1], stages[idx]
        for i, s in enumerate(stages):
            if s.order != i:
                s.order = i
                s.save(update_fields=['order'])
        return redirect('progression_settings', org_slug=org_slug)


class EditStageView(OrgAdminMixin, View):
    def post(self, request, org_slug, system_pk, pk):
        system = get_object_or_404(ProgressionSystem, pk=system_pk, organisation=self.org)
        stage = get_object_or_404(ProgressionStage, pk=pk, system=system)
        name = request.POST.get('name', '').strip()
        colour = request.POST.get('colour', '').strip()
        if not name:
            messages.error(request, 'Stage name is required.')
            return redirect('progression_settings', org_slug=org_slug)
        if system.stages.filter(name=name).exclude(pk=pk).exists():
            messages.error(request, f'"{name}" already exists in {system.name}.')
            return redirect('progression_settings', org_slug=org_slug)
        stage.name = name
        stage.colour = colour
        stage.save(update_fields=['name', 'colour'])
        messages.success(request, f'Stage updated.')
        return redirect('progression_settings', org_slug=org_slug)


class SetDefaultStageView(OrgAdminMixin, View):
    def post(self, request, org_slug, system_pk, pk):
        system = get_object_or_404(ProgressionSystem, pk=system_pk, organisation=self.org)
        stage = get_object_or_404(ProgressionStage, pk=pk, system=system)
        system.stages.filter(is_default=True).update(is_default=False)
        stage.is_default = True
        stage.save(update_fields=['is_default'])
        messages.success(request, f'"{stage.name}" is now the default for new members in {system.name}.')
        return redirect('progression_settings', org_slug=org_slug)


class ApplyDefaultStageView(OrgAdminMixin, View):
    """
    Assigns the system's default stage to every active member who doesn't
    already have any progression record within this system.
    """
    def post(self, request, org_slug, pk):
        from django.utils import timezone
        from members.models import Member

        system = get_object_or_404(ProgressionSystem, pk=pk, organisation=self.org)
        default_stage = system.stages.filter(is_default=True).first()

        if not default_stage:
            messages.error(request, f'"{system.name}" has no default stage set. Mark one with the star first.')
            return redirect('progression_settings', org_slug=org_slug)

        already_have = set(
            MemberProgression.objects.filter(stage__system=system)
            .values_list('member_id', flat=True)
        )
        members_to_assign = Member.objects.filter(
            organisation=self.org, is_active=True
        ).exclude(pk__in=already_have)

        today = timezone.localdate()
        count = 0
        for member in members_to_assign:
            MemberProgression.objects.create(
                member=member,
                stage=default_stage,
                achieved_date=today,
                notes='Default grade assigned in bulk.',
            )
            count += 1

        if count:
            messages.success(request, f'{count} member{"s" if count != 1 else ""} assigned to "{default_stage.name}".')
        else:
            messages.info(request, f'All active members already have a grade in "{system.name}".')

        return redirect('progression_settings', org_slug=org_slug)


class ImportProgressionView(OrgAdminMixin, View):
    """
    CSV import for historical progression data.

    Required columns: member_name, stage_name, system_name, achieved_date (YYYY-MM-DD)
    Optional column:  notes
    """

    def get(self, request, org_slug):
        systems = ProgressionSystem.objects.filter(organisation=self.org).prefetch_related('stages')
        return render(request, 'progression/import.html', {
            'systems': systems,
            'org': self.org,
            'org_membership': self.org_membership,
        })

    def post(self, request, org_slug):
        from members.models import Member

        csv_file = request.FILES.get('csv_file')
        if not csv_file:
            messages.error(request, 'No file uploaded.')
            return redirect('progression_import', org_slug=org_slug)

        try:
            text = csv_file.read().decode('utf-8-sig')
        except UnicodeDecodeError:
            messages.error(request, 'File must be UTF-8 encoded.')
            return redirect('progression_import', org_slug=org_slug)

        reader = csv.DictReader(io.StringIO(text))
        fieldnames = {c.strip().lower() for c in (reader.fieldnames or [])}
        required_cols = {'member_name', 'stage_name', 'system_name', 'achieved_date'}
        if not required_cols.issubset(fieldnames):
            missing = required_cols - fieldnames
            messages.error(request, f'CSV is missing columns: {", ".join(sorted(missing))}')
            return redirect('progression_import', org_slug=org_slug)

        members = {m.name.strip().lower(): m for m in Member.objects.filter(organisation=self.org)}
        stage_lookup = {}
        for system in ProgressionSystem.objects.filter(organisation=self.org).prefetch_related('stages'):
            for stage in system.stages.all():
                key = (system.name.strip().lower(), stage.name.strip().lower())
                stage_lookup[key] = stage

        created = skipped = errors = 0
        skip_reasons = []

        for row_num, row in enumerate(reader, start=2):
            member_name = row.get('member_name', '').strip()
            stage_name = row.get('stage_name', '').strip()
            system_name = row.get('system_name', '').strip()
            achieved_date_str = row.get('achieved_date', '').strip()
            notes = row.get('notes', '').strip()

            member = members.get(member_name.lower())
            if not member:
                skip_reasons.append(f'Row {row_num}: member "{member_name}" not found.')
                skipped += 1
                continue

            stage = stage_lookup.get((system_name.lower(), stage_name.lower()))
            if not stage:
                skip_reasons.append(
                    f'Row {row_num}: stage "{stage_name}" in system "{system_name}" not found.'
                )
                skipped += 1
                continue

            try:
                achieved_date = date.fromisoformat(achieved_date_str)
            except ValueError:
                skip_reasons.append(f'Row {row_num}: invalid date "{achieved_date_str}" — use YYYY-MM-DD.')
                errors += 1
                continue

            MemberProgression.objects.create(
                member=member,
                stage=stage,
                achieved_date=achieved_date,
                notes=notes,
            )
            created += 1

        if created:
            messages.success(request, f'{created} record{"s" if created != 1 else ""} imported.')
        if skipped:
            messages.warning(request, f'{skipped} row{"s" if skipped != 1 else ""} skipped — member or stage not found.')
        if errors:
            messages.error(request, f'{errors} row{"s" if errors != 1 else ""} had date errors.')

        systems_ctx = ProgressionSystem.objects.filter(organisation=self.org).prefetch_related('stages')
        return render(request, 'progression/import.html', {
            'systems': systems_ctx,
            'skip_reasons': skip_reasons,
            'org': self.org,
            'org_membership': self.org_membership,
        })
