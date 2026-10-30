"""RSA key pair for the Snowflake service user.

Key-pair sign-in means no password exists to leak or rotate, and Snowflake's MFA rules for
people don't apply to a `TYPE = SERVICE` user. The private key stays in the user's home
folder, outside the repo; only the public key goes to Snowflake.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

PUBLIC_KEY_PLACEHOLDER = "__RSA_PUBLIC_KEY__"


def _public_body(key: rsa.RSAPrivateKey) -> str:
    """The public key as Snowflake wants it: base64 DER, no PEM header or line breaks."""
    der = key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return base64.b64encode(der).decode("ascii")


def load_private_key(path: Path) -> rsa.RSAPrivateKey:
    key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(key, rsa.RSAPrivateKey):
        raise ValueError(f"{path} is not an RSA private key")
    return key


def private_key_der(path: Path) -> bytes:
    """Unencrypted PKCS#8 DER, the form snowflake-connector-python accepts."""
    return load_private_key(path).private_bytes(
        serialization.Encoding.DER,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def ensure_key_pair(path: Path) -> str:
    """Create the key at `path` unless it exists; return the public key. Never overwrites:
    replacing a key that Snowflake already trusts would lock the service user out."""
    if path.exists():
        return _public_body(load_private_key(path))
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    path.parent.mkdir(parents=True, exist_ok=True)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    # 0o600 at creation, so the key is never readable by others, even briefly (POSIX).
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(pem)
    return _public_body(key)


def fill_bootstrap(template: Path, public_key: str, out: Path) -> Path:
    text = template.read_text(encoding="utf-8")
    if PUBLIC_KEY_PLACEHOLDER not in text:
        raise ValueError(f"{template} has no {PUBLIC_KEY_PLACEHOLDER} marker")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text.replace(PUBLIC_KEY_PLACEHOLDER, public_key), encoding="utf-8", newline="\n")
    return out
