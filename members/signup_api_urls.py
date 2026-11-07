from django.urls import path

from . import signup_api

urlpatterns = [
    path('', signup_api.SignupApiView.as_view(), name='member_signup_api'),
]
