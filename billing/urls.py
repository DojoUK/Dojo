from django.urls import path
from . import views

urlpatterns = [
    path('', views.InvoiceListView.as_view(), name='invoice_list'),
    path('create/', views.InvoiceCreateView.as_view(), name='invoice_create'),
    path('bulk/', views.BulkInvoiceView.as_view(), name='invoice_bulk'),
    path('chase-overdue/', views.ChaseOverdueView.as_view(), name='invoice_chase_overdue'),
    path('export/', views.BillingExportView.as_view(), name='billing_export'),
    path('<int:pk>/', views.InvoiceDetailView.as_view(), name='invoice_detail'),
    path('<int:pk>/mark-paid/', views.MarkPaidView.as_view(), name='invoice_mark_paid'),
    path('<int:pk>/mark-unpaid/', views.MarkUnpaidView.as_view(), name='invoice_mark_unpaid'),
    path('<int:pk>/record-payment/', views.RecordPaymentView.as_view(), name='invoice_record_payment'),
    path('<int:pk>/send/', views.SendInvoiceEmailView.as_view(), name='invoice_send_email'),
    path('<int:pk>/remind/', views.SendReminderEmailView.as_view(), name='invoice_send_reminder'),

    # Billing policies & terms
    path('policies/', views.BillingPolicyListView.as_view(), name='billing_policies'),
    path('policies/create/', views.BillingPolicyCreateView.as_view(), name='billing_policy_create'),
    path('policies/<int:pk>/edit/', views.BillingPolicyEditView.as_view(), name='billing_policy_edit'),
    path('policies/<int:pk>/delete/', views.BillingPolicyDeleteView.as_view(), name='billing_policy_delete'),
    path('policies/<int:pk>/discount/add/', views.PolicyDiscountCreateView.as_view(), name='billing_discount_add'),
    path('discounts/<int:pk>/delete/', views.PolicyDiscountDeleteView.as_view(), name='billing_discount_delete'),
    path('terms/add/', views.OrgTermCreateView.as_view(), name='billing_term_add'),
    path('terms/<int:pk>/delete/', views.OrgTermDeleteView.as_view(), name='billing_term_delete'),

    # Member discounts
    path('member/<int:member_pk>/discount/add/', views.MemberDiscountAddView.as_view(), name='member_discount_add'),
    path('member-discount/<int:pk>/remove/', views.MemberDiscountRemoveView.as_view(), name='member_discount_remove'),
]
