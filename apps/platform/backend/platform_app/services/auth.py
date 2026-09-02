from __future__ import annotations

import base64
import hashlib
import hmac
import json
from pathlib import Path
import time


_HASH_SCHEME = "scrypt"


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value.encode("ascii"))


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode_unpadded(value: str) -> bytes:
    return _decode(value + "=" * (-len(value) % 4))


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


def verify_password(password: str, encoded_hash: str) -> bool:
    try:
        scheme, raw_n, raw_r, raw_p, raw_salt, raw_digest = encoded_hash.split("$")
        if scheme != _HASH_SCHEME:
            return False
        expected = _decode_unpadded(raw_digest)
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=_decode_unpadded(raw_salt),
            n=int(raw_n),
            r=int(raw_r),
            p=int(raw_p),
            dklen=len(expected),
        )
    except (TypeError, ValueError):
        return False
    return hmac.compare_digest(actual, expected)


def issue_session(username: str, secret: str, ttl_seconds: int) -> str:
    payload = json.dumps(
        {
            "exp": int(time.time()) + ttl_seconds,
            "sub": username,
            "v": 1,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    encoded_payload = _encode(payload)
    signature = hmac.new(
        secret.encode("utf-8"),
        encoded_payload.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"{encoded_payload}.{_encode(signature)}"


def validate_session(token: str | None, secret: str, username: str) -> bool:
    if not token:
        return False
    try:
        encoded_payload, encoded_signature = token.split(".", 1)
        supplied_signature = _decode_unpadded(encoded_signature)
        expected_signature = hmac.new(
            secret.encode("utf-8"),
            encoded_payload.encode("ascii"),
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(supplied_signature, expected_signature):
            return False
        payload = json.loads(_decode_unpadded(encoded_payload))
        return bool(
            payload.get("v") == 1
            and payload.get("sub") == username
            and int(payload.get("exp", 0)) >= int(time.time())
        )
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
        return False
