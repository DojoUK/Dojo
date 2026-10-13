"""
"Check connection" in Settings → Email: can this server reach the chosen
service, and does the service accept the saved login or key? Nothing is sent.
"""
import base64
import smtplib
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings

from . import providers

TIMEOUT_SECONDS = 8
# A big mail provider's inbound server, to test whether outgoing port 25 is open.
PORT_25_TEST_DOMAIN = 'gmail.com'


def check(email_settings):
    """A list of (what was checked, passed, explanation)."""
    key = email_settings.provider
    if key == 'log':
        return [('Nothing to check', True, 'Emails are written to the log, not sent.')]
    if key == 'dojo_mail':
        return [_mail_container(), _outgoing_port_25()]
    if key in ('ses', 'gmail', 'microsoft365', 'smtp'):
        return [_smtp_login(email_settings)]
    return [_api_key(email_settings)]


def _read_banner(host, port):
    with socket.create_connection((host, port), timeout=TIMEOUT_SECONDS) as conn:
        conn.settimeout(TIMEOUT_SECONDS)
        return conn.recv(512).decode(errors='replace')


def _mail_container():
    label = 'Dojo can reach its mail container'
    try:
        banner = _read_banner(settings.DOJO_MAIL_HOST, settings.DOJO_MAIL_PORT)
    except OSError as error:
        return (label, False, (f'Couldn\'t connect to {settings.DOJO_MAIL_HOST}:{settings.DOJO_MAIL_PORT} ({error}). '
                               'Is the mail container running? Try: docker compose up -d mail'))
    return (label, banner.startswith('220'), banner.strip()[:120] or 'No greeting received.')


def _first_mx(domain):
    import dns.exception
    import dns.resolver
    try:
        answers = sorted(dns.resolver.resolve(domain, 'MX', lifetime=5), key=lambda r: r.preference)
        return str(answers[0].exchange).rstrip('.')
    except (dns.exception.DNSException, OSError):  # fall back to a well-known host
        return 'gmail-smtp-in.l.google.com'


def _outgoing_port_25():
    label = 'This server can send email on port 25'
    host = _first_mx(PORT_25_TEST_DOMAIN)
    try:
        banner = _read_banner(host, 25)
    except OSError:
        return (label, False, ('Port 25 looks blocked: the connection to a Gmail mail server didn\'t go through. '
                               'Ask your hosting company to unblock outgoing port 25, or choose a sending service '
                               'such as Brevo instead.'))
    if not banner.startswith('220'):
        return (label, False, f'Unexpected reply from {host}: {banner.strip()[:120]}')
    return (label, True, f'Connected to {host} and got a greeting. Port 25 is open.')


def _smtp_target(email_settings):
    value = email_settings.value
    key = email_settings.provider
    if key == 'ses':
        return f"email-smtp.{value('region')}.amazonaws.com", 587, 'starttls'
    if key in ('gmail', 'microsoft365'):
        provider = providers.get(key)
        return provider.smtp_host, provider.smtp_port, provider.smtp_security
    return value('host'), int(value('port') or 587), value('security')


def _smtp_login(email_settings):
    host, port, security = _smtp_target(email_settings)
    username, password = email_settings.value('username'), email_settings.value('password')
    label = f'Connect to {host}:{port}' + (' and sign in' if username else '')
    try:
        context = ssl.create_default_context()
        if security == 'ssl':
            server = smtplib.SMTP_SSL(host, port, timeout=TIMEOUT_SECONDS, context=context)
        else:
            server = smtplib.SMTP(host, port, timeout=TIMEOUT_SECONDS)
            if security == 'starttls':
                server.starttls(context=context)
        try:
            if username:
                server.login(username, password)
        finally:
            server.quit()
    except smtplib.SMTPAuthenticationError:
        return (label, False, 'The server was reached, but it didn\'t accept the username and password.')
    except (smtplib.SMTPException, OSError, ssl.SSLError) as error:
        return (label, False, (f'Couldn\'t connect: {error}. Check the server and port, and that your host '
                               f'allows outgoing connections on port {port}.'))
    return (label, True, 'Connected and signed in.' if username else 'Connected.')


def _api_key(email_settings):
    key = email_settings.provider
    value = email_settings.value
    name = providers.get(key).label
    if key == 'brevo':
        url, headers = 'https://api.brevo.com/v3/account', {'api-key': value('api_key')}
    elif key == 'postmark':
        url, headers = 'https://api.postmarkapp.com/server', {'X-Postmark-Server-Token': value('api_key')}
    elif key == 'resend':
        url, headers = 'https://api.resend.com/domains', {'Authorization': f"Bearer {value('api_key')}"}
    else:  # mailgun
        host = 'api.eu.mailgun.net' if value('region') == 'eu' else 'api.mailgun.net'
        url = f"https://{host}/v3/domains/{urllib.parse.quote(value('domain'))}"
        headers = {'Authorization': 'Basic ' + base64.b64encode(f"api:{value('api_key')}".encode()).decode()}
    label = f'Reach {name} and check the key'
    request = urllib.request.Request(url, headers={'Accept': 'application/json', **headers})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS):
            pass
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors='replace')
        # Resend's send-only keys can't list domains, but that reply proves the key is real.
        if key == 'resend' and 'restricted' in detail.lower():
            return (label, True, 'Connected. The key is a send-only key, which is fine.')
        if error.code in (401, 403):
            return (label, False, f'{name} was reached, but didn\'t accept the key.')
        if key == 'mailgun' and error.code == 404:
            return (label, False, 'Mailgun was reached, but doesn\'t know that sending domain (check the region too).')
        return (label, False, f'{name} replied with an error ({error.code}).')
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        return (label, False, f'Couldn\'t reach {name}: {getattr(error, "reason", error)}.')
    return (label, True, 'Connected, and the key was accepted.')
