"""Look up whether a club's DNS records for the built-in mail server are in place."""
import dns.exception
import dns.resolver


def txt_records(name):
    try:
        answer = dns.resolver.resolve(name, 'TXT', lifetime=5)
    except (dns.exception.DNSException, OSError):
        return []
    return [b''.join(record.strings).decode(errors='replace') for record in answer]


def check(email_settings):
    """{record name: True/False} for each DNS record Dojo suggests."""
    results = {}
    for _type, name, value, _purpose in email_settings.dns_records():
        found = txt_records(name)
        if name.startswith(f'{email_settings.dkim_selector}._domainkey.'):
            key = value.split('p=', 1)[1]
            results[name] = any(key in record.replace(' ', '') for record in found)
        elif name.startswith('_dmarc.'):
            results[name] = any(record.lower().startswith('v=dmarc1') for record in found)
        else:
            results[name] = any(record.lower().startswith('v=spf1') for record in found)
    return results
