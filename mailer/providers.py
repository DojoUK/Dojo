"""
The ways Dojo can send email. Each provider lists the settings it needs; the
setup wizard and Settings → Email build their forms from this list.
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Setting:
    name: str
    label: str
    kind: str = 'text'  # text, password, number, select, email
    help: str = ''
    default: str = ''
    choices: tuple = ()
    required: bool = True

    @property
    def secret(self):
        return self.kind == 'password'


@dataclass(frozen=True)
class Provider:
    key: str
    label: str
    summary: str
    settings: tuple = ()
    # For providers reached over SMTP with fixed details.
    smtp_host: str = ''
    smtp_port: int = 587
    smtp_security: str = 'starttls'
    signup_url: str = ''
    tags: tuple = field(default_factory=tuple)


SECURITY_CHOICES = (
    ('starttls', 'STARTTLS (usually port 587)'),
    ('ssl', 'SSL/TLS (usually port 465)'),
    ('none', 'None (only inside a private network)'),
)

SES_REGIONS = (
    ('eu-west-2', 'Europe (London)'),
    ('eu-west-1', 'Europe (Ireland)'),
    ('eu-central-1', 'Europe (Frankfurt)'),
    ('us-east-1', 'US East (N. Virginia)'),
    ('us-east-2', 'US East (Ohio)'),
    ('us-west-2', 'US West (Oregon)'),
    ('ca-central-1', 'Canada (Central)'),
    ('ap-southeast-2', 'Asia Pacific (Sydney)'),
)

PROVIDERS = (
    Provider(
        'log', 'Not yet: write emails to the server log',
        'Nothing is sent. Each email is printed in the web container\'s log, so you can set up sending later.',
    ),
    Provider(
        'dojo_mail', 'Dojo\'s own mail service',
        'Dojo sends directly from your server using its built-in mail container. Free, but your server must be '
        'allowed to send on port 25 (many hosts block it until you ask), and you add a few DNS records so '
        'mail isn\'t marked as spam.',
    ),
    Provider(
        'brevo', 'Brevo',
        'A sending service with a free tier (300 emails a day). Works even where your host blocks email ports.',
        settings=(Setting('api_key', 'API key', 'password', 'Brevo → SMTP & API → API keys.'),),
        signup_url='https://www.brevo.com/',
    ),
    Provider(
        'ses', 'Amazon SES',
        'Amazon\'s sending service: very cheap at volume. Needs SMTP credentials made in the SES console.',
        settings=(
            Setting('region', 'Region', 'select', default='eu-west-2', choices=SES_REGIONS),
            Setting('username', 'SMTP username', help='SES → SMTP settings → Create SMTP credentials.'),
            Setting('password', 'SMTP password', 'password'),
        ),
        signup_url='https://aws.amazon.com/ses/',
    ),
    Provider(
        'mailgun', 'Mailgun',
        'A sending service with a web API. Works even where your host blocks email ports.',
        settings=(
            Setting('api_key', 'API key', 'password'),
            Setting('domain', 'Sending domain', help='The domain you verified in Mailgun, e.g. mg.yourclub.org.uk.'),
            Setting('region', 'Region', 'select', default='eu', choices=(('eu', 'EU'), ('us', 'US'))),
        ),
        signup_url='https://www.mailgun.com/',
    ),
    Provider(
        'postmark', 'Postmark',
        'A sending service known for fast, reliable delivery. Works even where your host blocks email ports.',
        settings=(Setting('api_key', 'Server API token', 'password', 'Postmark → your server → API Tokens.'),),
        signup_url='https://postmarkapp.com/',
    ),
    Provider(
        'resend', 'Resend',
        'A sending service with a free tier (3,000 emails a month). Works even where your host blocks email ports.',
        settings=(Setting('api_key', 'API key', 'password'),),
        signup_url='https://resend.com/',
    ),
    Provider(
        'gmail', 'Gmail or Google Workspace',
        'Send from a Google mailbox. Needs 2-Step Verification on that account and an app password. '
        'Google limits how many emails a day it will send.',
        settings=(
            Setting('username', 'Google email address', 'email'),
            Setting('password', 'App password', 'password', 'Google Account → Security → App passwords.'),
        ),
        smtp_host='smtp.gmail.com',
    ),
    Provider(
        'microsoft365', 'Microsoft 365 / Outlook',
        'Send from a Microsoft 365 mailbox. Your Microsoft 365 admin must allow "authenticated SMTP" for it.',
        settings=(
            Setting('username', 'Mailbox email address', 'email'),
            Setting('password', 'Password or app password', 'password'),
        ),
        smtp_host='smtp.office365.com',
    ),
    Provider(
        'smtp', 'Another SMTP server',
        'Any other email service or mail server: your web host, IONOS, Fastmail, Zoho, SendGrid, your own server.',
        settings=(
            Setting('host', 'Server', help='e.g. smtp.ionos.co.uk'),
            Setting('port', 'Port', 'number', default='587'),
            Setting('security', 'Security', 'select', default='starttls', choices=SECURITY_CHOICES),
            Setting('username', 'Username', required=False),
            Setting('password', 'Password', 'password', required=False),
        ),
    ),
)

BY_KEY = {provider.key: provider for provider in PROVIDERS}
DEFAULT = 'log'


def get(key):
    return BY_KEY.get(key, BY_KEY[DEFAULT])
