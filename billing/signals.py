from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Invoice


@receiver(post_save, sender=Invoice)
def queue_automatic_payment(sender, instance, created, **kwargs):
    """New invoices for members who pay automatically are queued for the scheduler to collect."""
    if created:
        from .autopay import mark_due
        transaction.on_commit(lambda: mark_due(instance))
