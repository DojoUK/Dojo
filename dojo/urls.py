from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect
from django.urls import include, path


@login_required
def root_redirect(request):
    from organisations.models import Organisation, OrganisationMember
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
    path('login/', auth_views.LoginView.as_view(), name='login'),
    path('logout/', auth_views.LogoutView.as_view(next_page='login'), name='logout'),
    path('password-reset/', auth_views.PasswordResetView.as_view(), name='password_reset'),
    path('password-reset/done/', auth_views.PasswordResetDoneView.as_view(), name='password_reset_done'),
    path('password-reset/<uidb64>/<token>/', auth_views.PasswordResetConfirmView.as_view(), name='password_reset_confirm'),
    path('password-reset/complete/', auth_views.PasswordResetCompleteView.as_view(), name='password_reset_complete'),
    path('', root_redirect, name='root'),
    path('org/<slug:org_slug>/', include('organisations.urls')),
    path('p/<str:token>/', include('members.portal_urls')),
    path('stripe/', include('billing.stripe_urls')),
    path('join/<slug:org_slug>/', include('members.signup_urls')),
    path('org/<slug:org_slug>/', include('documents.urls')),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
