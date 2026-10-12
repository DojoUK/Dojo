"""
Encryption for email passwords and API keys stored in the database.

The key is derived from SECRET_KEY, so a copy of the database alone doesn't
reveal them. Changing SECRET_KEY makes them unreadable: Settings → Email then
asks for them again.
"""
import base64
import hashlib
import json

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


def _fernet():
    digest = hashlib.sha256(b'dojo.mailer.secrets:' + settings.SECRET_KEY.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_dict(values):
    if not values:
        return ''
    return _fernet().encrypt(json.dumps(values).encode()).decode()


def decrypt_dict(token):
    """The stored values, or {} if there are none or they can't be read (e.g. SECRET_KEY changed)."""
    if not token:
        return {}
    try:
        return json.loads(_fernet().decrypt(token.encode()))
    except (InvalidToken, ValueError):
        return {}
