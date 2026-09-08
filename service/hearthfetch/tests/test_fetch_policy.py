"""The adversarial suite. No network is touched: resolver and connector are fakes."""

import pytest

from hearthfetch.fetch import (
    FetchError,
    FetchPolicy,
    RawResponse,
    Reason,
    Target,
    assert_address_allowed,
    fetch,
    resolve_allowed,
    validate_url,
)

POLICY = FetchPolicy()
HTML = {"Content-Type": "text/html; charset=utf-8"}


def resolver_for(mapping):
    def _resolve(host, port):
        return mapping[host]
    return _resolve


def connector_for(script):
    """script: final_host -> RawResponse. Records what it was asked to connect to."""
    calls = []

    def _connect(target: Target, address: str, *, timeout: float) -> RawResponse:
        calls.append((target.host, address, target.scheme))
        return script[target.host]

    _connect.calls = calls
    return _connect


# ---------------------------------------------------------------- URL shape

@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "gopher://example.org/",
    "javascript:alert(1)",
    "data:text/html,hi",
    "ftp://example.org/x",
])
def test_non_http_schemes_are_refused(url):
    with pytest.raises(FetchError) as e:
        validate_url(url)
    assert e.value.reason in (Reason.BAD_SCHEME, Reason.BAD_URL)


@pytest.mark.parametrize("url", [
    "https://example.org\\@evil.example/",
    "https://example.org\t/",
    "https://example.org\n/",
    "https://example.org\r/",
    "https://exa mple.org/",
])
def test_parser_confusing_characters_are_refused(url):
    with pytest.raises(FetchError) as e:
        validate_url(url)
    assert e.value.reason == Reason.BAD_URL


def test_userinfo_is_refused_because_it_disguises_the_host():
    with pytest.raises(FetchError):
        validate_url("https://example.org@evil.example/")


def test_default_ports_and_query_are_preserved():
    target = validate_url("https://example.org/a?b=c")
    assert (target.port, target.path) == (443, "/a?b=c")
    assert validate_url("http://example.org").port == 80


# ------------------------------------------------------------------ addresses

@pytest.mark.parametrize("address", [
    "127.0.0.1", "127.9.9.9", "0.0.0.0",
    "10.0.0.5", "172.16.0.1", "192.168.1.10",
    "169.254.169.254",                 # cloud metadata
    "100.64.0.1",                      # carrier-grade NAT
    "224.0.0.1",                       # multicast
    "::1", "fe80::1", "fc00::1", "::",
    "::ffff:127.0.0.1",                # IPv4-mapped loopback
    "::ffff:10.0.0.1",                 # IPv4-mapped RFC1918
    "::127.0.0.1",                     # deprecated IPv4-compatible
    "2002:a00:1::1",                   # 6to4 wrapping 10.0.0.1
    "not-an-address",
])
def test_blocked_addresses(address):
    with pytest.raises(FetchError) as e:
        assert_address_allowed(address)
    assert e.value.reason == Reason.BLOCKED_ADDRESS


@pytest.mark.parametrize("address", ["93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946"])
def test_public_addresses_are_allowed(address):
    assert_address_allowed(address)


def test_a_host_resolving_to_both_public_and_private_is_hostile_not_salvageable():
    target = validate_url("https://split.example/")
    with pytest.raises(FetchError) as e:
        resolve_allowed(target, resolver_for({"split.example": ["93.184.216.34", "10.0.0.1"]}))
    assert e.value.reason == Reason.BLOCKED_ADDRESS


def test_unresolvable_host():
    target = validate_url("https://nowhere.example/")
    with pytest.raises(FetchError) as e:
        resolve_allowed(target, resolver_for({"nowhere.example": []}))
    assert e.value.reason == Reason.UNRESOLVABLE


# ------------------------------------------------------------------- fetching

def test_happy_path_returns_the_body():
    doc = fetch(
        "https://example.org/a",
        policy=POLICY,
        resolver=resolver_for({"example.org": ["93.184.216.34"]}),
        connector=connector_for({"example.org": RawResponse(200, HTML, b"<p>hi</p>")}),
    )
    assert doc.body == b"<p>hi</p>"
    assert doc.content_type == "text/html"


def test_connector_is_handed_the_validated_address_not_the_hostname():
    """Connect-time validation only means something if we connect to what we checked."""
    connector = connector_for({"example.org": RawResponse(200, HTML, b"ok")})
    fetch(
        "https://example.org/a",
        policy=POLICY,
        resolver=resolver_for({"example.org": ["93.184.216.34"]}),
        connector=connector,
    )
    assert connector.calls == [("example.org", "93.184.216.34", "https")]


def test_rebinding_a_redirect_target_is_caught_because_every_hop_revalidates():
    resolver = resolver_for({"example.org": ["93.184.216.34"], "inside.example": ["10.0.0.7"]})
    connector = connector_for({
        "example.org": RawResponse(302, {"Location": "https://inside.example/secrets"}, b""),
    })
    with pytest.raises(FetchError) as e:
        fetch("https://example.org/a", policy=POLICY, resolver=resolver, connector=connector)
    assert e.value.reason == Reason.BLOCKED_ADDRESS


def test_redirect_to_a_non_http_scheme_is_refused():
    connector = connector_for({
        "example.org": RawResponse(302, {"Location": "file:///etc/passwd"}, b""),
    })
    with pytest.raises(FetchError) as e:
        fetch(
            "https://example.org/a",
            policy=POLICY,
            resolver=resolver_for({"example.org": ["93.184.216.34"]}),
            connector=connector,
        )
    assert e.value.reason in (Reason.BAD_SCHEME, Reason.BAD_URL)


def test_relative_redirects_resolve_against_the_hop_actually_made():
    resolver = resolver_for({"example.org": ["93.184.216.34"]})
    responses = iter([
        RawResponse(302, {"Location": "/second"}, b""),
        RawResponse(200, HTML, b"second"),
    ])

    def connector(target, address, *, timeout):
        return next(responses)

    doc = fetch("https://example.org/first", policy=POLICY, resolver=resolver, connector=connector)
    assert doc.body == b"second"
    assert doc.final_url == "https://example.org:443/second"


def test_redirect_loop_is_bounded():
    resolver = resolver_for({"example.org": ["93.184.216.34"]})
    connector = connector_for({
        "example.org": RawResponse(302, {"Location": "https://example.org/again"}, b""),
    })
    with pytest.raises(FetchError) as e:
        fetch("https://example.org/a", policy=POLICY, resolver=resolver, connector=connector)
    assert e.value.reason == Reason.TOO_MANY_REDIRECTS


def test_redirect_without_a_location_is_refused():
    connector = connector_for({"example.org": RawResponse(302, {}, b"")})
    with pytest.raises(FetchError) as e:
        fetch(
            "https://example.org/a",
            policy=POLICY,
            resolver=resolver_for({"example.org": ["93.184.216.34"]}),
            connector=connector,
        )
    assert e.value.reason == Reason.BAD_REDIRECT


@pytest.mark.parametrize("content_type", [
    "application/pdf", "image/png", "application/octet-stream",
    "application/json", "text/csv", "",
])
def test_only_text_content_types_are_accepted(content_type):
    connector = connector_for({
        "example.org": RawResponse(200, {"Content-Type": content_type}, b"x"),
    })
    with pytest.raises(FetchError) as e:
        fetch(
            "https://example.org/a",
            policy=POLICY,
            resolver=resolver_for({"example.org": ["93.184.216.34"]}),
            connector=connector,
        )
    assert e.value.reason == Reason.BAD_CONTENT_TYPE


def test_oversized_bodies_are_refused():
    policy = FetchPolicy(max_bytes=10)
    connector = connector_for({"example.org": RawResponse(200, HTML, b"x" * 11)})
    with pytest.raises(FetchError) as e:
        fetch(
            "https://example.org/a",
            policy=policy,
            resolver=resolver_for({"example.org": ["93.184.216.34"]}),
            connector=connector,
        )
    assert e.value.reason == Reason.TOO_LARGE


def test_error_messages_never_name_the_blocked_destination():
    """Reasons go to operator logs; a destination in the text would leak caller data."""
    with pytest.raises(FetchError) as e:
        assert_address_allowed("169.254.169.254")
    assert "169.254" not in str(e.value)
