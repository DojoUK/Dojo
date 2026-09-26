from django.urls import path
from . import portal_views

urlpatterns = [
    path('', portal_views.PortalLoginRequestView.as_view(), name='portal_login_request'),
]
