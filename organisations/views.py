from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.models import User
from django.contrib.auth.views import PasswordChangeView
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.db.models import Q, Sum
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View
from django.views.generic import ListView, TemplateView
from auditlog.models import LogEntry
from dojo.mixins import CapabilityMixin, OrgAdminMixin, OrgMixin
from dojo.version import RELEASES_URL, get_version_info
from .models import Announcement, Organisation, OrganisationMember, StaffHoliday
from members.models import CustomField
from accounts.policy import device_for, two_factor_required
from mailer.forms import provider_rows as email_provider_rows
from mailer import blocks as email_blocks
from mailer import compose
from billing.payment_forms import form_context as payment_form_context


class SetupView(View):
    """
    First-run, browser-only bootstrap: creates the organisation and its
    first admin account. Locks itself out once an organisation exists.
    """
    template_name = 'org/setup.html'

    def dispatch(self, request, *args, **kwargs):
        if Organisation.objects.exists():
            return redirect('login')
        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        from .features import FEATURES, default_enabled, feature_rows
        from .optional_fields import effective_fields, field_groups
        return render(request, self.template_name, {
            'demo_mode': request.GET.get('demo') == '1',
            'feature_rows': feature_rows({f.key for f in FEATURES if default_enabled(f)}),
            'field_groups': field_groups(effective_fields({})),
            **email_provider_rows(),
            **payment_form_context(),
        })

    def post(self, request):
        if request.POST.get('mode') == 'demo':
            return self._seed_demo(request)
        return self._create_org(request)

    def _seed_demo(self, request):
        call_command('seed_demo')
        org = Organisation.objects.get(slug='mockingham-martial-arts')
        org.settings['demo'] = True
        org.save(update_fields=['settings'])

        admin = User.objects.get(username='admin')
        login(request, admin)
        messages.info(request, "Demo instance seeded — you're logged in as “admin” (password: admin).")
        return redirect('org_dashboard', org_slug=org.slug)

    def _create_org(self, request):
        org_name = request.POST.get('org_name', '').strip()
        username = request.POST.get('username', '').strip()
        admin_email = request.POST.get('admin_email', '').strip()
        password = request.POST.get('password', '')
        password_confirm = request.POST.get('password_confirm', '')

        errors = []
        if not org_name:
            errors.append('Organisation name is required.')
        if not username:
            errors.append('Username is required.')
        elif User.objects.filter(username=username).exists():
            errors.append('That username is already taken.')
        if not password:
            errors.append('Password is required.')
        elif password != password_confirm:
            errors.append('Passwords do not match.')
        elif len(password) < 8:
            errors.append('Password must be at least 8 characters.')

        from .features import choices_from_keys, effective_features, feature_rows
        from .optional_fields import choices_from_keys as field_choices
        from .optional_fields import effective_fields, field_groups, labels_from_post
        feature_choices = choices_from_keys(request.POST.getlist('features'))
        optional_field_choices = field_choices(request.POST.getlist('optional_fields'))
        labels = labels_from_post(request.POST)
        from mailer.forms import apply_post
        from mailer.models import EmailSettings
        email_settings = EmailSettings()
        errors += apply_post(email_settings, request.POST)
        from billing.models import PaymentSettings
        from billing.payment_forms import apply_post as apply_payment_post
        payment_settings = PaymentSettings()
        errors += apply_payment_post(payment_settings, request.POST)

        if errors:
            return render(request, self.template_name, {
                'feature_rows': feature_rows(effective_features(feature_choices)),
                'field_groups': field_groups(effective_fields(optional_field_choices), labels),
                'errors': errors,
                'demo_mode': request.GET.get('demo') == '1',
                'org_name': org_name,
                'username': username,
                'admin_email': admin_email,
                'org_email': request.POST.get('org_email', '').strip(),
                'org_phone': request.POST.get('org_phone', '').strip(),
                'org_website': request.POST.get('org_website', '').strip(),
                **email_provider_rows(post=request.POST),
                **payment_form_context(post=request.POST),
            })

        user = User.objects.create_superuser(username=username, email=admin_email, password=password)
        org = Organisation.objects.create(
            name=org_name,
            email=request.POST.get('org_email', '').strip(),
            phone=request.POST.get('org_phone', '').strip(),
            website=request.POST.get('org_website', '').strip(),
            features=feature_choices,
            optional_fields=optional_field_choices,
            # New clubs start with two-step sign-in required; Settings → Security can relax it.
            settings={**labels, 'two_factor': Organisation.TWO_FACTOR_ALL},
        )
        OrganisationMember.objects.create(user=user, organisation=org, role=OrganisationMember.Role.ORG_ADMIN)
        # "Not yet" saves nothing, so the server's EMAIL_* settings (if any) carry on applying.
        if email_settings.provider != 'log':
            email_settings.organisation = org
            email_settings.save()
        if email_settings.provider == 'dojo_mail':
            email_settings.dkim_private_key(create=True)
        payment_settings.organisation = org
        payment_settings.save()

        login(request, user)
        return redirect('org_dashboard', org_slug=org.slug)


class ReseedDemoView(OrgAdminMixin, View):
    """Re-runs the demo seed for orgs that were bootstrapped via dummy mode."""

    def post(self, request, org_slug):
        if not self.org.settings.get('demo'):
            raise PermissionDenied
        slug = self.org.slug
        call_command('seed_demo', '--flush')
        org = Organisation.objects.get(slug=slug)
        org.settings['demo'] = True
        org.save(update_fields=['settings'])
        messages.success(request, 'Demo data has been reset.')
        return redirect('org_dashboard', org_slug=org.slug)


class DismissOnboardingView(OrgAdminMixin, View):
    """Permanently hides the getting-started checklist/popup for this org."""

    def post(self, request, org_slug):
        self.org.settings['onboarding_dismissed'] = True
        self.org.save(update_fields=['settings'])
        return redirect('org_dashboard', org_slug=self.org.slug)


class DashboardView(OrgMixin, TemplateView):
    template_name = 'org/dashboard.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        from classes.models import Attendance, ClassMember, Session
        from members.models import Member

        today = date.today()
        week_end = today + timedelta(days=7)

        is_admin = self.request.user.is_superuser or (
            self.org_membership and self.org_membership.role == 'org_admin'
        )

        active_members = self.org.club_members.filter(is_active=True)
        member_count = active_members.count()

        enrolled_ids = ClassMember.objects.filter(
            assigned_class__organisation=self.org
        ).values_list('member_id', flat=True)
        unenrolled_count = active_members.exclude(pk__in=enrolled_ids).count()

        sessions_qs = Session.objects.filter(
            assigned_class__organisation=self.org, date__gte=today, date__lte=week_end
        ).select_related('assigned_class').order_by('date')

        if not is_admin:
            sessions_qs = sessions_qs.filter(
                assigned_class__coaches__user=self.request.user
            )

        upcoming_sessions = sessions_qs

        sessions_with_status = []
        for session in upcoming_sessions:
            taken = Attendance.objects.filter(session=session).exists()
            enrolled = ClassMember.objects.filter(assigned_class=session.assigned_class).count()
            present = Attendance.objects.filter(session=session, present=True).count() if taken else None
            sessions_with_status.append({
                'session': session,
                'taken': taken,
                'enrolled': enrolled,
                'present': present,
            })

        from billing.models import Invoice, Payment
        from django.db.models import Sum, Q
        from members.models import MemberApplication

        first_of_month = today.replace(day=1)
        four_weeks_ago = today - timedelta(weeks=4)
        two_weeks_ago = today - timedelta(weeks=2)

        revenue_month = Payment.objects.filter(
            invoice__organisation=self.org,
            paid_at__date__gte=first_of_month,
        ).aggregate(t=Sum('amount'))['t'] or 0

        outstanding = Invoice.objects.filter(
            organisation=self.org, status=Invoice.Status.UNPAID
        ).aggregate(t=Sum('amount'))['t'] or 0

        new_members_month = active_members.filter(joined_date__gte=first_of_month).count()

        from classes.models import Attendance
        total_records = Attendance.objects.filter(
            session__assigned_class__organisation=self.org,
            session__date__gte=four_weeks_ago,
        ).count()
        present_records = Attendance.objects.filter(
            session__assigned_class__organisation=self.org,
            session__date__gte=four_weeks_ago,
            present=True,
        ).count()
        attendance_rate = round(present_records / total_records * 100) if total_records else None

        from django.db.models import Max
        at_risk = active_members.annotate(
            last_attended=Max(
                'attendance__session__date',
                filter=Q(attendance__present=True),
            )
        ).filter(
            Q(last_attended__lt=two_weeks_ago) | Q(last_attended__isnull=True)
        ).count()

        pending_applications = MemberApplication.objects.filter(
            organisation=self.org, status=MemberApplication.Status.PENDING
        ).count() if is_admin and self.org.has_feature('applications') else 0

        thirty_days = today + timedelta(days=30)
        track_licences = is_admin and self.org.has_field('licence')
        licences_expiring = active_members.filter(
            licence_expiry__lte=thirty_days, licence_expiry__gte=today
        ).count() if track_licences else 0
        licences_expired = active_members.filter(
            licence_expiry__lt=today
        ).count() if track_licences else 0

        # Staff expiry warnings only cover the checks the club has switched on.
        staff_checks = [
            (expiry_field, label)
            for key, expiry_field, label in (
                ('dbs', 'dbs_expiry', 'DBS'),
                ('coaching_licence', 'coaching_licence_expiry', self.org.coaching_licence_label),
            )
            if self.org.has_field(key)
        ]
        staff_expiring = staff_expired = 0
        if is_admin and staff_checks:
            staff = OrganisationMember.objects.filter(organisation=self.org)
            expiring_q, expired_q = Q(pk__in=[]), Q(pk__in=[])
            for expiry_field, _ in staff_checks:
                expiring_q |= Q(**{f'{expiry_field}__lte': thirty_days, f'{expiry_field}__gte': today})
                expired_q |= Q(**{f'{expiry_field}__lt': today})
            staff_expiring = staff.filter(expiring_q).count()
            staff_expired = staff.filter(expired_q).count()

        onboarding = None
        if is_admin and not self.org.settings.get('onboarding_dismissed'):
            from classes.models import ClassCoach
            has_member = member_count > 0
            has_class = self.org.classes.exists()
            has_coach = ClassCoach.objects.filter(assigned_class__organisation=self.org).exists() if has_class else False
            has_invoice = (
                not self.org.has_feature('billing')
                or Invoice.objects.filter(organisation=self.org).exists()
            )
            if not (has_member and has_class and has_coach and has_invoice):
                onboarding = {
                    'has_member': has_member,
                    'has_class': has_class,
                    'has_coach': has_coach,
                    'has_invoice': has_invoice,
                }

        unsigned_waivers_count = 0
        if is_admin and self.org.has_feature('documents'):
            from documents.models import SignedWaiver, WaiverTemplate
            required_templates = WaiverTemplate.objects.filter(
                organisation=self.org, is_active=True, is_required=True
            )
            if required_templates.exists():
                signed_member_ids = set(
                    SignedWaiver.objects.filter(
                        template__in=required_templates
                    ).values_list('member_id', flat=True)
                )
                unsigned_waivers_count = active_members.exclude(
                    pk__in=signed_member_ids
                ).count()

        context.update({
            'member_count': member_count,
            'class_count': self.org.classes.count(),
            'unenrolled_count': unenrolled_count,
            'sessions_this_week': len(upcoming_sessions),
            'upcoming': sessions_with_status,
            'today': today,
            'is_admin': is_admin,
            'revenue_month': revenue_month,
            'outstanding': outstanding,
            'new_members_month': new_members_month,
            'attendance_rate': attendance_rate,
            'at_risk_count': at_risk,
            'pending_applications': pending_applications,
            'licences_expiring': licences_expiring,
            'licences_expired': licences_expired,
            'staff_expiring': staff_expiring,
            'staff_expired': staff_expired,
            'staff_check_names': ' or '.join(label for _, label in staff_checks),
            'unsigned_waivers_count': unsigned_waivers_count,
            'onboarding': onboarding,
        })
        return context


class AuditLogView(OrgAdminMixin, ListView):
    template_name = 'org/audit_log.html'
    context_object_name = 'entries'
    paginate_by = 50

    def get_queryset(self):
        from members.models import Guardian, Member
        from classes.models import Attendance, Class, ClassCoach, ClassMember, Session

        member_pks = list(
            Member.objects.filter(organisation=self.org).values_list('pk', flat=True)
        )
        class_pks = list(
            Class.objects.filter(organisation=self.org).values_list('pk', flat=True)
        )
        guardian_pks = list(
            Guardian.objects.filter(member__organisation=self.org).values_list('pk', flat=True)
        )
        session_pks = list(
            Session.objects.filter(assigned_class__organisation=self.org).values_list('pk', flat=True)
        )
        attendance_pks = list(
            Attendance.objects.filter(session__assigned_class__organisation=self.org).values_list('pk', flat=True)
        )
        classmember_pks = list(
            ClassMember.objects.filter(assigned_class__organisation=self.org).values_list('pk', flat=True)
        )
        classcoach_pks = list(
            ClassCoach.objects.filter(assigned_class__organisation=self.org).values_list('pk', flat=True)
        )

        def ct(model):
            return ContentType.objects.get_for_model(model)

        q = (
            Q(content_type=ct(Member), object_id__in=member_pks)
            | Q(content_type=ct(Guardian), object_id__in=guardian_pks)
            | Q(content_type=ct(Class), object_id__in=class_pks)
            | Q(content_type=ct(Session), object_id__in=session_pks)
            | Q(content_type=ct(Attendance), object_id__in=attendance_pks)
            | Q(content_type=ct(ClassMember), object_id__in=classmember_pks)
            | Q(content_type=ct(ClassCoach), object_id__in=classcoach_pks)
        )

        return (
            LogEntry.objects.filter(q)
            .select_related('actor', 'content_type')
            .order_by('-timestamp')
        )


class StaffListView(OrgAdminMixin, View):
    def get(self, request, org_slug):
        staff = (
            OrganisationMember.objects.filter(organisation=self.org)
            .select_related('user', 'user__two_factor')
            .order_by('user__first_name', 'user__username')
        )
        today = date.today()
        holidays = (
            StaffHoliday.objects.filter(member__organisation=self.org)
            .select_related('member__user')
            .order_by('-end_date')
        )
        return render(request, 'org/staff.html', {
            'org': self.org,
            'org_membership': self.org_membership,
            'staff': staff,
            'roles': OrganisationMember.Role.choices,
            'today': today,
            'thirty_days': today + timedelta(days=30),
            'holidays': holidays,
        })

    def _account_action_refusal(self, user, name):
        """Why the signed-in admin can't change `user`'s sign-in, or '' if they can."""
        actor = self.request.user
        if user == actor:
            return 'Use your own account page to change your sign-in.'
        if user.is_superuser and not actor.is_superuser:
            return f'{name} is a site superuser; only another superuser can change their sign-in.'
        if not actor.is_superuser and user.organisation_memberships.exclude(organisation=self.org).exists():
            return f'{name} also works for another club on this Dojo, so only a site superuser can change their sign-in.'
        return ''

    def _send_password_reset(self, user):
        from urllib.parse import urlsplit

        from django.conf import settings as django_settings
        from django.contrib.auth.forms import PasswordResetForm
        from django.contrib.auth.tokens import default_token_generator
        from django.utils.encoding import force_bytes
        from django.utils.http import urlsafe_base64_encode
        site = urlsplit(django_settings.SITE_URL)
        context = {
            'email': user.email,
            'domain': site.netloc,
            'site_name': self.org.name,
            'uid': urlsafe_base64_encode(force_bytes(user.pk)),
            'user': user,
            'token': default_token_generator.make_token(user),
            'protocol': site.scheme or 'https',
        }
        PasswordResetForm().send_mail(
            'registration/password_reset_subject.txt', 'registration/password_reset_email.html',
            context, None, user.email,
        )

    def post(self, request, org_slug):
        action = request.POST.get('action')

        if action == 'add':
            username = request.POST.get('username', '').strip()
            first_name = request.POST.get('first_name', '').strip()
            last_name = request.POST.get('last_name', '').strip()
            password = request.POST.get('password', '').strip()
            role = request.POST.get('role', OrganisationMember.Role.COACH)

            if not username or not password:
                messages.error(request, 'Username and password are required.')
                return redirect('org_staff', org_slug=self.org.slug)

            if User.objects.filter(username=username).exists():
                messages.error(request, f'Username "{username}" is already taken.')
                return redirect('org_staff', org_slug=self.org.slug)

            user = User.objects.create_user(
                username=username,
                password=password,
                first_name=first_name,
                last_name=last_name,
            )
            OrganisationMember.objects.create(user=user, organisation=self.org, role=role)
            messages.success(request, f'{user.get_full_name() or username} added as {role}.')

        elif action == 'change_role':
            member_pk = request.POST.get('member_pk')
            new_role = request.POST.get('role')
            om = get_object_or_404(OrganisationMember, pk=member_pk, organisation=self.org)
            if om.user == request.user:
                messages.error(request, "You can't change your own role.")
            else:
                om.role = new_role
                om.save(update_fields=['role'])
                messages.success(request, f'Role updated for {om.user.get_full_name() or om.user.username}.')

        elif action == 'remove':
            member_pk = request.POST.get('member_pk')
            om = get_object_or_404(OrganisationMember, pk=member_pk, organisation=self.org)
            if om.user == request.user:
                messages.error(request, "You can't remove yourself.")
            else:
                name = om.user.get_full_name() or om.user.username
                om.delete()
                messages.success(request, f'{name} removed from {self.org.name}.')

        elif action == 'update_qualifications':
            member_pk = request.POST.get('member_pk')
            om = get_object_or_404(OrganisationMember, pk=member_pk, organisation=self.org)
            # Only fields the club has switched on are on the form; leave the rest untouched.
            from .optional_fields import STAFF, enabled_model_fields
            fields = enabled_model_fields(self.org, STAFF)
            for name in fields:
                value = request.POST.get(name, '').strip()
                setattr(om, name, (value or None) if name.endswith('_expiry') else value)
            if fields:
                om.save(update_fields=fields)
            messages.success(request, 'Details updated.')

        elif action in ('reset_two_factor', 'send_password_reset', 'lock_account', 'unlock_account'):
            om = get_object_or_404(OrganisationMember, pk=request.POST.get('member_pk'), organisation=self.org)
            user = om.user
            name = user.get_full_name() or user.username
            refusal = self._account_action_refusal(user, name)
            if refusal:
                messages.error(request, refusal)
            elif action == 'reset_two_factor':
                from accounts.models import remove_two_factor
                remove_two_factor(user)
                messages.success(request, f'Two-step sign-in reset for {name}. They\'ll set it up again '
                                          'next time they sign in if the club requires it.')
            elif action == 'send_password_reset':
                if not user.email:
                    messages.error(request, f'{name} has no email address. Add one on their account first.')
                else:
                    self._send_password_reset(user)
                    messages.success(request, f'Password reset link emailed to {name}.')
            else:
                # Inactive accounts can't sign in, and Django ends their open sessions on the next request.
                user.is_active = action == 'unlock_account'
                user.save(update_fields=['is_active'])
                messages.success(request, f'{name} can sign in again.' if user.is_active
                                 else f'{name} is locked out and signed out everywhere.')
            return redirect(f"{reverse('org_staff', kwargs={'org_slug': self.org.slug})}")

        elif action == 'update_capabilities':
            member_pk = request.POST.get('member_pk')
            om = get_object_or_404(OrganisationMember, pk=member_pk, organisation=self.org)
            granted = set(request.POST.getlist('capabilities'))
            fields = []
            for name, _, _ in OrganisationMember.CAPABILITIES:
                setattr(om, f'can_{name}', name in granted)
                fields.append(f'can_{name}')
            om.save(update_fields=fields)
            messages.success(request, f'Permissions updated for {om.user.get_full_name() or om.user.username}.')

        elif action == 'add_holiday':
            member_pk = request.POST.get('member_pk')
            om = get_object_or_404(OrganisationMember, pk=member_pk, organisation=self.org)
            start_raw = request.POST.get('start_date', '').strip()
            end_raw = request.POST.get('end_date', '').strip()
            note = request.POST.get('note', '').strip()

            if not start_raw or not end_raw:
                messages.error(request, 'Start and end date are required.')
                return redirect('org_staff', org_slug=self.org.slug)

            try:
                start_date = date.fromisoformat(start_raw)
                end_date = date.fromisoformat(end_raw)
            except ValueError:
                messages.error(request, 'Invalid date.')
                return redirect('org_staff', org_slug=self.org.slug)

            if end_date < start_date:
                messages.error(request, 'End date must be on or after the start date.')
                return redirect('org_staff', org_slug=self.org.slug)

            StaffHoliday.objects.create(
                member=om, start_date=start_date, end_date=end_date, note=note,
            )
            name = om.user.get_full_name() or om.user.username
            messages.success(request, f'Holiday added for {name} ({start_date:%d %b %Y} – {end_date:%d %b %Y}).')

        elif action == 'remove_holiday':
            holiday_pk = request.POST.get('holiday_pk')
            holiday = get_object_or_404(StaffHoliday, pk=holiday_pk, member__organisation=self.org)
            holiday.delete()
            messages.success(request, 'Holiday removed.')

        return redirect('org_staff', org_slug=self.org.slug)


class TestEmailView(OrgAdminMixin, View):
    def post(self, request, org_slug):
        from django.core.mail import send_mail
        recipient = request.user.email
        if not recipient:
            messages.error(request, 'Your account has no email address. Add one on your account page first.')
            return redirect(f"{reverse('org_settings', kwargs={'org_slug': self.org.slug})}#email")
        from mailer import connection_for
        try:
            send_mail(
                subject=f'Test email from {self.org.name} (Dojo)',
                message=f'This is a test email from Dojo to confirm your email settings are working.\n\n— {self.org.name}',
                from_email=None,
                recipient_list=[recipient],
                connection=connection_for(self.org),
            )
            messages.success(request, f'Test email sent to {recipient}. If it doesn\'t arrive in a few minutes, check spam.')
        except Exception as e:  # any failure is shown to the admin testing their settings
            messages.error(request, f'Email failed: {e}')
        return redirect(f"{reverse('org_settings', kwargs={'org_slug': self.org.slug})}#email")


class OrgSettingsView(OrgAdminMixin, View):
    template_name = 'org/settings.html'

    PRESETS = [
        {'name': 'Dojo Blue',      'sidebar': '#1E3A5F', 'accent': '#2563EB'},
        {'name': 'Forest',         'sidebar': '#1A3C34', 'accent': '#059669'},
        {'name': 'Indigo',         'sidebar': '#312e81', 'accent': '#4F46E5'},
        {'name': 'Charcoal',       'sidebar': '#1f2937', 'accent': '#6366f1'},
        {'name': 'Burgundy',       'sidebar': '#4a0e1a', 'accent': '#dc2626'},
        {'name': 'Midnight',       'sidebar': '#0f172a', 'accent': '#0ea5e9'},
        {'name': 'Slate',          'sidebar': '#334155', 'accent': '#f59e0b'},
        {'name': 'Teal',           'sidebar': '#134e4a', 'accent': '#14b8a6'},
    ]

    # (key, label, Bootstrap icon, help) for each settings tab; templates/org/settings/_<key>.html.
    SECTIONS = (
        ('general', 'General', 'building', 'Your club\'s name and contact details.'),
        ('appearance', 'Appearance', 'palette', 'Colours and logo for your club\'s Dojo.'),
        ('features', 'Features', 'toggles', 'Switch off anything your club doesn\'t use and it disappears from Dojo. '
                                            'Nothing is deleted: switch a feature back on and its data is still there.'),
        ('fields', 'Fields', 'input-cursor-text', 'Which built-in details Dojo records. Switched-off fields are hidden '
                                                  'everywhere, but anything already recorded is kept.'),
        ('security', 'Security', 'shield-lock', ''),
        ('payments', 'Payments', 'credit-card', 'How members pay their invoices. Every invoice email links to a page '
                                                'showing the ways you choose here.'),
        ('email', 'Email', 'envelope', 'How Dojo sends emails to members and staff.'),
        ('more', 'More', 'three-dots', 'Other settings pages.'),
    )

    def _payments_context(self):
        from billing.payment_forms import form_context
        from billing.payments import settings_for
        from billing.stripe_setup import check_connection, webhook_url
        payment_settings = settings_for(self.org)
        from jobs.registry import is_running
        context = {**form_context(payment_settings), 'payment_settings': payment_settings,
                   'stripe_webhook_url': webhook_url(self.org), 'scheduler_running': is_running()}
        if payment_settings and payment_settings.accept_card and self.request.GET.get('check_stripe'):
            context['stripe_check'] = check_connection(payment_settings.stripe_secret_key)
        return context

    def _email_context(self):
        from mailer.forms import provider_rows
        from mailer.models import EmailSettings
        email_settings = EmailSettings.objects.filter(organisation=self.org).first()
        context = {**provider_rows(email_settings), 'email_settings': email_settings}
        if email_settings is None:
            from django.conf import settings as django_settings
            fallback = django_settings.DOJO_FALLBACK_EMAIL_BACKEND
            context['email_fallback'] = (
                'printing emails to the server log instead of sending them' if fallback.endswith('console.EmailBackend')
                else f'using the server\'s email settings from .env ({django_settings.EMAIL_HOST or fallback})'
            )
        if email_settings and self.request.GET.get('check_connection'):
            from mailer import connectivity
            context['email_connection_check'] = connectivity.check(email_settings)
        if email_settings and email_settings.provider == 'dojo_mail':
            context['email_dns_records'] = email_settings.dns_records()
            if self.request.GET.get('check_dns'):
                from mailer import dns_check
                context['email_dns_check'] = dns_check.check(email_settings)
        return context

    def _context(self):
        from .features import feature_rows
        from .optional_fields import field_groups
        return {
            'settings_sections': [section for section in self.SECTIONS
                                  if section[0] != 'payments' or self.org.has_feature('billing')],
            'version': get_version_info(),
            'org_url': self.request.build_absolute_uri(reverse('org_dashboard', kwargs={'org_slug': self.org.slug})),
            'org_url_display': self.request.get_host() + reverse('org_dashboard', kwargs={'org_slug': self.org.slug}),
            'releases_url': RELEASES_URL,
            'org': self.org,
            'org_membership': self.org_membership,
            'presets': self.PRESETS,
            'feature_rows': feature_rows(self.org.enabled_features),
            'field_groups': field_groups(self.org.enabled_fields, self.org.settings),
            'session_timeout_choices': Organisation.SESSION_TIMEOUT_CHOICES,
            'two_factor_choices': Organisation.TWO_FACTOR_CHOICES,
            **self._email_context(),
            **self._payments_context(),
            'staff_without_two_factor': (
                OrganisationMember.objects.filter(organisation=self.org, user__two_factor__isnull=True)
                .select_related('user').order_by('user__first_name', 'user__username')
            ),
        }

    def get(self, request, org_slug):
        from django.shortcuts import render
        return render(request, self.template_name, self._context())

    def post(self, request, org_slug):
        action = request.POST.get('action', 'general')

        if action == 'features':
            from .features import choices_from_keys
            self.org.set_features(choices_from_keys(request.POST.getlist('features')))
            self.org.save(update_fields=['features'])
            messages.success(request, 'Features saved.')
            return redirect(f"{reverse('org_settings', kwargs={'org_slug': self.org.slug})}#features")

        if action == 'payments':
            from billing.models import PaymentSettings
            from billing.payment_forms import apply_post
            payment_settings = (PaymentSettings.objects.filter(organisation=self.org).first()
                                or PaymentSettings(organisation=self.org))
            errors = apply_post(payment_settings, request.POST)
            for error in errors:
                messages.error(request, error)
            if not errors:
                payment_settings.save()
                messages.success(request, 'Payment settings saved.')
            return redirect(f"{reverse('org_settings', kwargs={'org_slug': self.org.slug})}#payments")

        if action == 'stripe_webhook':
            import stripe

            from billing.payments import settings_for
            from billing.stripe_setup import create_webhook
            payment_settings = settings_for(self.org)
            if not (payment_settings and payment_settings.stripe_secret_key):
                messages.error(request, 'Save your Stripe keys first.')
            else:
                try:
                    secret = create_webhook(self.org, payment_settings.stripe_secret_key)
                except stripe.error.StripeError as error:
                    messages.error(request, f'Stripe couldn\'t set up the webhook: {error.user_message or error}')
                else:
                    from billing.stripe_setup import WEBHOOK_EVENTS
                    payment_settings.set_secrets({**payment_settings.get_secrets(), 'stripe_webhook_secret': secret,
                                                  'stripe_webhook_events': ' '.join(WEBHOOK_EVENTS)})
                    payment_settings.save(update_fields=['secrets'])
                    messages.success(request, 'Webhook set up: invoices paid by card will now be marked paid automatically.')
            return redirect(f"{reverse('org_settings', kwargs={'org_slug': self.org.slug})}#payments")

        if action == 'email':
            from mailer.forms import apply_post
            from mailer.models import EmailSettings
            email_settings = (EmailSettings.objects.filter(organisation=self.org).first()
                              or EmailSettings(organisation=self.org))
            errors = apply_post(email_settings, request.POST)
            for error in errors:
                messages.error(request, error)
            if not errors:
                email_settings.save()
                if email_settings.provider == 'dojo_mail':
                    email_settings.dkim_private_key(create=True)
                messages.success(request, f'Email settings saved: sending with {email_settings.provider_info.label}. '
                                          'Send a test email to check.')
            return redirect(f"{reverse('org_settings', kwargs={'org_slug': self.org.slug})}#email")

        if action == 'security':
            try:
                minutes = int(request.POST.get('session_timeout_minutes', ''))
            except ValueError:
                minutes = None
            two_factor = request.POST.get('two_factor', self.org.two_factor_policy)
            if minutes not in {m for m, _ in Organisation.SESSION_TIMEOUT_CHOICES}:
                messages.error(request, 'Choose one of the listed times.')
            elif two_factor not in dict(Organisation.TWO_FACTOR_CHOICES):
                messages.error(request, 'Choose who must use two-step sign-in.')
            else:
                self.org.settings = {**(self.org.settings or {}), 'session_timeout_minutes': minutes,
                                     'two_factor': two_factor}
                self.org.save(update_fields=['settings'])
                messages.success(request, 'Security settings saved.')
            return redirect(f"{reverse('org_settings', kwargs={'org_slug': self.org.slug})}#security")

        if action == 'fields':
            from .optional_fields import choices_from_keys as field_choices
            from .optional_fields import labels_from_post
            self.org.set_optional_fields(field_choices(request.POST.getlist('optional_fields')))
            self.org.settings = {**(self.org.settings or {}), **labels_from_post(request.POST)}
            self.org.save(update_fields=['optional_fields', 'settings'])
            messages.success(request, 'Fields saved.')
            return redirect(f"{reverse('org_settings', kwargs={'org_slug': self.org.slug})}#fields")

        if action == 'theme':
            sidebar_color = request.POST.get('sidebar_color', '#1E3A5F').strip()
            accent_color = request.POST.get('accent_color', '#2563EB').strip()
            # Derive a darker shade for sidebar gradient (~15% darker)
            def darken(hex_color):
                hex_color = hex_color.lstrip('#')
                try:
                    r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
                    r, g, b = max(0, int(r * 0.8)), max(0, int(g * 0.8)), max(0, int(b * 0.8))
                    return f'#{r:02x}{g:02x}{b:02x}'
                except Exception:
                    return hex_color
            settings = self.org.settings or {}
            settings['sidebar_color'] = sidebar_color
            settings['sidebar_color_dark'] = darken(sidebar_color)
            settings['accent_color'] = accent_color
            settings['accent_hover'] = darken(accent_color)
            self.org.settings = settings
            self.org.save(update_fields=['settings'])

            if 'logo' in request.FILES:
                from .branding import image_luminance, validate_logo
                upload = request.FILES['logo']
                error = validate_logo(upload)
                if error:
                    messages.error(request, error)
                    return redirect(f"{reverse('org_settings', kwargs={'org_slug': self.org.slug})}#appearance")
                if self.org.logo:
                    self.org.logo.delete(save=False)
                self.org.logo = upload
                self.org.settings = {**(self.org.settings or {}), 'logo_luminance': round(image_luminance(upload), 3)}
                self.org.save(update_fields=['logo', 'settings'])
            if request.POST.get('remove_logo'):
                self.org.logo.delete(save=False)
                self.org.settings = {k: v for k, v in (self.org.settings or {}).items() if k != 'logo_luminance'}
                self.org.save(update_fields=['logo', 'settings'])

            dev_mode = request.POST.get('dev_mode') == '1'
            if dev_mode:
                self.org.custom_css = request.POST.get('custom_css', '').strip()
                self.org.save(update_fields=['custom_css'])

            messages.success(request, 'Theme saved.')
            return redirect(f"{reverse('org_settings', kwargs={'org_slug': self.org.slug})}#appearance")

        # General settings
        name = request.POST.get('name', '').strip()
        email = request.POST.get('email', '').strip()
        phone = request.POST.get('phone', '').strip()
        website = request.POST.get('website', '').strip()

        if not name:
            messages.error(request, 'Organisation name is required.')
            return render(request, self.template_name, self._context())

        self.org.name = name
        self.org.email = email
        self.org.phone = phone
        self.org.website = website
        short_name = request.POST.get('short_name', '').strip()[:40]
        self.org.settings = {k: v for k, v in (self.org.settings or {}).items() if k != 'short_name'}
        if short_name:
            self.org.settings['short_name'] = short_name
        self.org.save(update_fields=['name', 'email', 'phone', 'website', 'settings'])
        messages.success(request, 'Settings saved.')
        return redirect(f"{reverse('org_settings', kwargs={'org_slug': self.org.slug})}#general")


class CustomFieldSettingsView(OrgAdminMixin, View):
    def get(self, request, org_slug):
        from django.shortcuts import render
        from members.models import CustomField
        fields = CustomField.objects.filter(organisation=self.org)
        return render(request, 'org/custom_fields.html', {
            'org': self.org,
            'org_membership': self.org_membership,
            'fields': fields,
            'field_types': CustomField.FieldType.choices,
        })

    def post(self, request, org_slug):
        from django.shortcuts import render
        from members.models import CustomField
        action = request.POST.get('action')

        if action == 'add':
            name = request.POST.get('name', '').strip()
            field_type = request.POST.get('field_type', 'text')
            options_raw = request.POST.get('options', '').strip()
            if name:
                options = [o.strip() for o in options_raw.splitlines() if o.strip()] if options_raw else []
                order = CustomField.objects.filter(organisation=self.org).count()
                CustomField.objects.create(
                    organisation=self.org,
                    name=name,
                    field_type=field_type,
                    options=options,
                    order=order,
                )
                messages.success(request, f'Field "{name}" added.')

        elif action == 'delete':
            field_pk = request.POST.get('field_pk')
            field = get_object_or_404(CustomField, pk=field_pk, organisation=self.org)
            field.delete()
            messages.success(request, f'Field "{field.name}" deleted.')

        return redirect('org_custom_fields', org_slug=self.org.slug)


class AnnouncementListView(OrgMixin, View):
    """
    All staff can send announcements. Coaches can only send to classes they
    coach unless granted `message_all_members`, which also unlocks sending
    to every active member.
    """
    required_feature = 'announcements'

    def _sendable_classes(self):
        from classes.models import Class
        classes = Class.objects.filter(organisation=self.org)
        if not self.can('message_all_members'):
            classes = classes.filter(pk__in=self.coached_class_ids())
        return classes.order_by('name')

    def get(self, request, org_slug):
        announcements = Announcement.objects.filter(organisation=self.org)
        classes = self._sendable_classes()
        return render(request, 'org/announcements.html', {
            'org': self.org,
            'org_membership': self.org_membership,
            'announcements': announcements,
            'classes': classes,
        })

    def post(self, request, org_slug):
        from classes.models import ClassMember
        from members.models import Member

        subject = request.POST.get('subject', '').strip()
        body = request.POST.get('body', '').strip()
        recipient_type = request.POST.get('recipient_type', 'all')
        class_pk = request.POST.get('class_pk', '').strip()

        if not subject or not body:
            messages.error(request, 'Subject and body are required.')
            return redirect('org_announcements', org_slug=self.org.slug)

        if not self.can('message_all_members'):
            recipient_type = 'class'

        if recipient_type == 'class':
            if not class_pk:
                messages.error(request, 'Select a class to send to.')
                return redirect('org_announcements', org_slug=self.org.slug)
            cls = get_object_or_404(self._sendable_classes(), pk=class_pk)
            member_ids = ClassMember.objects.filter(assigned_class=cls).values_list('member_id', flat=True)
            members = Member.objects.filter(pk__in=member_ids, is_active=True)
            label = f'Class: {cls.name}'
        else:
            members = Member.objects.filter(organisation=self.org, is_active=True)
            label = 'All active members'

        sent = 0
        for member in members:
            recipient = member.email
            if not recipient:
                guardian = member.guardians.filter(email__gt='').first()
                if guardian:
                    recipient = guardian.email
            if not recipient:
                continue

            try:
                compose.send(self.org, 'announcement', [recipient],
                             {**compose.member_values(member), 'title': subject},
                             {'message': email_blocks.message(body)})
                sent += 1
            except Exception:  # one bad address mustn't stop the rest of the announcement
                pass

        Announcement.objects.create(
            organisation=self.org,
            subject=subject,
            body=body,
            sent_by=request.user,
            recipient_count=sent,
            recipient_label=label,
        )
        messages.success(request, f'Announcement sent to {sent} member{"s" if sent != 1 else ""}.')
        return redirect('org_announcements', org_slug=self.org.slug)


class CalendarView(OrgMixin, View):
    """Any org member (coach or admin) can view the calendar."""
    def get(self, request, org_slug):
        return render(request, 'org/calendar.html', {
            'org': self.org,
            'org_membership': self.org_membership,
        })


class CalendarEventsView(OrgMixin, View):
    def get(self, request, org_slug):
        from classes.models import Session
        start = request.GET.get('start', '')
        end = request.GET.get('end', '')
        qs = Session.objects.filter(
            assigned_class__organisation=self.org
        ).select_related('assigned_class')
        if start:
            qs = qs.filter(date__gte=start[:10])
        if end:
            qs = qs.filter(date__lte=end[:10])

        is_admin = request.user.is_superuser or (
            self.org_membership and self.org_membership.role == 'org_admin'
        )
        if not is_admin:
            qs = qs.filter(assigned_class__coaches__user=request.user)

        from django.urls import reverse
        events = []
        for session in qs:
            color = '#dc3545' if session.is_cancelled else '#212529'
            title = session.assigned_class.name
            if session.is_cancelled:
                title += ' (cancelled)'
            events.append({
                'id': session.pk,
                'title': title,
                'start': session.date.isoformat(),
                'url': reverse('session_register', kwargs={
                    'org_slug': org_slug,
                    'pk': session.assigned_class.pk,
                    'session_pk': session.pk,
                }),
                'color': color,
            })

        from datetime import timedelta as _timedelta

        holiday_qs = StaffHoliday.objects.filter(
            member__organisation=self.org
        ).select_related('member__user')
        if start:
            holiday_qs = holiday_qs.filter(end_date__gte=start[:10])
        if end:
            holiday_qs = holiday_qs.filter(start_date__lte=end[:10])

        for holiday in holiday_qs:
            name = holiday.member.user.get_full_name() or holiday.member.user.username
            events.append({
                'id': f'holiday-{holiday.pk}',
                'title': f'{name} — holiday',
                'start': holiday.start_date.isoformat(),
                # FullCalendar all-day events treat `end` as exclusive, so add a day.
                'end': (holiday.end_date + _timedelta(days=1)).isoformat(),
                'color': '#6c757d',
                'display': 'block',
            })

        return JsonResponse(events, safe=False)


class FinancialReportView(CapabilityMixin, View):
    required_feature = 'billing'
    required_capabilities = ('manage_billing',)

    def get(self, request, org_slug):
        from billing.models import Invoice, Payment
        from django.db.models.functions import TruncMonth
        import json

        today = date.today()

        twelve_months_ago = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
        twelve_months_ago = twelve_months_ago.replace(
            year=twelve_months_ago.year - 1 if twelve_months_ago.month == 12 else twelve_months_ago.year,
            month=1 if twelve_months_ago.month == 12 else twelve_months_ago.month + 1
        )
        # Simpler: go back 11 months from current month start
        start_month = today.replace(day=1)
        months = []
        for i in range(11, -1, -1):
            y = start_month.year
            m = start_month.month - i
            while m <= 0:
                m += 12
                y -= 1
            months.append(date(y, m, 1))

        monthly_data = (
            Payment.objects.filter(invoice__organisation=self.org)
            .annotate(month=TruncMonth('paid_at'))
            .values('month')
            .annotate(total=Sum('amount'))
            .order_by('month')
        )
        revenue_map = {r['month'].date().replace(day=1): float(r['total']) for r in monthly_data}

        chart_labels = [m.strftime('%b %Y') for m in months]
        chart_data = [revenue_map.get(m, 0) for m in months]

        outstanding_members = (
            Invoice.objects.filter(organisation=self.org, status=Invoice.Status.UNPAID)
            .select_related('member')
            .values('member__name', 'member__pk')
            .annotate(total=Sum('amount'))
            .order_by('-total')
        )

        total_revenue_ytd = Payment.objects.filter(
            invoice__organisation=self.org,
            paid_at__year=today.year,
        ).aggregate(t=Sum('amount'))['t'] or 0

        total_outstanding = Invoice.objects.filter(
            organisation=self.org, status=Invoice.Status.UNPAID
        ).aggregate(t=Sum('amount'))['t'] or 0

        return render(request, 'org/finance.html', {
            'org': self.org,
            'org_membership': self.org_membership,
            'chart_labels': json.dumps(chart_labels),
            'chart_data': json.dumps(chart_data),
            'outstanding_members': outstanding_members,
            'total_revenue_ytd': total_revenue_ytd,
            'total_outstanding': total_outstanding,
            'today': today,
        })


class AccountView(OrgMixin, View):
    """Self-service account management — available to any logged-in member (coach or admin), not just org admins."""
    template_name = 'org/account.html'

    def get(self, request, org_slug):
        holidays = (
            self.org_membership.holidays.all() if self.org_membership else StaffHoliday.objects.none()
        )
        return render(request, self.template_name, {
            'org': self.org,
            'org_membership': self.org_membership,
            'memberships': request.user.organisation_memberships.select_related('organisation').order_by('organisation__name'),
            'holidays': holidays,
            'today': date.today(),
            'two_factor': device_for(request.user),
            'two_factor_required': two_factor_required(request.user),
        })

    def post(self, request, org_slug):
        action = request.POST.get('action', 'update_account')

        if action == 'add_holiday':
            if not self.org_membership:
                messages.error(request, "You're not a staff member of this organisation.")
                return redirect('account_settings', org_slug=self.org.slug)

            start_raw = request.POST.get('start_date', '').strip()
            end_raw = request.POST.get('end_date', '').strip()
            note = request.POST.get('note', '').strip()

            if not start_raw or not end_raw:
                messages.error(request, 'Start and end date are required.')
                return redirect('account_settings', org_slug=self.org.slug)

            try:
                start_date = date.fromisoformat(start_raw)
                end_date = date.fromisoformat(end_raw)
            except ValueError:
                messages.error(request, 'Invalid date.')
                return redirect('account_settings', org_slug=self.org.slug)

            if end_date < start_date:
                messages.error(request, 'End date must be on or after the start date.')
                return redirect('account_settings', org_slug=self.org.slug)

            StaffHoliday.objects.create(
                member=self.org_membership, start_date=start_date, end_date=end_date, note=note,
            )
            messages.success(request, f'Holiday added ({start_date:%d %b %Y} – {end_date:%d %b %Y}).')
            return redirect('account_settings', org_slug=self.org.slug)

        elif action == 'remove_holiday':
            if not self.org_membership:
                messages.error(request, "You're not a staff member of this organisation.")
                return redirect('account_settings', org_slug=self.org.slug)

            holiday_pk = request.POST.get('holiday_pk')
            # Scoped to the logged-in user's own membership — a coach can only remove their own holidays.
            holiday = get_object_or_404(StaffHoliday, pk=holiday_pk, member=self.org_membership)
            holiday.delete()
            messages.success(request, 'Holiday removed.')
            return redirect('account_settings', org_slug=self.org.slug)

        elif action == 'update_emergency_contacts':
            if not self.org_membership:
                messages.error(request, "You're not a staff member of this organisation.")
                return redirect('account_settings', org_slug=self.org.slug)
            # Scoped to the logged-in user's own membership, and only the
            # emergency contact fields the club has switched on.
            from .optional_fields import STAFF, enabled_model_fields
            fields = enabled_model_fields(
                self.org, STAFF, keys={'staff_emergency_contact', 'staff_emergency_contact_2'},
            )
            for name in fields:
                setattr(self.org_membership, name, request.POST.get(name, '').strip())
            if fields:
                self.org_membership.save(update_fields=fields)
            messages.success(request, 'Emergency contacts updated.')
            return redirect('account_settings', org_slug=self.org.slug)

        user = request.user
        first_name = request.POST.get('first_name', '').strip()
        last_name = request.POST.get('last_name', '').strip()
        email = request.POST.get('email', '').strip()

        if not email:
            messages.error(request, 'Email is required.')
            return redirect('account_settings', org_slug=self.org.slug)

        user.first_name = first_name
        user.last_name = last_name
        user.email = email
        user.save(update_fields=['first_name', 'last_name', 'email'])
        messages.success(request, 'Account details updated.')
        return redirect('account_settings', org_slug=self.org.slug)


class KeepAliveView(OrgMixin, View):
    """Pinged by the page while someone is active, so the inactivity timer restarts."""

    def post(self, request, org_slug):
        return HttpResponse(status=204)


class AccountPasswordChangeView(OrgMixin, PasswordChangeView):
    template_name = 'org/account_password.html'

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        for field in form.fields.values():
            field.widget.attrs['class'] = 'form-control'
        return form

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['org'] = self.org
        context['org_membership'] = self.org_membership
        return context

    def get_success_url(self):
        messages.success(self.request, 'Password changed.')
        return reverse('account_settings', kwargs={'org_slug': self.org.slug})
