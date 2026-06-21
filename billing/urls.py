from django.urls import path
from . import views

urlpatterns = [
    path('', views.InvoiceListView.as_view(), name='invoice_list'),
    path('create/', views.InvoiceCreateView.as_view(), name='invoice_create'),
    path('<int:pk>/', views.InvoiceDetailView.as_view(), name='invoice_detail'),
    path('<int:pk>/mark-paid/', views.MarkPaidView.as_view(), name='invoice_mark_paid'),
    path('<int:pk>/mark-unpaid/', views.MarkUnpaidView.as_view(), name='invoice_mark_unpaid'),
    path('<int:pk>/record-payment/', views.RecordPaymentView.as_view(), name='invoice_record_payment'),
]
