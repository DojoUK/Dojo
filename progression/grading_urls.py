from django.urls import path

from . import grading_views as views

urlpatterns = [
    path('', views.GradingListView.as_view(), name='grading_list'),
    path('<int:pk>/', views.GradingDetailView.as_view(), name='grading_detail'),
    path('<int:pk>/edit/', views.GradingEditView.as_view(), name='grading_edit'),
    path('<int:pk>/delete/', views.GradingDeleteView.as_view(), name='grading_delete'),
    path('<int:pk>/sessions/add/', views.SessionAddView.as_view(), name='grading_session_add'),
    path('<int:pk>/sessions/<int:session_pk>/', views.SessionView.as_view(), name='grading_session'),
    path('<int:pk>/sessions/<int:session_pk>/edit/', views.SessionEditView.as_view(), name='grading_session_edit'),
    path('<int:pk>/sessions/<int:session_pk>/delete/', views.SessionDeleteView.as_view(), name='grading_session_delete'),
    path('<int:pk>/sessions/<int:session_pk>/candidates/add/', views.CandidatesAddView.as_view(), name='grading_candidates_add'),
    path('<int:pk>/sessions/<int:session_pk>/candidates/<int:candidate_pk>/remove/', views.CandidateRemoveView.as_view(), name='grading_candidate_remove'),
    path('<int:pk>/sessions/<int:session_pk>/results/', views.ResultsSaveView.as_view(), name='grading_results_save'),
]
