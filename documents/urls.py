from django.urls import path
from . import views

urlpatterns = [
    path('members/<int:member_pk>/documents/upload/', views.DocumentUploadView.as_view(), name='document_upload'),
    path('members/<int:member_pk>/documents/<int:pk>/delete/', views.DocumentDeleteView.as_view(), name='document_delete'),
    path('members/<int:member_pk>/documents/<int:pk>/download/', views.DocumentDownloadView.as_view(), name='document_download'),

    path('waivers/', views.WaiverListView.as_view(), name='waiver_list'),
    path('waivers/<int:pk>/delete/', views.WaiverDeleteView.as_view(), name='waiver_delete'),
    path('waivers/<int:pk>/download/', views.WaiverDownloadView.as_view(), name='waiver_download'),

    path('members/<int:member_pk>/signed-waivers/<int:pk>/download/', views.SignedWaiverDownloadView.as_view(), name='signed_waiver_download'),
    path('members/<int:member_pk>/signed-waivers/<int:pk>/delete/', views.SignedWaiverDeleteView.as_view(), name='signed_waiver_delete'),
    path('members/<int:member_pk>/signed-waivers/offline/', views.SignedWaiverOfflineView.as_view(), name='signed_waiver_offline'),
]
