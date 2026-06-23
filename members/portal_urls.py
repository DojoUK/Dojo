from django.urls import path
from . import portal_views

urlpatterns = [
    path('', portal_views.PortalView.as_view(), name='member_portal'),
    path('pay/<int:invoice_pk>/', portal_views.CreateCheckoutView.as_view(), name='portal_checkout'),
    path('subscribe/', portal_views.CreateSubscriptionView.as_view(), name='portal_subscribe'),
    path('cancel-subscription/', portal_views.CancelSubscriptionView.as_view(), name='portal_cancel_subscription'),
    path('billing-portal/', portal_views.BillingPortalView.as_view(), name='portal_billing_portal'),
]
