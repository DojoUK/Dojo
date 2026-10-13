from django.urls import path

from . import syllabus_views as views

urlpatterns = [
    path('', views.SyllabusHomeView.as_view(), name='syllabus_home'),
    path('techniques/', views.TechniqueListView.as_view(), name='technique_list'),
    path('techniques/<int:pk>/', views.TechniqueEditView.as_view(), name='technique_edit'),
    path('<int:pk>/', views.SyllabusDetailView.as_view(), name='syllabus_detail'),
    path('<int:pk>/edit/', views.SyllabusEditView.as_view(), name='syllabus_edit'),
    path('<int:pk>/delete/', views.SyllabusDeleteView.as_view(), name='syllabus_delete'),
    path('<int:pk>/grades/', views.SyllabusStagesView.as_view(), name='syllabus_stages'),
    path('<int:pk>/headings/add/', views.HeadingAddView.as_view(), name='syllabus_heading_add'),
    path('<int:pk>/headings/<int:heading_pk>/', views.HeadingEditView.as_view(), name='syllabus_heading_edit'),
    path('<int:pk>/techniques/add/', views.EntryAddView.as_view(), name='syllabus_entry_add'),
    path('<int:pk>/techniques/<int:entry_pk>/', views.EntryEditView.as_view(), name='syllabus_entry_edit'),
]
