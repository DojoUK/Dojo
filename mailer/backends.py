"""
Email backends. `ClubBackend` is Dojo's EMAIL_BACKEND: it looks up the club's
choice in Settings → Email and hands each email to the matching sender below.
"""
import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from email.utils import parseaddr

from django.conf import settings
from django.core.mail import get_connection
from django.core.mail.backends.base import BaseEmailBackend
from django.core.mail.backends.smtp import EmailBackend as SmtpBackend

from . import providers

TIMEOUT_SECONDS = 20


class SendError(Exception):
    pass


def _only_organisation():
    from organisations.models import Organisation
    orgs = list(Organisation.objects.all()[:2])
    return orgs[0] if len(orgs) == 1 else None


class ClubBackend(BaseEmailBackend):
    """
    Sends with the club's chosen service. Pass organisation= (see mailer.connection_for);
    without one, a server with a single club uses that club's settings. Anything else
    falls back to the EMAIL_* environment variables.
    """

    def __init__(self, organisation=None, fail_silently=False, **kwargs):
        super().__init__(fail_silently=fail_silently)
        from .models import EmailSettings
        organisation = organisation or _only_organisation()
        self.email_settings = (
            EmailSettings.objects.filter(organisation=organisation).select_related('organisation').first()
            if organisation else None
        )
        if self.email_settings:
            self.delegate = backend_for(self.email_settings, fail_silently)
        else:
            self.delegate = get_connection(settings.DOJO_FALLBACK_EMAIL_BACKEND, fail_silently=fail_silently)

    def open(self):
        return self.delegate.open()

    def close(self):
        return self.delegate.close()

    def send_messages(self, email_messages):
        if self.email_settings:
            for message in email_messages:
                # Messages built without a sender get DEFAULT_FROM_EMAIL; use the club's address instead.
                if self.email_settings.from_address and message.from_email == settings.DEFAULT_FROM_EMAIL:
                    message.from_email = self.email_settings.from_address
                # Replies go to the address chosen in Settings → Email, else the club's contact email.
                reply_to = self.email_settings.reply_to or self.email_settings.organisation.email
                if reply_to and not message.reply_to:
                    message.reply_to = [reply_to]
        return self.delegate.send_messages(email_messages)


def backend_for(email_settings, fail_silently=False):
    """The backend that sends with `email_settings`' provider."""
    key = email_settings.provider
    value = email_settings.value
    smtp = {'fail_silently': fail_silently, 'timeout': TIMEOUT_SECONDS}
    if key == 'log':
        return get_connection('django.core.mail.backends.console.EmailBackend', fail_silently=fail_silently)
    if key == 'dojo_mail':
        return DkimSmtpBackend(
            host=settings.DOJO_MAIL_HOST, port=settings.DOJO_MAIL_PORT, use_tls=False, use_ssl=False,
            username='', password='', dkim_settings=email_settings, **smtp,
        )
    if key == 'ses':
        return SmtpBackend(
            host=f"email-smtp.{value('region')}.amazonaws.com", port=587, use_tls=True,
            username=value('username'), password=value('password'), **smtp,
        )
    if key in ('gmail', 'microsoft365'):
        provider = providers.get(key)
        return SmtpBackend(
            host=provider.smtp_host, port=provider.smtp_port, use_tls=True,
            username=value('username'), password=value('password'), **smtp,
        )
    if key == 'smtp':
        security = value('security')
        return SmtpBackend(
            host=value('host'), port=int(value('port') or 587),
            use_tls=security == 'starttls', use_ssl=security == 'ssl',
            username=value('username'), password=value('password'), **smtp,
        )
    api = {'brevo': BrevoBackend, 'mailgun': MailgunBackend, 'postmark': PostmarkBackend, 'resend': ResendBackend}
    return api[key](email_settings, fail_silently=fail_silently)


class DkimSmtpBackend(SmtpBackend):
    """SMTP to Dojo's mail container, signing each email with the club's DKIM key first."""

    def __init__(self, *args, dkim_settings=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.dkim_settings = dkim_settings

    def _send(self, email_message):
        import email.policy
        import smtplib

        import dkim
        if not email_message.recipients():
            return False
        from_email = self.prep_address(email_message.from_email)
        recipients = [self.prep_address(addr) for addr in email_message.recipients()]
        message = email_message.message(policy=email.policy.SMTP).as_bytes()
        key = self.dkim_settings.dkim_private_key() if self.dkim_settings else ''
        if key:
            signature = dkim.sign(
                message, self.dkim_settings.dkim_selector.encode(),
                self.dkim_settings.sending_domain.encode(), key.encode(),
                include_headers=[b'from', b'to', b'cc', b'subject', b'date', b'message-id',
                                 b'reply-to', b'mime-version', b'content-type'],
            )
            message = signature + message
        try:
            self.connection.sendmail(from_email, recipients, message)
        except smtplib.SMTPException:
            if not self.fail_silently:
                raise
            return False
        return True


# Web API senders: for hosts that block email ports, and for services that prefer them.

def message_parts(message):
    """(text, html) bodies of a Django email message."""
    text = message.body if message.content_subtype == 'plain' else ''
    html = message.body if message.content_subtype == 'html' else ''
    for content, mimetype in getattr(message, 'alternatives', []):
        if mimetype == 'text/html':
            html = content
    return text, html


class HttpApiBackend(BaseEmailBackend):
    name = ''

    def __init__(self, email_settings, fail_silently=False, **kwargs):
        super().__init__(fail_silently=fail_silently)
        self.email_settings = email_settings

    def send_messages(self, email_messages):
        sent = 0
        for message in email_messages:
            if not message.recipients():
                continue
            try:
                self.send_one(message)
                sent += 1
            except SendError:
                if not self.fail_silently:
                    raise
        return sent

    def post(self, url, headers, data=None, json_body=None):
        if json_body is not None:
            body = json.dumps(json_body).encode()
            headers = {**headers, 'Content-Type': 'application/json'}
        else:
            body = urllib.parse.urlencode(data, doseq=True).encode()
        request = urllib.request.Request(url, data=body, headers={'Accept': 'application/json', **headers}, method='POST')
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            detail = error.read().decode(errors='replace')[:300]
            raise SendError(f'{self.name} refused the email ({error.code}): {detail}') from None
        except (urllib.error.URLError, TimeoutError) as error:
            raise SendError(f'Couldn\'t reach {self.name}: {getattr(error, "reason", error)}') from None


class BrevoBackend(HttpApiBackend):
    name = 'Brevo'

    def send_one(self, message):
        text, html = message_parts(message)
        sender_name, sender_email = parseaddr(message.from_email)
        payload = {
            'sender': {'name': sender_name, 'email': sender_email} if sender_name else {'email': sender_email},
            'to': [{'email': address} for address in message.to],
            'subject': message.subject,
        }
        if message.cc:
            payload['cc'] = [{'email': address} for address in message.cc]
        if message.bcc:
            payload['bcc'] = [{'email': address} for address in message.bcc]
        if html:
            payload['htmlContent'] = html
        if text:
            payload['textContent'] = text
        if message.reply_to:
            payload['replyTo'] = {'email': parseaddr(message.reply_to[0])[1]}
        self.post('https://api.brevo.com/v3/smtp/email',
                  {'api-key': self.email_settings.value('api_key')}, json_body=payload)


class PostmarkBackend(HttpApiBackend):
    name = 'Postmark'

    def send_one(self, message):
        text, html = message_parts(message)
        payload = {
            'From': message.from_email, 'To': ', '.join(message.to), 'Subject': message.subject,
            'MessageStream': 'outbound',
        }
        if message.cc:
            payload['Cc'] = ', '.join(message.cc)
        if message.bcc:
            payload['Bcc'] = ', '.join(message.bcc)
        if text:
            payload['TextBody'] = text
        if html:
            payload['HtmlBody'] = html
        if message.reply_to:
            payload['ReplyTo'] = ', '.join(message.reply_to)
        self.post('https://api.postmarkapp.com/email',
                  {'X-Postmark-Server-Token': self.email_settings.value('api_key')}, json_body=payload)


class ResendBackend(HttpApiBackend):
    name = 'Resend'

    def send_one(self, message):
        text, html = message_parts(message)
        payload = {'from': message.from_email, 'to': list(message.to), 'subject': message.subject}
        if message.cc:
            payload['cc'] = list(message.cc)
        if message.bcc:
            payload['bcc'] = list(message.bcc)
        if text:
            payload['text'] = text
        if html:
            payload['html'] = html
        if message.reply_to:
            payload['reply_to'] = list(message.reply_to)
        self.post('https://api.resend.com/emails',
                  {'Authorization': f"Bearer {self.email_settings.value('api_key')}"}, json_body=payload)


class MailgunBackend(HttpApiBackend):
    name = 'Mailgun'

    def send_one(self, message):
        text, html = message_parts(message)
        data = {'from': message.from_email, 'to': list(message.to), 'subject': message.subject}
        if message.cc:
            data['cc'] = list(message.cc)
        if message.bcc:
            data['bcc'] = list(message.bcc)
        if text:
            data['text'] = text
        if html:
            data['html'] = html
        if message.reply_to:
            data['h:Reply-To'] = ', '.join(message.reply_to)
        host = 'api.eu.mailgun.net' if self.email_settings.value('region') == 'eu' else 'api.mailgun.net'
        domain = urllib.parse.quote(self.email_settings.value('domain'))
        auth = base64.b64encode(f"api:{self.email_settings.value('api_key')}".encode()).decode()
        self.post(f'https://{host}/v3/{domain}/messages', {'Authorization': f'Basic {auth}'}, data=data)
