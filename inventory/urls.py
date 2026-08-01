from django.urls import path
from . import views

urlpatterns = [
    path('', views.ProductListView.as_view(), name='inventory_list'),
    path('products/create/', views.ProductCreateView.as_view(), name='product_create'),
    path('products/<int:pk>/edit/', views.ProductEditView.as_view(), name='product_edit'),
    path('products/<int:pk>/delete/', views.ProductDeleteView.as_view(), name='product_delete'),
    path('products/<int:product_pk>/variants/create/', views.VariantCreateView.as_view(), name='variant_create'),
    path('variants/<int:pk>/edit/', views.VariantEditView.as_view(), name='variant_edit'),
    path('variants/<int:pk>/delete/', views.VariantDeleteView.as_view(), name='variant_delete'),
    path('variants/<int:pk>/adjust-stock/', views.VariantAdjustStockView.as_view(), name='variant_adjust_stock'),
]
