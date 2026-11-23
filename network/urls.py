from django.urls import path

from . import views

urlpatterns = [
    path('account/', views.HomeView.as_view(), name='network_home'),
    path('account/details/', views.DetailsView.as_view(), name='network_details'),
    path('account/progression/', views.ProgressionView.as_view(), name='network_progression'),
    path('account/memberships/<int:pk>/leave/', views.LeaveClubView.as_view(), name='network_leave'),
    path('account/signup/', views.SignUpView.as_view(), name='network_signup'),
    path('account/login/', views.SignInView.as_view(), name='network_login'),
    path('account/logout/', views.SignOutView.as_view(), name='network_logout'),
    path('account/verify/resend/', views.ResendVerificationView.as_view(), name='network_verify_resend'),
    path('account/verify/<str:token>/', views.VerifyEmailView.as_view(), name='network_verify'),
    path('account/password-reset/', views.PasswordResetView.as_view(), name='network_password_reset'),
    path('account/password-reset/sent/', views.PasswordResetDoneView.as_view(), name='network_password_reset_done'),
    path('account/password-reset/<uidb64>/<token>/', views.PasswordResetConfirmView.as_view(),
         name='network_password_reset_confirm'),
    path('account/password-reset/done/', views.PasswordResetCompleteView.as_view(), name='network_password_reset_complete'),
    path('clubs/', views.FinderView.as_view(), name='network_finder'),
    path('clubs/search.json', views.SearchView.as_view(), name='network_search'),
    path('clubs/<slug:slug>/', views.ClubView.as_view(), name='network_club'),
    path('clubs/<slug:slug>/favourite/', views.FavouriteView.as_view(), name='network_favourite'),
    path('clubs/<slug:slug>/join/', views.JoinClubView.as_view(), name='network_join'),
    path('clubs/<slug:slug>/review/', views.ReviewView.as_view(), name='network_review'),
]
