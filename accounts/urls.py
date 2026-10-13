from django.urls import path

from . import views

urlpatterns = [
    path('verify/', views.VerifyView.as_view(), name='two_factor_verify'),
    path('setup/', views.SetupView.as_view(), name='two_factor_setup'),
    path('recovery-codes/', views.RecoveryCodesView.as_view(), name='two_factor_recovery_codes'),
    path('turn-off/', views.DisableView.as_view(), name='two_factor_disable'),
]
