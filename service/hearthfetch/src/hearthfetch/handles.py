"""Opaque result handles.

The privileged model manipulates references, not values: `search_web` returns
handles and `fetch_result` takes one, so for every search-derived page the model
never sees, holds, or composes a URL.

Two properties are required, and signing alone gives only the first:

  unforgeable  the model cannot mint a handle for a destination of its choosing;
  opaque       the model cannot read a URL back out of one.

A signed-but-readable token (JWT-shaped, base64 payload) fails the second. So the
payload is encrypted with AES-GCM, which authenticates it at the same time.

Handles are sealed, not stored, so the service stays stateless.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

PREFIX = "hf1"
NONCE_BYTES = 12
KEY_BYTES = 32
DEFAULT_TTL_SECONDS = 900


class HandleError(ValueError):
    """A handle is malformed, forged, expired, or from another search call."""


@dataclass(frozen=True, slots=True)
class Handle:
    url: str
    search_id: str


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def new_key() -> bytes:
    return AESGCM.generate_key(bit_length=256)


def new_search_id() -> str:
    return secrets.token_urlsafe(9)


def mint(
    url: str,
    *,
    key: bytes,
    search_id: str,
    now: float,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> str:
    """Seal a URL into a handle bound to one search call and an expiry."""
    if len(key) != KEY_BYTES:
        raise HandleError("handle key must be 32 bytes")
    payload = json.dumps(
        {"u": url, "s": search_id, "e": int(now) + ttl_seconds},
        separators=(",", ":"),
    ).encode("utf-8")
    nonce = os.urandom(NONCE_BYTES)
    sealed = AESGCM(key).encrypt(nonce, payload, PREFIX.encode("ascii"))
    return f"{PREFIX}.{_b64e(nonce)}.{_b64e(sealed)}"


def open_handle(
    token: str,
    *,
    key: bytes,
    now: float,
    search_id: str | None = None,
) -> Handle:
    """Recover a handle's URL, or raise.

    Every failure raises the same exception type with a generic message. The
    caller must not report which check failed: a distinguishable error is an
    oracle for probing handle validity.
    """
    if len(key) != KEY_BYTES:
        raise HandleError("handle key must be 32 bytes")
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != PREFIX:
        raise HandleError("invalid handle")
    try:
        nonce = _b64d(parts[1])
        sealed = _b64d(parts[2])
    except (ValueError, base64.binascii.Error) as exc:  # type: ignore[attr-defined]
        raise HandleError("invalid handle") from exc
    if len(nonce) != NONCE_BYTES:
        raise HandleError("invalid handle")
    try:
        payload = AESGCM(key).decrypt(nonce, sealed, PREFIX.encode("ascii"))
    except InvalidTag as exc:
        raise HandleError("invalid handle") from exc
    try:
        data = json.loads(payload)
        url = data["u"]
        sid = data["s"]
        expiry = data["e"]
    except (ValueError, KeyError, TypeError) as exc:
        raise HandleError("invalid handle") from exc
    if not isinstance(url, str) or not isinstance(sid, str) or not isinstance(expiry, int):
        raise HandleError("invalid handle")
    if now >= expiry:
        raise HandleError("invalid handle")
    if search_id is not None and sid != search_id:
        raise HandleError("invalid handle")
    return Handle(url=url, search_id=sid)
