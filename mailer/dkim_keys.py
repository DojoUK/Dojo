"""DKIM: the signature that tells receiving mail servers an email really came from your domain."""
import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def generate_private_key():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption(),
    ).decode()


def public_key_base64(private_pem):
    key = serialization.load_pem_private_key(private_pem.encode(), password=None)
    der = key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return base64.b64encode(der).decode()


def dns_record_value(private_pem):
    return f'v=DKIM1; k=rsa; p={public_key_base64(private_pem)}'
