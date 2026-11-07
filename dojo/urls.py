from django.conf import settings
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.shortcuts import redirect
from django.urls import include, path, re_path
from django.views.static import serve

from mailer.password_reset import ClubPasswordResetForm
from organisations import views as organisations_views


def root_redirect(request):
    from organisations.models import Organisation, OrganisationMember
    if not Organisation.objects.exists():
        return redirect('setup')
    if not request.user.is_authenticated:
        return redirect('login')
    membership = OrganisationMember.objects.filter(user=request.user).select_related('organisation').first()
    if membership:
        return redirect('org_dashboard', org_slug=membership.organisation.slug)
    if request.user.is_superuser:
        org = Organisation.objects.first()
        if org:
            return redirect('org_dashboard', org_slug=org.slug)
        return redirect('admin:index')
    return redirect('login')


urlpatterns = [
    path('admin/', admin.site.urls),
    path('setup/', organisations_views.SetupView.as_view(), name='setup'),
    path('login/', auth_views.LoginView.as_view(), name='login'),
    path('2fa/', include('accounts.urls')),
    path('logout/', auth_views.LogoutView.as_view(next_page='login'), name='logout'),
    path('password-reset/', auth_views.PasswordResetView.as_view(form_class=ClubPasswordResetForm), name='password_reset'),
    path('password-reset/done/', auth_views.PasswordResetDoneView.as_view(), name='password_reset_done'),
    path('password-reset/<uidb64>/<token>/', auth_views.PasswordResetConfirmView.as_view(), name='password_reset_confirm'),
    path('password-reset/complete/', auth_views.PasswordResetCompleteView.as_view(), name='password_reset_complete'),
    path('', root_redirect, name='root'),
    path('org/<slug:org_slug>/', include('organisations.urls')),
    path('p/<str:token>/', include('members.portal_urls')),
    path('stripe/', include('billing.stripe_urls')),
    path('pay/', include('billing.pay_urls')),
    path('join/<slug:org_slug>/', include('members.signup_urls')),
    path('api/join/<slug:org_slug>/', include('members.signup_api_urls')),
    path('org/<slug:org_slug>/', include('documents.urls')),
]

# Uploaded files that are public by design are served straight from MEDIA_URL:
# club logos (emails, the sign-in page) and waiver forms (the join page).
# Members' documents and signed waivers are never served from here, only
# through the signed-in download views and the member's own portal.
PUBLIC_MEDIA_FOLDERS = ('logos', 'waiver_templates')


def public_media_patterns():
    # Each folder is its own document root, so "logos/../documents/..." can't climb out of it.
    prefix = settings.MEDIA_URL.lstrip('/')
    return [
        re_path(rf'^{prefix}{folder}/(?P<path>.+)$', serve, {'document_root': settings.MEDIA_ROOT / folder})
        for folder in PUBLIC_MEDIA_FOLDERS
    ]


if settings.DEBUG and settings.MEDIA_URL.startswith('/'):
    urlpatterns += public_media_patterns()
