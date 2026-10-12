from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db.models import Count, Max, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from dojo.mixins import OrgAdminMixin

from .models import (
    ProgressionStage,
    ProgressionSystem,
    Syllabus,
    SyllabusEntry,
    SyllabusHeading,
    Technique,
)

_validate_url = URLValidator(schemes=['http', 'https'])


def clean_link(raw):
    """A technique's link, or raise ValidationError. Only http(s) links are allowed, never javascript: etc."""
    link = (raw or '').strip()
    if link:
        _validate_url(link)
    return link


def _next_order(queryset):
    return (queryset.aggregate(m=Max('order'))['m'] or 0) + 1


def _move(items, item, direction):
    """Swap `item` with its neighbour in `items` (an ordered list) and renumber them all."""
    items = list(items)
    index = items.index(item)
    swap = index - 1 if direction == 'up' else index + 1
    if 0 <= swap < len(items):
        items[index], items[swap] = items[swap], items[index]
    for position, obj in enumerate(items):
        if obj.order != position:
            obj.order = position
            obj.save(update_fields=['order'])


class SyllabusMixin(OrgAdminMixin):
    """Editing the technique library and syllabuses: org admins, while the feature is on."""
    required_feature = 'syllabus'

    def get_syllabus(self):
        return get_object_or_404(Syllabus, pk=self.kwargs['pk'], organisation=self.org)

    def to_syllabus(self, syllabus):
        return redirect('syllabus_detail', org_slug=self.org.slug, pk=syllabus.pk)

    def technique_from_post(self, request, technique=None):
        """Create or update a technique from posted fields. Returns (technique, error)."""
        name = request.POST.get('name', '').strip()
        if not name:
            return None, 'Technique name is required.'
        try:
            link = clean_link(request.POST.get('link'))
        except ValidationError:
            return None, 'The link must be a web address starting with http:// or https://.'
        technique = technique or Technique(organisation=self.org)
        technique.name = name
        technique.category = request.POST.get('category', '').strip()
        technique.description = request.POST.get('description', '').strip()
        technique.link = link
        technique.save()
        return technique, None


class SyllabusHomeView(SyllabusMixin, View):
    def get(self, request, org_slug):
        syllabuses = (
            Syllabus.objects.filter(organisation=self.org)
            .annotate(technique_count=Count('entries', distinct=True))
            .prefetch_related('stages__system')
        )
        return render(request, 'progression/syllabus/home.html', {
            'org': self.org,
            'org_membership': self.org_membership,
            'syllabuses': syllabuses,
            'technique_count': Technique.objects.filter(organisation=self.org).count(),
        })

    def post(self, request, org_slug):
        name = request.POST.get('name', '').strip()
        if not name:
            messages.error(request, 'Syllabus name is required.')
            return redirect('syllabus_home', org_slug=self.org.slug)
        syllabus = Syllabus.objects.create(
            organisation=self.org, name=name,
            order=_next_order(Syllabus.objects.filter(organisation=self.org)),
        )
        messages.success(request, f'{syllabus.name} created.')
        return self.to_syllabus(syllabus)


class SyllabusDetailView(SyllabusMixin, View):
    def get(self, request, org_slug, pk):
        syllabus = self.get_syllabus()
        used = syllabus.entries.values('technique_id')
        return render(request, 'progression/syllabus/detail.html', {
            'org': self.org,
            'org_membership': self.org_membership,
            'syllabus': syllabus,
            'groups': syllabus.grouped_entries(),
            'headings': syllabus.headings.all(),
            'available_techniques': Technique.objects.filter(organisation=self.org).exclude(pk__in=used),
            'categories': _categories(self.org),
            'systems': ProgressionSystem.objects.filter(organisation=self.org).prefetch_related('stages__syllabus'),
        })


def _categories(org):
    return list(
        Technique.objects.filter(organisation=org).exclude(category='')
        .values_list('category', flat=True).distinct().order_by('category')
    )


class SyllabusEditView(SyllabusMixin, View):
    def post(self, request, org_slug, pk):
        syllabus = self.get_syllabus()
        name = request.POST.get('name', '').strip()
        if not name:
            messages.error(request, 'Syllabus name is required.')
        else:
            syllabus.name = name
            syllabus.introduction = request.POST.get('introduction', '').strip()
            syllabus.save(update_fields=['name', 'introduction'])
            messages.success(request, 'Syllabus updated.')
        return self.to_syllabus(syllabus)


class SyllabusDeleteView(SyllabusMixin, View):
    def post(self, request, org_slug, pk):
        syllabus = self.get_syllabus()
        syllabus.delete()
        messages.success(request, f'{syllabus.name} deleted. Its techniques are still in the library.')
        return redirect('syllabus_home', org_slug=self.org.slug)


class SyllabusStagesView(SyllabusMixin, View):
    """Set which grades this syllabus is for. A grade ticked here moves off any other syllabus."""

    def post(self, request, org_slug, pk):
        syllabus = self.get_syllabus()
        stage_ids = {int(x) for x in request.POST.getlist('stage_ids') if x.isdigit()}
        stages = ProgressionStage.objects.filter(system__organisation=self.org)
        stages.filter(syllabus=syllabus).exclude(pk__in=stage_ids).update(syllabus=None)
        stages.filter(pk__in=stage_ids).update(syllabus=syllabus)
        messages.success(request, 'Grades updated.')
        return self.to_syllabus(syllabus)


class HeadingAddView(SyllabusMixin, View):
    def post(self, request, org_slug, pk):
        syllabus = self.get_syllabus()
        name = request.POST.get('name', '').strip()
        if name:
            SyllabusHeading.objects.create(
                syllabus=syllabus, name=name, order=_next_order(syllabus.headings.all()),
            )
        else:
            messages.error(request, 'Heading name is required.')
        return self.to_syllabus(syllabus)


class HeadingEditView(SyllabusMixin, View):
    def post(self, request, org_slug, pk, heading_pk):
        syllabus = self.get_syllabus()
        heading = get_object_or_404(SyllabusHeading, pk=heading_pk, syllabus=syllabus)
        action = request.POST.get('action')
        if action in ('up', 'down'):
            _move(syllabus.headings.all(), heading, action)
        elif action == 'delete':
            heading.delete()
            messages.success(request, f'Heading "{heading.name}" removed. Its techniques are kept, without a heading.')
        else:
            name = request.POST.get('name', '').strip()
            if name:
                heading.name = name
                heading.save(update_fields=['name'])
        return self.to_syllabus(syllabus)


class EntryAddView(SyllabusMixin, View):
    """Add a library technique to the syllabus, or create a new technique and add it."""

    def post(self, request, org_slug, pk):
        syllabus = self.get_syllabus()
        heading = None
        if request.POST.get('heading_id'):
            heading = get_object_or_404(SyllabusHeading, pk=request.POST['heading_id'], syllabus=syllabus)

        technique_id = request.POST.get('technique_id', '')
        if technique_id:
            technique = get_object_or_404(Technique, pk=technique_id, organisation=self.org)
        else:
            technique, error = self.technique_from_post(request)
            if error:
                messages.error(request, error)
                return self.to_syllabus(syllabus)

        if syllabus.entries.filter(technique=technique).exists():
            messages.info(request, f'{technique.name} is already in this syllabus.')
        else:
            SyllabusEntry.objects.create(
                syllabus=syllabus, technique=technique, heading=heading,
                notes=request.POST.get('notes', '').strip(),
                order=_next_order(syllabus.entries.all()),
            )
            messages.success(request, f'{technique.name} added.')
        return self.to_syllabus(syllabus)


class EntryEditView(SyllabusMixin, View):
    def post(self, request, org_slug, pk, entry_pk):
        syllabus = self.get_syllabus()
        entry = get_object_or_404(SyllabusEntry, pk=entry_pk, syllabus=syllabus)
        action = request.POST.get('action')
        if action in ('up', 'down'):
            siblings = syllabus.entries.filter(heading=entry.heading)
            _move(siblings, entry, action)
        elif action == 'delete':
            entry.delete()
            messages.success(request, f'{entry.technique.name} removed from this syllabus. It is still in the library.')
        else:
            heading_id = request.POST.get('heading_id', '')
            entry.heading = (
                get_object_or_404(SyllabusHeading, pk=heading_id, syllabus=syllabus) if heading_id else None
            )
            entry.notes = request.POST.get('notes', '').strip()
            entry.save(update_fields=['heading', 'notes'])
        return self.to_syllabus(syllabus)


class TechniqueListView(SyllabusMixin, View):
    def get(self, request, org_slug):
        techniques = (
            Technique.objects.filter(organisation=self.org)
            .annotate(syllabus_count=Count('syllabus_entries'))
        )
        q = request.GET.get('q', '').strip()
        category = request.GET.get('category', '')
        if q:
            techniques = techniques.filter(Q(name__icontains=q) | Q(description__icontains=q))
        if category:
            techniques = techniques.filter(category=category)
        return render(request, 'progression/syllabus/techniques.html', {
            'org': self.org,
            'org_membership': self.org_membership,
            'techniques': techniques,
            'categories': _categories(self.org),
            'q': q,
            'category': category,
        })

    def post(self, request, org_slug):
        technique, error = self.technique_from_post(request)
        if error:
            messages.error(request, error)
        else:
            messages.success(request, f'{technique.name} added to the library.')
        return redirect('technique_list', org_slug=self.org.slug)


class TechniqueEditView(SyllabusMixin, View):
    def post(self, request, org_slug, pk):
        technique = get_object_or_404(Technique, pk=pk, organisation=self.org)
        if request.POST.get('action') == 'delete':
            if technique.syllabus_entries.exists():
                messages.error(request, f'{technique.name} is used in a syllabus. Remove it from there first.')
            else:
                technique.delete()
                messages.success(request, f'{technique.name} deleted.')
        else:
            _, error = self.technique_from_post(request, technique)
            if error:
                messages.error(request, error)
            else:
                messages.success(request, 'Technique updated.')
        return redirect('technique_list', org_slug=self.org.slug)
