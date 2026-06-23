from django.urls import path
from . import signup_views

urlpatterns = [
    path('', signup_views.SignupView.as_view(), name='member_signup'),
]
