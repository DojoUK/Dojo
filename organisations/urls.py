from django.urls import include, path

from mailer import views as mailer_views

from dojo.csv_import import ImportView

from . import dashboard_views, views

urlpatterns = [
    path('', dashboard_views.DashboardView.as_view(), name='org_dashboard'),
    path('dashboard/layout/', dashboard_views.DashboardLayoutView.as_view(), name='org_dashboard_layout'),
    path('dashboard/widgets/<slug:key>/', dashboard_views.DashboardWidgetView.as_view(), name='org_dashboard_widget'),
    path('keep-alive/', views.KeepAliveView.as_view(), name='org_keep_alive'),
    path('members/', include('members.urls')),
    path('classes/', include('classes.urls')),
    path('gradings/', include('gradings.urls')),
    # DojoUK's own gradings (switched off unless the club turns on the Gradings
    # feature); the fork's gradings app above keeps the gradings/ addresses.
    path('grading-events/', include('progression.grading_urls')),
    path('syllabus/', include('progression.syllabus_urls')),
    path('billing/', include('billing.urls')),
    path('inventory/', include('inventory.urls')),
    path('audit/', views.AuditLogView.as_view(), name='org_audit_log'),
    path('import/', ImportView.as_view(), name='org_import'),
    path('import/<slug:kind>/', ImportView.as_view(), name='org_import_kind'),
    path('staff/', views.StaffListView.as_view(), name='org_staff'),
    path('settings/', views.OrgSettingsView.as_view(), name='org_settings'),
    path('reseed-demo/', views.ReseedDemoView.as_view(), name='org_reseed_demo'),
    path('onboarding/dismiss/', views.DismissOnboardingView.as_view(), name='org_onboarding_dismiss'),
    path('settings/test-email/', views.TestEmailView.as_view(), name='org_test_email'),
    path('settings/emails/', mailer_views.EmailTemplateListView.as_view(), name='org_email_templates'),
    path('settings/emails/<slug:key>/', mailer_views.EmailTemplateEditView.as_view(), name='org_email_template_edit'),
    path('settings/emails/<slug:key>/preview/', mailer_views.EmailTemplatePreviewView.as_view(),
         name='org_email_template_preview'),
    path('settings/fields/', views.CustomFieldSettingsView.as_view(), name='org_custom_fields'),
    path('settings/progression/', include('progression.urls')),
    path('announcements/', views.AnnouncementListView.as_view(), name='org_announcements'),
    path('calendar/', views.CalendarView.as_view(), name='org_calendar'),
    path('calendar/events/', views.CalendarEventsView.as_view(), name='org_calendar_events'),
    path('finance/', views.FinancialReportView.as_view(), name='org_finance'),
    path('account/', views.AccountView.as_view(), name='account_settings'),
    path('account/password/', views.AccountPasswordChangeView.as_view(), name='account_password_change'),
]
