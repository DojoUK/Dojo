from email.utils import formataddr

from django.db import models

from organisations.models import Organisation

from . import crypto, dkim_keys, providers


class EmailSettings(models.Model):
    """How a club's emails are sent: chosen in the setup wizard, changed in Settings → Email."""
    organisation = models.OneToOneField(Organisation, on_delete=models.CASCADE, related_name='email_settings')
    provider = models.CharField(max_length=32, default=providers.DEFAULT)
    from_name = models.CharField(max_length=100, blank=True, help_text='Defaults to the club name.')
    from_email = models.EmailField(blank=True)
    reply_to = models.EmailField(blank=True)
    # Non-secret provider settings (server, port, region...).
    options = models.JSONField(default=dict, blank=True)
    # Passwords, API keys and the DKIM private key, encrypted (see mailer.crypto).
    secrets = models.TextField(blank=True)
    dkim_selector = models.CharField(max_length=63, default='dojo')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = 'email settings'

    def __str__(self):
        return f'Email for {self.organisation}'

    @property
    def provider_info(self):
        return providers.get(self.provider)

    def get_secrets(self):
        return crypto.decrypt_dict(self.secrets)

    def set_secrets(self, values):
        self.secrets = crypto.encrypt_dict({k: v for k, v in values.items() if v})

    def value(self, name):
        """A provider setting, whether it's stored as an option or a secret."""
        setting = next((s for s in self.provider_info.settings if s.name == name), None)
        if setting and setting.secret:
            return self.get_secrets().get(name, '')
        return self.options.get(name, setting.default if setting else '')

    @property
    def from_address(self):
        if not self.from_email:
            return ''
        return formataddr((self.from_name or self.organisation.name, self.from_email))

    @property
    def sending_domain(self):
        return self.from_email.rpartition('@')[2].lower()

    # DKIM, for the built-in mail server.
    def dkim_private_key(self, create=False):
        secrets = self.get_secrets()
        if not secrets.get('dkim_private_key') and create:
            secrets['dkim_private_key'] = dkim_keys.generate_private_key()
            self.set_secrets(secrets)
            self.save(update_fields=['secrets'])
        return secrets.get('dkim_private_key', '')

    def dns_records(self):
        """The DNS records to add for the built-in mail server, as (type, name, value, purpose)."""
        domain = self.sending_domain
        key = self.dkim_private_key()
        if not domain or not key:
            return []
        return [
            ('TXT', f'{self.dkim_selector}._domainkey.{domain}', dkim_keys.dns_record_value(key),
             'DKIM: proves emails from Dojo really come from your domain.'),
            ('TXT', domain, 'v=spf1 a mx ip4:YOUR.SERVER.IP ~all',
             ('SPF: lists the servers allowed to send for your domain. Replace YOUR.SERVER.IP with your '
              'server\'s public address, and merge with any SPF record you already have (a domain can only have one).')),
            ('TXT', f'_dmarc.{domain}', f'v=DMARC1; p=none; rua=mailto:{self.reply_to or self.from_email}',
             'DMARC: tells receiving servers what to do with mail that fails the checks above.'),
        ]
