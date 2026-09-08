"""Fetch policy and the SSRF boundary.

The rule that matters: validate **the address actually connected to**, not one
from an earlier lookup. Resolution happens once here, the result is filtered,
and the connector is handed a specific validated IP with the original hostname
carried in SNI and the Host header. A second DNS answer cannot be substituted
between check and connect, because there is no second lookup.

Resolver and connector are injected so the whole policy is exercised without a
network in tests.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Callable, Mapping, Protocol, Sequence
from urllib.parse import urljoin, urlsplit

ALLOWED_SCHEMES = frozenset({"http", "https"})
ALLOWED_CONTENT_TYPES = frozenset({"text/html", "text/plain", "application/xhtml+xml"})
FORBIDDEN_URL_CHARS = ("\\", "\t", "\n", "\r", " ")
DEFAULT_PORTS = {"http": 80, "https": 443}


class Reason(StrEnum):
    """Why a fetch was refused. Operator-facing; never returned to a caller."""

    BAD_SCHEME = "bad_scheme"
    BAD_URL = "bad_url"
    BLOCKED_ADDRESS = "blocked_address"
    UNRESOLVABLE = "unresolvable"
    TOO_MANY_REDIRECTS = "too_many_redirects"
    BAD_REDIRECT = "bad_redirect"
    TOO_LARGE = "too_large"
    BAD_CONTENT_TYPE = "bad_content_type"
    BAD_STATUS = "bad_status"


class FetchError(Exception):
    def __init__(self, reason: Reason) -> None:
        # No address, host, or URL in the message: these end up in logs, and the
        # blocked destination is the caller's data, not ours to record.
        super().__init__(str(reason))
        self.reason = reason


@dataclass(frozen=True, slots=True)
class FetchPolicy:
    max_bytes: int = 2_000_000
    max_redirects: int = 5
    timeout_seconds: float = 10.0
    allowed_content_types: frozenset[str] = field(default=ALLOWED_CONTENT_TYPES)


@dataclass(frozen=True, slots=True)
class Target:
    scheme: str
    host: str
    port: int
    path: str

    @property
    def origin_url(self) -> str:
        return f"{self.scheme}://{self.host}:{self.port}{self.path}"


@dataclass(frozen=True, slots=True)
class RawResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


@dataclass(frozen=True, slots=True)
class FetchedDocument:
    final_url: str
    content_type: str
    body: bytes


class Connector(Protocol):
    def __call__(
        self, target: Target, address: str, *, timeout: float
    ) -> RawResponse: ...


Resolver = Callable[[str, int], Sequence[str]]


def _unwrap(ip: ipaddress.IPv4Address | ipaddress.IPv6Address):
    """Reduce IPv4-in-IPv6 forms to the v4 address they actually reach."""
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return ip.ipv4_mapped
        if ip.sixtofour is not None:
            return ip.sixtofour
        # ::ffff:0:0/96 is covered by ipv4_mapped; ::a.b.c.d is deprecated but
        # still routable on some stacks, so unwrap it too.
        packed = ip.packed
        if packed[:12] == b"\x00" * 12 and packed[12:] != b"\x00" * 4:
            return ipaddress.IPv4Address(packed[12:])
    return ip


def assert_address_allowed(raw: str) -> None:
    """Reject anything that is not a globally routable unicast address.

    `is_global` is necessary but NOT sufficient, which is easy to get wrong:
    Python reports `is_global is True` for multicast (224.0.0.0/4, ff00::/8),
    because multicast is globally *scoped* rather than private. Relying on it
    alone would permit multicast destinations, so each disqualifying class is
    also checked explicitly.

    `is_global` does cover loopback, RFC1918, carrier-grade NAT, link-local —
    and with it the 169.254.169.254 metadata endpoint — reserved ranges, the
    unspecified address, and IPv6 unique-local.
    """
    try:
        ip = ipaddress.ip_address(raw)
    except ValueError as exc:
        raise FetchError(Reason.BLOCKED_ADDRESS) from exc
    ip = _unwrap(ip)
    disqualified = (
        not ip.is_global
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_unspecified
    )
    if disqualified:
        raise FetchError(Reason.BLOCKED_ADDRESS)


def validate_url(raw: str) -> Target:
    if any(ch in raw for ch in FORBIDDEN_URL_CHARS):
        # Backslashes, tabs, newlines and spaces are how one parser is made to
        # disagree with another about where the host ends.
        raise FetchError(Reason.BAD_URL)
    parts = urlsplit(raw)
    scheme = parts.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise FetchError(Reason.BAD_SCHEME)
    if parts.username or parts.password:
        # userinfo is a classic way to make a URL look like it points somewhere else.
        raise FetchError(Reason.BAD_URL)
    host = parts.hostname
    if not host:
        raise FetchError(Reason.BAD_URL)
    try:
        port = parts.port or DEFAULT_PORTS[scheme]
    except ValueError as exc:
        raise FetchError(Reason.BAD_URL) from exc
    if not 1 <= port <= 65535:
        raise FetchError(Reason.BAD_URL)
    path = parts.path or "/"
    if parts.query:
        path = f"{path}?{parts.query}"
    return Target(scheme=scheme, host=host, port=port, path=path)


def resolve_allowed(target: Target, resolver: Resolver) -> str:
    """Return one validated address, or raise.

    Every answer is checked, not just the one chosen: a host that resolves to a
    mix of public and private addresses is treated as hostile rather than as a
    reason to pick the public one.
    """
    try:
        addresses = list(resolver(target.host, target.port))
    except Exception as exc:
        raise FetchError(Reason.UNRESOLVABLE) from exc
    if not addresses:
        raise FetchError(Reason.UNRESOLVABLE)
    for address in addresses:
        assert_address_allowed(address)
    return addresses[0]


def _content_type(headers: Mapping[str, str]) -> str:
    for key, value in headers.items():
        if key.lower() == "content-type":
            return value.split(";", 1)[0].strip().lower()
    return ""


def _location(headers: Mapping[str, str]) -> str | None:
    for key, value in headers.items():
        if key.lower() == "location":
            return value
    return None


def fetch(
    url: str,
    *,
    policy: FetchPolicy,
    resolver: Resolver,
    connector: Connector,
) -> FetchedDocument:
    """Fetch one document, validating every hop.

    No Accept-Encoding is offered anywhere in this path, so there is no
    decompression step and therefore no decompression bomb to bound.
    """
    current = url
    for _ in range(policy.max_redirects + 1):
        target = validate_url(current)
        address = resolve_allowed(target, resolver)
        response = connector(target, address, timeout=policy.timeout_seconds)

        if response.status in (301, 302, 303, 307, 308):
            location = _location(response.headers)
            if not location:
                raise FetchError(Reason.BAD_REDIRECT)
            # Resolve relative redirects against the hop we actually made, then
            # loop: the next iteration revalidates scheme, host and address.
            current = urljoin(f"{target.scheme}://{target.host}:{target.port}{target.path}", location)
            continue

        if not 200 <= response.status < 300:
            raise FetchError(Reason.BAD_STATUS)
        content_type = _content_type(response.headers)
        if content_type not in policy.allowed_content_types:
            raise FetchError(Reason.BAD_CONTENT_TYPE)
        if len(response.body) > policy.max_bytes:
            raise FetchError(Reason.TOO_LARGE)
        return FetchedDocument(
            final_url=current, content_type=content_type, body=response.body
        )

    raise FetchError(Reason.TOO_MANY_REDIRECTS)
