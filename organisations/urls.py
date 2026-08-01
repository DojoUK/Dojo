from django.urls import include, path
from . import views

urlpatterns = [
    path('', views.DashboardView.as_view(), name='org_dashboard'),
    path('members/', include('members.urls')),
    path('classes/', include('classes.urls')),
    path('billing/', include('billing.urls')),
    path('inventory/', include('inventory.urls')),
    path('audit/', views.AuditLogView.as_view(), name='org_audit_log'),
    path('staff/', views.StaffListView.as_view(), name='org_staff'),
    path('settings/', views.OrgSettingsView.as_view(), name='org_settings'),
    path('reseed-demo/', views.ReseedDemoView.as_view(), name='org_reseed_demo'),
    path('onboarding/dismiss/', views.DismissOnboardingView.as_view(), name='org_onboarding_dismiss'),
    path('settings/test-email/', views.TestEmailView.as_view(), name='org_test_email'),
    path('settings/fields/', views.CustomFieldSettingsView.as_view(), name='org_custom_fields'),
    path('settings/progression/', include('progression.urls')),
    path('announcements/', views.AnnouncementListView.as_view(), name='org_announcements'),
    path('calendar/', views.CalendarView.as_view(), name='org_calendar'),
    path('calendar/events/', views.CalendarEventsView.as_view(), name='org_calendar_events'),
    path('finance/', views.FinancialReportView.as_view(), name='org_finance'),
    path('account/', views.AccountView.as_view(), name='account_settings'),
    path('account/password/', views.AccountPasswordChangeView.as_view(), name='account_password_change'),
]
