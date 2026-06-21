from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, UpdateView
from dojo.mixins import OrgAdminMixin
from .forms import GuardianFormSet, MemberForm, build_custom_field_widgets, extract_custom_field_values
from .models import Member


class MemberListView(OrgAdminMixin, ListView):
    template_name = 'members/list.html'
    context_object_name = 'members'
    paginate_by = 50

    def get_queryset(self):
        qs = Member.objects.filter(organisation=self.org).prefetch_related('guardians').order_by('name')
        q = self.request.GET.get('q', '').strip()
        if q:
            qs = qs.filter(name__icontains=q) | qs.filter(email__icontains=q) | qs.filter(phone__icontains=q)
        show = self.request.GET.get('show', 'active')
        if show == 'inactive':
            qs = qs.filter(is_active=False)
        elif show == 'all':
            pass
        else:
            qs = qs.filter(is_active=True)
        return qs

    def get_template_names(self):
        if self.request.htmx:
            return ['members/partials/member_rows.html']
        return [self.template_name]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['q'] = self.request.GET.get('q', '')
        context['show'] = self.request.GET.get('show', 'active')
        return context


class MemberCreateView(OrgAdminMixin, CreateView):
    template_name = 'members/form.html'
    form_class = MemberForm

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Add member'
        if self.request.POST:
            context['guardian_formset'] = GuardianFormSet(self.request.POST)
        else:
            context['guardian_formset'] = GuardianFormSet()
        context['custom_fields'] = build_custom_field_widgets(self.org, self.request.POST or None)
        return context

    def form_valid(self, form):
        context = self.get_context_data()
        guardian_formset = context['guardian_formset']
        if not guardian_formset.is_valid():
            return self.form_invalid(form)
        member = form.save(commit=False)
        member.organisation = self.org
        member.custom_field_values = extract_custom_field_values(self.org, self.request.POST)
        member.save()
        guardian_formset.instance = member
        guardian_formset.save()
        messages.success(self.request, f'{member.name} added successfully.')
        return redirect('member_detail', org_slug=self.org.slug, pk=member.pk)


class MemberDetailView(OrgAdminMixin, DetailView):
    template_name = 'members/detail.html'
    context_object_name = 'member'

    def get_object(self):
        return get_object_or_404(Member, pk=self.kwargs['pk'], organisation=self.org)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['guardians'] = self.object.guardians.all()
        context['invoices'] = self.object.invoices.order_by('-created_at')[:5]
        from .models import CustomField
        values = self.object.custom_field_values or {}
        context['custom_fields'] = [
            {
                'field': cf,
                'value': values.get(str(cf.pk), ''),
                'is_bool': cf.field_type == CustomField.FieldType.BOOLEAN,
            }
            for cf in CustomField.objects.filter(organisation=self.org)
        ]
        return context


class MemberUpdateView(OrgAdminMixin, UpdateView):
    template_name = 'members/form.html'
    form_class = MemberForm

    def get_object(self):
        return get_object_or_404(Member, pk=self.kwargs['pk'], organisation=self.org)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = f'Edit {self.object.name}'
        context['member'] = self.object
        if self.request.POST:
            context['guardian_formset'] = GuardianFormSet(self.request.POST, instance=self.object)
        else:
            context['guardian_formset'] = GuardianFormSet(instance=self.object)
        context['custom_fields'] = build_custom_field_widgets(
            self.org,
            self.request.POST or None,
            initial_values=self.object.custom_field_values,
        )
        return context

    def form_valid(self, form):
        context = self.get_context_data()
        guardian_formset = context['guardian_formset']
        if not guardian_formset.is_valid():
            return self.form_invalid(form)
        member = form.save(commit=False)
        member.custom_field_values = extract_custom_field_values(self.org, self.request.POST)
        member.save()
        guardian_formset.instance = member
        guardian_formset.save()
        messages.success(self.request, f'{member.name} updated successfully.')
        return redirect('member_detail', org_slug=self.org.slug, pk=member.pk)


class MemberArchiveView(OrgAdminMixin, View):
    def post(self, request, org_slug, pk):
        member = get_object_or_404(Member, pk=pk, organisation=self.org)
        member.is_active = not member.is_active
        member.save(update_fields=['is_active'])
        status = 'reactivated' if member.is_active else 'archived'
        messages.success(request, f'{member.name} {status}.')
        return redirect('member_detail', org_slug=self.org.slug, pk=member.pk)
