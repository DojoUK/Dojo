from django.urls import path
from . import views

urlpatterns = [
    path('', views.ProgressionSettingsView.as_view(), name='progression_settings'),
    path('systems/add/', views.AddSystemView.as_view(), name='progression_system_add'),
    path('systems/<int:pk>/delete/', views.DeleteSystemView.as_view(), name='progression_system_delete'),
    path('systems/<int:pk>/toggle-auto/', views.ToggleAutoAssignView.as_view(), name='progression_system_toggle_auto'),
    path('systems/<int:system_pk>/stages/add/', views.AddStageView.as_view(), name='progression_stage_add'),
    path('systems/<int:system_pk>/stages/<int:pk>/delete/', views.DeleteStageView.as_view(), name='progression_stage_delete'),
    path('systems/<int:system_pk>/stages/<int:pk>/move/', views.MoveStageView.as_view(), name='progression_stage_move'),
    path('systems/<int:system_pk>/stages/<int:pk>/set-default/', views.SetDefaultStageView.as_view(), name='progression_stage_set_default'),
    path('systems/<int:system_pk>/stages/<int:pk>/edit/', views.EditStageView.as_view(), name='progression_stage_edit'),
    path('systems/<int:pk>/apply-default/', views.ApplyDefaultStageView.as_view(), name='progression_system_apply_default'),
    path('import/', views.ImportProgressionView.as_view(), name='progression_import'),
]
