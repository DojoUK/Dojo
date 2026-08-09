from django.urls import path
from . import views

urlpatterns = [
    path('', views.GradingListView.as_view(), name='grading_list'),
    path('<int:pk>/', views.GradingDetailView.as_view(), name='grading_detail'),
    path('<int:pk>/sessions/add/', views.AddGradingSessionView.as_view(), name='grading_session_add'),
    path('<int:pk>/sessions/<int:session_pk>/', views.GradingSessionDetailView.as_view(), name='grading_session_detail'),
    path('<int:pk>/sessions/<int:session_pk>/enrol/', views.EnrolMemberView.as_view(), name='grading_session_enrol'),
    path('<int:pk>/sessions/<int:session_pk>/unenrol/<int:member_pk>/', views.UnenrolMemberView.as_view(), name='grading_session_unenrol'),
    path('<int:pk>/sessions/<int:session_pk>/register/', views.GradingRegisterView.as_view(), name='grading_register'),
    path('<int:pk>/sessions/<int:session_pk>/cancel/', views.CancelGradingSessionView.as_view(), name='grading_session_cancel'),
]
