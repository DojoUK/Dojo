"""Settings → Email → Customise emails: the list of Dojo's emails, the layout, and the editor."""
import re

from django.contrib import messages
from django.core.mail import EmailMultiAlternatives
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views import View

from dojo.mixins import OrgAdminMixin

from . import blocks, catalogue, compose, connection_for
from .models import EmailTemplate

HEX_COLOUR = re.compile(r'#[0-9a-fA-F]{6}')


def _email_type(key):
    if key not in catalogue.BY_KEY:
        raise Http404('No such email.')
    return catalogue.BY_KEY[key]


class EmailTemplateListView(OrgAdminMixin, View):
    template_name = 'mailer/email_list.html'

    def get(self, request, org_slug):
        saved = {t.key: t for t in EmailTemplate.objects.filter(organisation=self.org)}
        return render(request, self.template_name, {
            'org': self.org, 'org_membership': self.org_membership,
            'rows': [(email_type, saved.get(email_type.key)) for email_type in catalogue.EMAIL_TYPES],
            'layout': compose.layout_options(self.org),
            'club_colour': self.org.theme()['sidebar_color'],
        })

    def post(self, request, org_slug):
        colour = request.POST.get('header_colour', '').strip()
        if request.POST.get('use_club_colour') or not colour:
            colour = ''
        elif not HEX_COLOUR.fullmatch(colour):
            messages.error(request, 'The header colour should look like #1E3A5F.')
            return redirect('org_email_templates', org_slug=self.org.slug)
        self.org.settings = {**(self.org.settings or {}), 'email_layout': {
            'header_colour': colour,
            'show_logo': bool(request.POST.get('show_logo')),
            'header_text': request.POST.get('header_text', '').strip()[:100],
            'footer_text': request.POST.get('footer_text', '').strip()[:500],
        }}
        self.org.save(update_fields=['settings'])
        messages.success(request, 'Email layout saved.')
        return redirect('org_email_templates', org_slug=self.org.slug)


def _posted(request):
    return {
        'subject': request.POST.get('subject', '').strip(),
        'mode': 'html' if request.POST.get('mode') == 'html' else 'wording',
        'body': request.POST.get('body', '').replace('\r\n', '\n'),
        'html_document': request.POST.get('html', '').replace('\r\n', '\n'),
    }


def _unknown_fields(email_type, *texts):
    known = {name for name, _ in email_type.all_fields} | {name for name, _ in email_type.blocks}
    return sorted({name for text in texts for name in compose.FIELD.findall(text)} - known)


class EmailTemplateEditView(OrgAdminMixin, View):
    template_name = 'mailer/email_edit.html'

    def context(self, email_type, form):
        saved = EmailTemplate.objects.filter(organisation=self.org, key=email_type.key).first()
        return {
            'org': self.org, 'org_membership': self.org_membership, 'email_type': email_type, 'saved': saved,
            'form': form, 'preview_url': reverse('org_email_template_preview',
                                                 kwargs={'org_slug': self.org.slug, 'key': email_type.key}),
        }

    def get(self, request, org_slug, key):
        email_type = _email_type(key)
        saved = EmailTemplate.objects.filter(organisation=self.org, key=key).first()
        form = {
            'subject': saved.subject if saved else email_type.default_subject,
            'mode': saved.mode if saved else 'wording',
            'body': saved.body if saved and saved.body else email_type.default_body,
            'html_document': saved.html if saved else '',
        }
        return render(request, self.template_name, self.context(email_type, form))

    def post(self, request, org_slug, key):
        email_type = _email_type(key)
        action = request.POST.get('action', 'save')
        here = reverse('org_email_template_edit', kwargs={'org_slug': self.org.slug, 'key': key})
        if action == 'reset':
            EmailTemplate.objects.filter(organisation=self.org, key=key).delete()
            messages.success(request, f'"{email_type.label}" is back to Dojo\'s default.')
            return redirect(here)

        form = _posted(request)
        errors = []
        if not form['subject']:
            errors.append('Enter a subject.')
        if form['mode'] == 'wording' and not form['body'].strip():
            errors.append('Write the email, or reset it to the default.')
        if form['mode'] == 'html' and not form['html_document'].strip():
            errors.append('Add the email\'s HTML, or press "Start from Dojo\'s design".')
        if errors:
            for error in errors:
                messages.error(request, error)
            return render(request, self.template_name, self.context(email_type, form), status=400)

        unknown = _unknown_fields(email_type, form['subject'], form['body'] if form['mode'] == 'wording'
                                  else form['html_document'])
        if unknown:
            messages.warning(request, 'These aren\'t fields this email knows, so they\'ll appear exactly as typed: '
                             + ', '.join('{' + name + '}' for name in unknown))

        if action == 'test':
            if not request.user.email:
                messages.error(request, 'Your account has no email address to send a test to.')
            else:
                values, example_blocks = blocks.example(self.org, key)
                subject, document, text = compose.render(self.org, key, values, example_blocks, **form)
                message = EmailMultiAlternatives(subject=f'[Test] {subject}', body=text, to=[request.user.email],
                                                 connection=connection_for(self.org))
                message.attach_alternative(document, 'text/html')
                try:
                    message.send()
                    messages.success(request, f'Test sent to {request.user.email}, with example details filled in.')
                except Exception as error:  # shown to the admin testing it
                    messages.error(request, f'The test email failed: {error}')
            return render(request, self.template_name, self.context(email_type, form))

        EmailTemplate.objects.update_or_create(organisation=self.org, key=key, defaults={
            'subject': form['subject'][:200], 'mode': form['mode'],
            'body': form['body'], 'html': form['html_document'],
        })
        messages.success(request, f'"{email_type.label}" saved. Every email of this kind now uses it.')
        return redirect(here)


class EmailTemplatePreviewView(OrgAdminMixin, View):
    """The editor's live preview, filled with example details."""

    def post(self, request, org_slug, key):
        email_type = _email_type(key)
        form = _posted(request)
        values, example_blocks = blocks.example(self.org, key)
        if request.POST.get('start_from_design'):
            # Dojo's design as HTML, with the fields and blocks left in for the club to keep or move.
            placeholders = {name: '{' + name + '}' for name, _ in email_type.all_fields}
            markers = {name: {'html': '{' + name + '}', 'text': ''} for name, _ in email_type.blocks}
            _, document, _ = compose.render(self.org, key, placeholders, markers, subject=form['subject'],
                                            mode='wording', body=form['body'] or email_type.default_body)
            return JsonResponse({'html': document})
        subject, document, _ = compose.render(self.org, key, values, example_blocks, **form)
        return JsonResponse({'subject': subject, 'html': document,
                             'unknown': _unknown_fields(email_type, form['subject'], form['body']
                                                        if form['mode'] == 'wording' else form['html_document'])})
