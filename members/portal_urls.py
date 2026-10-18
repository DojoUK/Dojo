from django.urls import path
from . import portal_views

urlpatterns = [
    path('', portal_views.PortalView.as_view(), name='member_portal'),
    path('my-data/', portal_views.DownloadDataView.as_view(), name='portal_download_data'),
    path('pay/<int:invoice_pk>/', portal_views.CreateCheckoutView.as_view(), name='portal_checkout'),
    path('automatic-payments/', portal_views.AutoPaySetupView.as_view(), name='portal_autopay_setup'),
    path('automatic-payments/off/', portal_views.AutoPayOffView.as_view(), name='portal_autopay_off'),
]
