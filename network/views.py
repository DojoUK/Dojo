"""The Dojo network's pages: member accounts (/account/) and the club finder (/clubs/)."""
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth import views as auth_views
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View

from organisations import disciplines
from organisations.location import maps_key, maps_map_id
from organisations.models import Organisation

from dojo.mixins import OrgMixin
from members.names import PRONOUN_SUGGESTIONS

from . import accounts, finder, reviews
from .forms import SignInForm, SignUpForm
from .models import FavouriteClub


def _next_url(request, default):
    url = request.POST.get('next') or request.GET.get('next') or ''
    if url and url_has_allowed_host_and_scheme(url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return url
    return default


class AccountRequiredMixin:
    """Signed in with a Dojo account (club staff sign-ins get a member account made the first time)."""

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"{reverse('network_login')}?next={request.get_full_path()}")
        self.account = accounts.account_for(request.user)
        return super().dispatch(request, *args, **kwargs)


def _relations(account):
    """For an account: {club id: 'member' | 'applied'}, and the set of favourite club ids."""
    if account is None:
        return {}, set()
    status = {}
    for app in account.applications.filter(status='pending').values('organisation_id'):
        status[app['organisation_id']] = 'applied'
    for member in account.memberships.filter(is_active=True).values('organisation_id'):
        status[member['organisation_id']] = 'member'
    favourites = set(account.favourites.values_list('organisation_id', flat=True))
    return status, favourites


def _takes_applications(org):
    return 'applications' in org.enabled_features


# ── Accounts ─────────────────────────────────────────────────────────────

class SignUpView(View):
    template_name = 'network/signup.html'

    def get(self, request):
        if request.user.is_authenticated:
            return redirect('network_home')
        return render(request, self.template_name, {'form': SignUpForm(), 'next': _next_url(request, ''), 'active': 'signup',
                                                    'pronoun_suggestions': PRONOUN_SUGGESTIONS})

    def post(self, request):
        form = SignUpForm(request.POST)
        if not form.is_valid():
            return render(request, self.template_name, {'form': form, 'next': _next_url(request, ''), 'active': 'signup',
                                                        'pronoun_suggestions': PRONOUN_SUGGESTIONS})
        data = form.cleaned_data
        account = accounts.create_account('', data['email'], data['password'],
                                          first_name=data['first_name'], surname=data['surname'])
        # Everything else on the form goes into My details.
        for name in SignUpForm.DETAIL_FIELDS:
            value = data.get(name)
            setattr(account, name, value.strip() if isinstance(value, str) else value)
        account.save()
        login(request, account.user, backend='django.contrib.auth.backends.ModelBackend')
        try:
            accounts.send_verification(account)
        except Exception:
            messages.warning(request, 'Your account is ready, but we couldn\'t send the email to confirm your address. '
                                      'Try again from your account page.')
        else:
            messages.success(request, f'Welcome to Dojo. We\'ve emailed {account.email} a link to confirm it\'s yours.')
        # New accounts are asked for any grades they already hold, unless they were on their way somewhere.
        return redirect(_next_url(request, reverse('network_progression') + '?welcome=1'))


class SignInView(auth_views.LoginView):
    template_name = 'network/login.html'
    authentication_form = SignInForm
    redirect_authenticated_user = True
    extra_context = {'active': 'login'}

    def get_default_redirect_url(self):
        return reverse('network_home')


class SignOutView(auth_views.LogoutView):
    next_page = reverse_lazy('network_finder')


class VerifyEmailView(View):
    def get(self, request, token):
        account = accounts.account_from_token(token)
        if account is None:
            return render(request, 'network/verify_failed.html', status=400)
        linked = accounts.mark_verified(account)
        if request.user.is_authenticated and request.user.pk == account.user_id:
            clubs = sorted({m.organisation.name for m in linked})
            messages.success(request, 'Your email is confirmed.' + (
                f' We\'ve linked your membership{"s" if len(linked) != 1 else ""} at {", ".join(clubs)}.' if clubs else ''))
            return redirect('network_home')
        return render(request, 'network/verify_done.html', {'account': account})


class ResendVerificationView(AccountRequiredMixin, View):
    def post(self, request):
        if self.account.is_verified:
            return redirect('network_home')
        try:
            accounts.send_verification(self.account)
        except Exception:
            messages.error(request, 'We couldn\'t send the email just now. Please try again later.')
        else:
            messages.success(request, f'We\'ve sent another link to {self.account.email}.')
        return redirect('network_home')


class PasswordResetView(auth_views.PasswordResetView):
    template_name = 'network/password_reset.html'
    email_template_name = 'network/emails/password_reset.txt'
    html_email_template_name = 'network/emails/password_reset.html'
    subject_template_name = 'network/emails/password_reset_subject.txt'
    success_url = reverse_lazy('network_password_reset_done')


class PasswordResetDoneView(auth_views.PasswordResetDoneView):
    template_name = 'network/password_reset_done.html'


class PasswordResetConfirmView(auth_views.PasswordResetConfirmView):
    template_name = 'network/password_reset_confirm.html'
    success_url = reverse_lazy('network_password_reset_complete')


class PasswordResetCompleteView(auth_views.PasswordResetCompleteView):
    template_name = 'network/password_reset_complete.html'


class HomeView(AccountRequiredMixin, View):
    """My clubs: memberships (with what's coming up and a way into each club's portal), favourites and applications."""

    def get(self, request):
        from members.models import MemberLeavingRequest, current_plans
        from members.portal_views import _up_next
        account = self.account
        accounts.link_memberships(account)   # picks up members a club has added since, once verified
        by_club = {}
        up_next = []
        for member in account.memberships.filter(is_active=True).select_related('organisation').order_by('organisation__name', 'name'):
            org = member.organisation
            entry = by_club.setdefault(org.pk, {'org': org, 'members': [],
                                                'portal': 'member_portal' in org.enabled_features})
            entry['members'].append(member)
            member.plans = current_plans(member)
            member.leaving = member.leaving_requests.filter(status=MemberLeavingRequest.Status.PENDING).first()
            if entry['portal']:
                for row in _up_next(member, limit=3):
                    up_next.append({**row, 'member': member, 'org': org})
        up_next.sort(key=lambda row: row['start'])
        status, favourite_ids = _relations(account)
        favourites = [{'org': f.organisation, 'status': status.get(f.organisation_id),
                       'takes_applications': _takes_applications(f.organisation)}
                      for f in account.favourites.select_related('organisation')]
        return render(request, 'network/home.html', {
            'active': 'home',
            'account': account,
            'clubs': list(by_club.values()),
            'up_next': up_next[:6],
            'favourites': favourites,
            'applications': account.applications.filter(status='pending').select_related('organisation'),
            'several_people': len({m.pk for c in by_club.values() for m in c['members']}) > len(by_club),
        })


class LeaveClubView(AccountRequiredMixin, View):
    """From My clubs: let a club know one of the account's members plans to stop training there.
    Like the club portal's own button, staff review it before anything changes."""

    def post(self, request, pk):
        from members.models import request_to_leave
        member = get_object_or_404(self.account.memberships.filter(is_active=True).select_related('organisation'), pk=pk)
        request_to_leave(member, request.POST.get('reason', ''))
        who = 'you\'re' if member.is_account_holder else f'{member.name} is'
        messages.success(request, f'We\'ve let {member.organisation.name} know {who} planning to stop training. '
                                  f'They\'ll be in touch, and nothing changes until then.')
        return redirect('network_home')


class ProgressionView(AccountRequiredMixin, View):
    """
    The Dojo Passport: every grade the member holds, from the clubs on Dojo (read only: the awarding
    club keeps it) and the ones they've added themselves (theirs to change), by martial art.
    """

    def get(self, request):
        from . import progression
        return render(request, 'network/progression.html', {
            'active': 'progression', 'account': self.account, 'people': progression.people(self.account),
            'discipline_choices': disciplines.DISCIPLINES, 'welcome': request.GET.get('welcome') == '1',
            'editing': request.GET.get('edit', ''),
        })

    def post(self, request):
        from datetime import date as date_type
        from .models import PriorProgression
        action = request.POST.get('action', 'save')
        record = None
        if request.POST.get('pk'):
            record = get_object_or_404(PriorProgression, pk=request.POST['pk'], account=self.account)
        if action == 'delete' and record:
            record.delete()
            messages.success(request, 'Grade removed.')
            return redirect('network_progression')
        discipline = request.POST.get('discipline', '')
        grade = request.POST.get('grade', '').strip()[:255]
        raw_date = request.POST.get('achieved_date', '').strip()
        errors = []
        if discipline not in disciplines.LABELS:
            errors.append('Choose the martial art.')
        if not grade:
            errors.append('Enter the grade, e.g. Blue belt.')
        achieved = None
        if raw_date:
            try:
                achieved = date_type.fromisoformat(raw_date)
            except ValueError:
                errors.append('Enter the date as a date.')
            else:
                if achieved > date_type.today():
                    errors.append('The date can\'t be in the future.')
        if errors:
            for error in errors:
                messages.error(request, error)
            return redirect('network_progression')
        record = record or PriorProgression(account=self.account)
        record.discipline, record.grade, record.achieved_date = discipline, grade, achieved
        record.awarded_by = request.POST.get('awarded_by', '').strip()[:255]
        record.save()
        messages.success(request, f'{grade} saved.' + ('' if request.POST.get('welcome') != '1' else ' Add another, or carry on to your clubs.'))
        return redirect(reverse('network_progression') + ('?welcome=1' if request.POST.get('welcome') == '1' else ''))


class DetailsView(AccountRequiredMixin, View):
    """
    My details: kept once in the account, used to fill in each club's join form, and kept up to date
    on the member's own records at the clubs they've joined, each club getting only what it records.
    """

    def _render(self, request, groups, errors=(), name=None):
        from . import details
        own = list(details.own_memberships(self.account))
        return render(request, 'network/details.html', {
            'active': 'details', 'account': self.account, 'groups': groups, 'errors': errors,
            'name': {'first_name': self.account.first_name, 'surname': self.account.surname} if name is None else name,
            'pronoun_suggestions': PRONOUN_SUGGESTIONS,
            'own_clubs': sorted({m.organisation.name for m in own}),
        })

    def get(self, request):
        from . import details
        return self._render(request, details.form_groups(self.account))

    def post(self, request):
        from . import details
        values, errors = details.clean(request.POST)
        first_name = request.POST.get('first_name', '').strip()[:150]
        surname = request.POST.get('surname', '').strip()[:150]
        if not first_name:
            errors.insert(0, 'Enter your first name.')
        if errors:
            return self._render(request, details.form_groups(self.account, request.POST), errors,
                                {'first_name': first_name, 'surname': surname})
        values.update(first_name=first_name, surname=surname)
        changed = [f for f, v in values.items() if getattr(self.account, f) != v]
        if 'first_name' in changed or 'surname' in changed:
            changed.append('name')
        for field, value in values.items():
            setattr(self.account, field, value)
        self.account.save()
        updated = details.sync(self.account, changed)
        message = 'My details saved.'
        if updated:
            message += f' Updated at {", ".join(org.name for org in updated)}.'
        messages.success(request, message)
        return redirect('network_details')


# ── Club finder ──────────────────────────────────────────────────────────

class FinderView(View):
    def get(self, request):
        return render(request, 'network/finder.html', {
            'active': 'finder',
            'discipline_choices': finder.offered_disciplines(),   # only martial arts a listed club teaches
            'radius_choices': finder.RADIUS_CHOICES,
            'sort_choices': finder.SORT_CHOICES, 'default_sort': finder.DEFAULT_SORT,
            'default_radius': finder.DEFAULT_RADIUS,
            'maps_key': maps_key(), 'maps_map_id': maps_map_id(),
            'account': accounts.account_for(request.user) if request.user.is_authenticated else None,
        })


class SearchView(View):
    """JSON for the finder: listed clubs near a point, or all of them, in the chosen order (finder.search)."""

    def get(self, request):
        lat, lng = finder.parse_point(request.GET)
        radius = finder.parse_radius(request.GET)
        account = accounts.account_for(request.user) if request.user.is_authenticated else None
        status, favourite_ids = _relations(account)
        clubs = []
        sort = finder.parse_sort(request.GET)
        from billing.promos import offers_by_club
        found = finder.search(lat, lng, radius, finder.parse_discipline(request.GET), sort)
        offers = offers_by_club([club.pk for club, _ in found])
        for club, miles in found:
            clubs.append({
                'slug': club.slug, 'name': club.name,
                'disciplines': club.discipline_labels,
                'address': club.address_text,
                'lat': float(club.latitude), 'lng': float(club.longitude),
                'miles': round(miles, 1) if miles is not None else None,
                'url': reverse('network_club', kwargs={'slug': club.slug}),
                'logo': club.logo.url if club.logo else '',
                'favourite': club.pk in favourite_ids,
                'status': status.get(club.pk, ''),
                'rating': club.rating, 'reviews': club.review_count,
                'offer': offers[club.pk].offer_text() if club.pk in offers else '',
            })
        return JsonResponse({'clubs': clubs, 'radius': radius, 'searched': lat is not None, 'sort': sort})


def _club_or_404(slug, account):
    """A club's finder page: listed clubs, or any club the account is a member of or has saved."""
    org = get_object_or_404(Organisation, slug=slug)
    if finder.listed_clubs().filter(pk=org.pk).exists():
        return org
    status, favourite_ids = _relations(account)
    if org.pk in status or org.pk in favourite_ids:
        return org
    raise Http404


class ClubView(View):
    def get(self, request, slug):
        account = accounts.account_for(request.user) if request.user.is_authenticated else None
        org = _club_or_404(slug, account)
        status, favourite_ids = _relations(account)
        portal_members = []
        if status.get(org.pk) == 'member' and 'member_portal' in org.enabled_features:
            portal_members = list(account.memberships.filter(organisation=org, is_active=True))
        from billing import promos
        from members.signup import active_waivers
        club_reviews = list(org.reviews.select_related('account')[:50])
        return render(request, 'network/club.html', {
            'active': 'finder',
            'org': org, 'account': account, 'maps_key': maps_key(), 'maps_map_id': maps_map_id(),
            'status': status.get(org.pk, ''), 'favourite': org.pk in favourite_ids,
            'takes_applications': _takes_applications(org), 'portal_members': portal_members,
            'waivers': active_waivers(org),
            'intro_offers': promos.public_offers(org),
            'review_summary': reviews.summary(org), 'reviews': club_reviews,
            'my_review': org.reviews.filter(account=account).first() if account else None,
            'can_review': reviews.can_review(account, org),
        })


class ReviewView(AccountRequiredMixin, View):
    """Post, change or remove the account's review of a club (members of it only)."""

    def post(self, request, slug):
        org = _club_or_404(slug, self.account)
        back = reverse('network_club', kwargs={'slug': org.slug}) + '#reviews'
        if request.POST.get('action') == 'delete':
            org.reviews.filter(account=self.account).delete()
            messages.success(request, 'Your review has been removed.')
            return redirect(back)
        existed = org.reviews.filter(account=self.account).exists()
        review, errors = reviews.save(self.account, org, request.POST.get('rating'), request.POST.get('text'))
        for error in errors:
            messages.error(request, error)
        if review:
            messages.success(request, 'Your review has been updated.' if existed else f'Thanks for reviewing {org.name}.')
        return redirect(back)


class StaffReviewsView(OrgMixin, View):
    """The club's reviews from members, for its staff. Admins can reply publicly and choose whether
    members are asked for reviews."""

    def _is_admin(self):
        return self.request.user.is_superuser or (self.org_membership and self.org_membership.role == 'org_admin')

    def get(self, request, org_slug):
        return render(request, 'org/reviews.html', {
            'org': self.org, 'org_membership': self.org_membership,
            'review_summary': reviews.summary(self.org),
            'reviews': self.org.reviews.select_related('account', 'replied_by'),
            'can_reply': self._is_admin(), 'prompts_on': reviews.prompts_on(self.org),
            'prompt_after': reviews.PROMPT_AFTER_SESSIONS,
        })

    def post(self, request, org_slug):
        from django.utils import timezone
        back = redirect('org_reviews', org_slug=self.org.slug)
        if not self._is_admin():
            messages.error(request, 'Only club admins can do that.')
            return back
        action = request.POST.get('action')
        if action == 'prompts':
            self.org.settings = {**(self.org.settings or {}), 'review_prompts': request.POST.get('on') == '1'}
            self.org.save(update_fields=['settings'])
            messages.success(request, 'Members will be asked for reviews.' if reviews.prompts_on(self.org)
                             else 'Members won\'t be asked for reviews.')
            return back
        review = get_object_or_404(self.org.reviews, pk=request.POST.get('review'))
        text = request.POST.get('reply', '').strip()[:reviews.MAX_TEXT]
        if action == 'remove_reply' or not text:
            type(review).objects.filter(pk=review.pk).update(reply='', replied_at=None, replied_by=None)
            messages.success(request, 'Reply removed.')
        else:
            # update(), so replying doesn't count as the member editing their review
            type(review).objects.filter(pk=review.pk).update(reply=text, replied_at=timezone.now(), replied_by=request.user)
            messages.success(request, 'Reply posted. It shows under the review on your Dojo page.')
        return back


class FavouriteView(AccountRequiredMixin, View):
    """Save or unsave a club (POST). Answers JSON to the finder's fetch, or goes back to the page."""

    def post(self, request, slug):
        org = _club_or_404(slug, self.account)
        favourite = FavouriteClub.objects.filter(account=self.account, organisation=org).first()
        if favourite:
            favourite.delete()
            saved = False
        else:
            FavouriteClub.objects.create(account=self.account, organisation=org)
            saved = True
        if request.headers.get('Accept', '').startswith('application/json'):
            return JsonResponse({'favourite': saved})
        messages.success(request, f'{org.name} is {"in" if saved else "no longer in"} your favourites.')
        return redirect(_next_url(request, reverse('network_club', kwargs={'slug': org.slug})))


class JoinClubView(AccountRequiredMixin, View):
    """Apply to join a club: its own application form, filled in from the account and linked to it."""

    def get(self, request, slug):
        org = _club_or_404(slug, self.account)
        status, _ = _relations(self.account)
        if status.get(org.pk) == 'member':
            messages.info(request, f'You\'re already a member of {org.name}.')
            return redirect('network_club', slug=org.slug)
        if not _takes_applications(org):
            messages.info(request, f'{org.name} isn\'t taking applications online. Contact them to join.')
            return redirect('network_club', slug=org.slug)
        url = reverse('member_signup', kwargs={'org_slug': org.slug})
        code = request.GET.get('code', '')
        return redirect(f'{url}?code={code}' if code.isalnum() else url)
