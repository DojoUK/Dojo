from django.urls import path
from . import portal_views

urlpatterns = [
    path('', portal_views.PortalView.as_view(), name='member_portal'),
    path('details/', portal_views.PortalMyDetailsView.as_view(), name='portal_my_details'),
    path('syllabus/', portal_views.PortalSyllabusView.as_view(), name='portal_syllabus'),
    path('classes/', portal_views.PortalClassesView.as_view(), name='portal_classes'),
    path('code-of-conduct/', portal_views.PortalCodeOfConductView.as_view(), name='portal_code_of_conduct'),
    path('payments/', portal_views.PortalPaymentHistoryView.as_view(), name='portal_payments'),
    path('documents/upload/', portal_views.PortalDocumentUploadView.as_view(), name='portal_document_upload'),
    path('documents/<int:pk>/download/', portal_views.PortalDocumentDownloadView.as_view(), name='portal_document_download'),
    path('my-data/', portal_views.PortalDataSummaryView.as_view(), name='portal_my_data'),
    path('my-data/export.json', portal_views.DownloadDataView.as_view(), name='portal_download_data'),
    path('pay/<int:invoice_pk>/', portal_views.CreateCheckoutView.as_view(), name='portal_checkout'),
    path('subscribe/', portal_views.CreateSubscriptionView.as_view(), name='portal_subscribe'),
    path('cancel-subscription/', portal_views.CancelSubscriptionView.as_view(), name='portal_cancel_subscription'),
    path('stop-training/', portal_views.RequestStopTrainingView.as_view(), name='portal_stop_training'),
    path('billing-portal/', portal_views.BillingPortalView.as_view(), name='portal_billing_portal'),
]
