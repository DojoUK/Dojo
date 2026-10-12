"""
Time-based one-time codes (RFC 6238), as used by authenticator apps.

Kept to the standard library: a shared secret, HMAC-SHA1, 30-second steps and
six digits, which is what Google Authenticator, Microsoft Authenticator,
1Password, Bitwarden and the rest all expect.
"""
import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote, urlencode

STEP_SECONDS = 30
DIGITS = 6
# Accept the code before and after the current one, for phones whose clocks drift.
DRIFT_STEPS = 1


def generate_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode()


def current_step(now=None):
    return int((time.time() if now is None else now) // STEP_SECONDS)


def code_at(secret, step):
    key = base64.b32decode(secret.upper() + '=' * (-len(secret) % 8))
    digest = hmac.new(key, struct.pack('>Q', step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack('>I', digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(number % 10 ** DIGITS).zfill(DIGITS)


def matching_step(secret, code, after_step=0, now=None):
    """The time step `code` belongs to, or None. Steps at or before `after_step` are refused (no replays)."""
    code = ''.join(ch for ch in str(code) if ch.isdigit())
    if len(code) != DIGITS:
        return None
    step = current_step(now)
    for candidate in range(step - DRIFT_STEPS, step + DRIFT_STEPS + 1):
        if candidate > after_step and hmac.compare_digest(code_at(secret, candidate), code):
            return candidate
    return None


def provisioning_uri(secret, account, issuer):
    """The otpauth:// link an authenticator app reads from the QR code."""
    label = quote(f'{issuer}:{account}')
    return f'otpauth://totp/{label}?' + urlencode({'secret': secret, 'issuer': issuer})


def qr_svg(data):
    """An inline SVG QR code for `data`."""
    import segno
    return segno.make(data, error='m').svg_inline(scale=5, border=2, dark='#0f172a', light='#ffffff')


def grouped(secret):
    """The secret in fours, for typing into an app by hand."""
    return ' '.join(secret[i:i + 4] for i in range(0, len(secret), 4))
