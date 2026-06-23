from datetime import date, timedelta

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.core.management.base import BaseCommand
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from billing.models import Invoice


class Command(BaseCommand):
    help = 'Send payment reminders for overdue invoices'

    def add_arguments(self, parser):
        parser.add_argument(
            '--days', type=int, default=7,
            help='Send reminder if overdue by at least this many days (default: 7)'
        )
        parser.add_argument(
            '--resend-after', type=int, default=14,
            help='Re-send reminder if last one was sent this many days ago (default: 14)'
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Print what would be sent without actually sending'
        )

    def handle(self, *args, **options):
        today = date.today()
        overdue_cutoff = today - timedelta(days=options['days'])
        resend_cutoff = timezone.now() - timedelta(days=options['resend_after'])

        invoices = Invoice.objects.filter(
            status=Invoice.Status.UNPAID,
            due_date__lte=overdue_cutoff,
            member__is_active=True,
        ).filter(
            # Never reminded, or last reminder was long enough ago
            reminder_sent_at__isnull=True
        ) | Invoice.objects.filter(
            status=Invoice.Status.UNPAID,
            due_date__lte=overdue_cutoff,
            member__is_active=True,
            reminder_sent_at__lte=resend_cutoff,
        )

        invoices = invoices.select_related('member', 'organisation').distinct()
        sent = 0

        for invoice in invoices:
            member = invoice.member
            org = invoice.organisation
            recipient = None
            has_guardians = False

            if member.email:
                recipient = member.email
            else:
                guardian = member.guardians.filter(email__isnull=False).exclude(email='').first()
                if guardian:
                    recipient = guardian.email
                    has_guardians = True

            if not recipient:
                self.stdout.write(f'  SKIP {member.name} — no email address')
                continue

            portal_url = settings.SITE_URL.rstrip('/') + reverse(
                'member_portal', kwargs={'token': member.token}
            )

            context = {
                'member': member,
                'invoice': invoice,
                'org_name': org.name,
                'org_email': org.email or settings.DEFAULT_FROM_EMAIL,
                'portal_url': portal_url,
                'has_guardians': has_guardians,
            }
            html = render_to_string('emails/invoice_reminder.html', context)
            subject = f"Payment reminder — {invoice.period} ({org.name})"

            if options['dry_run']:
                self.stdout.write(f'  DRY RUN: would email {recipient} re invoice #{invoice.pk} ({invoice.period})')
                continue

            msg = EmailMultiAlternatives(
                subject=subject,
                body=f"Payment reminder for {invoice.period}. Please visit {portal_url} to pay.",
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=[recipient],
            )
            msg.attach_alternative(html, 'text/html')
            msg.send()

            invoice.reminder_sent_at = timezone.now()
            invoice.save(update_fields=['reminder_sent_at'])
            sent += 1
            self.stdout.write(f'  SENT to {recipient} — {member.name} / {invoice.period}')

        self.stdout.write(self.style.SUCCESS(f'Done — {sent} reminder(s) sent.'))
