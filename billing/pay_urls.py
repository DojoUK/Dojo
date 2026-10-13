from django.urls import path

from . import pay_views

urlpatterns = [
    path('<str:token>/', pay_views.InvoicePayView.as_view(), name='invoice_pay'),
    path('<str:token>/card/', pay_views.InvoiceCardPaymentView.as_view(), name='invoice_pay_card'),
]
