from django.urls import path
from . import stripe_views

urlpatterns = [
    path('webhook/', stripe_views.StripeWebhookView.as_view(), name='stripe_webhook'),
]
