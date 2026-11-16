from django.urls import path
from . import views

urlpatterns = [
    path('', views.ClassListView.as_view(), name='class_list'),
    path('add/', views.ClassCreateView.as_view(), name='class_add'),
    path('generate-registers/', views.BulkGenerateSessionsView.as_view(), name='class_bulk_generate'),
    path('<int:pk>/', views.ClassDetailView.as_view(), name='class_detail'),
    path('<int:pk>/edit/', views.ClassUpdateView.as_view(), name='class_edit'),
    path('<int:pk>/enrol/', views.EnrolMemberView.as_view(), name='class_enrol'),
    path('<int:pk>/auto-enrol/<int:member_pk>/', views.SetAutoEnrolView.as_view(), name='class_set_auto_enrol'),
    path('<int:pk>/unenrol/<int:member_pk>/', views.UnenrolMemberView.as_view(), name='class_unenrol'),
    path('<int:pk>/waiting-list/<int:member_pk>/remove/', views.RemoveFromWaitingListView.as_view(), name='class_waiting_remove'),
    path('<int:pk>/generate-sessions/', views.GenerateSessionsView.as_view(), name='class_generate_sessions'),
    path('<int:pk>/sessions/<int:session_pk>/register/', views.AttendanceRegisterView.as_view(), name='session_register'),
    path('<int:pk>/sessions/<int:session_pk>/cancel/', views.CancelSessionView.as_view(), name='session_cancel'),
    path('<int:pk>/sessions/<int:session_pk>/print/', views.PrintRegisterView.as_view(), name='session_print'),
    path('<int:pk>/coaches/add/', views.AddCoachView.as_view(), name='class_coach_add'),
    path('<int:pk>/coaches/<int:coach_pk>/remove/', views.RemoveCoachView.as_view(), name='class_coach_remove'),
    path('<int:pk>/helpers/add/', views.AddHelperView.as_view(), name='class_helper_add'),
    path('<int:pk>/helpers/<int:helper_pk>/remove/', views.RemoveHelperView.as_view(), name='class_helper_remove'),
    path('<int:pk>/default-leader/', views.SetClassLeaderView.as_view(), name='class_set_default_leader'),
    path('attendance/', views.AttendanceAnalyticsView.as_view(), name='attendance_analytics'),
    path('attendance/export/', views.AttendanceExportView.as_view(), name='attendance_export'),
    path('my-classes/', views.CoachClassListView.as_view(), name='coach_class_list'),
    path('<int:pk>/my-view/', views.CoachClassDetailView.as_view(), name='coach_class_detail'),
]
