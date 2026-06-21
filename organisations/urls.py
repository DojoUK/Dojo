from django.urls import include, path
from . import views

urlpatterns = [
    path('', views.DashboardView.as_view(), name='org_dashboard'),
    path('members/', include('members.urls')),
    path('classes/', include('classes.urls')),
    path('billing/', include('billing.urls')),
    path('audit/', views.AuditLogView.as_view(), name='org_audit_log'),
    path('staff/', views.StaffListView.as_view(), name='org_staff'),
    path('settings/fields/', views.CustomFieldSettingsView.as_view(), name='org_custom_fields'),
]
