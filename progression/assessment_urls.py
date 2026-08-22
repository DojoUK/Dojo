from django.urls import path
from . import assessment_views as views

urlpatterns = [
    path('', views.AssessmentListView.as_view(), name='assessment_list'),
    path('<int:pk>/', views.AssessmentDetailView.as_view(), name='assessment_detail'),
    path('<int:pk>/sessions/add/', views.AddAssessmentSessionView.as_view(), name='assessment_session_add'),
    path('<int:pk>/sessions/<int:session_pk>/', views.AssessmentSessionDetailView.as_view(), name='assessment_session_detail'),
    path('<int:pk>/sessions/<int:session_pk>/enrol/', views.EnrolAssessmentMemberView.as_view(), name='assessment_session_enrol'),
    path('<int:pk>/sessions/<int:session_pk>/unenrol/<int:member_pk>/', views.UnenrolAssessmentMemberView.as_view(), name='assessment_session_unenrol'),
    path('<int:pk>/sessions/<int:session_pk>/register/', views.AssessmentRegisterView.as_view(), name='assessment_register'),
    path('<int:pk>/sessions/<int:session_pk>/cancel/', views.CancelAssessmentSessionView.as_view(), name='assessment_session_cancel'),
]
