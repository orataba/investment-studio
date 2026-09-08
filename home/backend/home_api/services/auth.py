from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from pathlib import Path


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode_unpadded(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def read_private_text(path: Path | None, label: str) -> str:
    if path is None:
        raise ValueError(f"{label} is not configured.")
    resolved = path.expanduser()
    if not resolved.is_file():
        raise ValueError(f"{label} does not exist.")
    if resolved.stat().st_mode & 0o077:
        raise ValueError(f"{label} must not be accessible by group or others.")
    value = resolved.read_text(encoding="utf-8").strip()
    if not value:
        raise ValueError(f"{label} is empty.")
    return value


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=32768, r=8, p=1, dklen=32, maxmem=64 * 1024 * 1024)
    return f"scrypt$32768$8$1${_encode(salt)}${_encode(digest)}"


def verify_password(password: str, encoded_hash: str | None) -> bool:
    try:
        scheme, raw_n, raw_r, raw_p, raw_salt, raw_digest = (encoded_hash or "").split("$")
        if scheme != "scrypt":
            return False
        expected = _decode_unpadded(raw_digest)
        actual = hashlib.scrypt(password.encode("utf-8"), salt=_decode_unpadded(raw_salt), n=int(raw_n), r=int(raw_r), p=int(raw_p), dklen=len(expected), maxmem=128 * 1024 * 1024)
    except (TypeError, ValueError):
        return False
    return hmac.compare_digest(actual, expected)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_token() -> str:
    return secrets.token_urlsafe(32)
