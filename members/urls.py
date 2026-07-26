from django.urls import path
from . import views

urlpatterns = [
    path('', views.MemberListView.as_view(), name='member_list'),
    path('add/', views.MemberCreateView.as_view(), name='member_add'),
    path('<int:pk>/', views.MemberDetailView.as_view(), name='member_detail'),
    path('<int:pk>/edit/', views.MemberUpdateView.as_view(), name='member_edit'),
    path('<int:pk>/archive/', views.MemberArchiveView.as_view(), name='member_archive'),
    path('<int:pk>/send-welcome/', views.SendWelcomeEmailView.as_view(), name='member_send_welcome'),
    path('<int:pk>/promote/', views.RecordPromotionView.as_view(), name='member_promote'),
    path('<int:pk>/progression/<int:prog_pk>/delete/', views.DeleteProgressionView.as_view(), name='member_progression_delete'),
    path('import/', views.MemberImportView.as_view(), name='member_import'),
    path('export/', views.MemberExportView.as_view(), name='member_export'),
    path('<int:pk>/notes/add/', views.AddMemberNoteView.as_view(), name='member_note_add'),
    path('<int:pk>/notes/<int:note_pk>/delete/', views.DeleteMemberNoteView.as_view(), name='member_note_delete'),
    path('bulk/', views.MemberBulkActionView.as_view(), name='member_bulk_action'),
    path('applications/', views.ApplicationListView.as_view(), name='application_list'),
    path('applications/<int:pk>/approve/', views.ApproveApplicationView.as_view(), name='application_approve'),
    path('applications/<int:pk>/reject/', views.RejectApplicationView.as_view(), name='application_reject'),
    path('<int:pk>/custom-fields/', views.MemberCustomFieldsView.as_view(), name='member_custom_fields'),
    path('former/', views.FormerMembersView.as_view(), name='former_members'),
    path('<int:pk>/retention-notes/', views.MemberRetentionNotesView.as_view(), name='member_retention_notes'),
]
