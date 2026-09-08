import json
import re

import pytest

from hearthfetch.handles import (
    HandleError,
    mint,
    new_key,
    new_search_id,
    open_handle,
)

NOW = 1_000_000.0
URL = "https://example.org/article?id=7"


def test_round_trip():
    key, sid = new_key(), new_search_id()
    token = mint(URL, key=key, search_id=sid, now=NOW)
    assert open_handle(token, key=key, now=NOW).url == URL


def test_handle_is_opaque_the_url_is_not_recoverable_without_the_key():
    """Signing alone is not enough: the model must not be able to read the URL."""
    token = mint(URL, key=new_key(), search_id=new_search_id(), now=NOW)
    assert "example.org" not in token
    # Nor after decoding any base64 segment of it.
    import base64

    for part in token.split("."):
        try:
            raw = base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))
        except Exception:
            continue
        assert b"example.org" not in raw


def test_forged_handle_is_rejected():
    token = mint(URL, key=new_key(), search_id=new_search_id(), now=NOW)
    with pytest.raises(HandleError):
        open_handle(token, key=new_key(), now=NOW)


def test_the_model_cannot_mint_a_handle_for_a_destination_of_its_choosing():
    """The whole point: no key, no handle, so no attacker-chosen fetch."""
    import base64

    payload = base64.urlsafe_b64encode(
        json.dumps({"u": "https://evil.example/collect", "s": "x", "e": 2_000_000}).encode()
    ).decode().rstrip("=")
    for attempt in (f"hf1.AAAAAAAAAAAAAAAA.{payload}", f"hf1..{payload}", payload):
        with pytest.raises(HandleError):
            open_handle(attempt, key=new_key(), now=NOW)


@pytest.mark.parametrize("mangle", [
    lambda t: t[:-1] + ("A" if t[-1] != "A" else "B"),
    lambda t: t[:-4],
    lambda t: t.replace("hf1", "hf2", 1),
    lambda t: t.split(".", 1)[1],
    lambda t: t + ".extra",
])
def test_tampering_is_rejected(mangle):
    key = new_key()
    token = mint(URL, key=key, search_id=new_search_id(), now=NOW)
    with pytest.raises(HandleError):
        open_handle(mangle(token), key=key, now=NOW)


def test_expiry_is_enforced():
    key = new_key()
    token = mint(URL, key=key, search_id=new_search_id(), now=NOW, ttl_seconds=60)
    assert open_handle(token, key=key, now=NOW + 59).url == URL
    with pytest.raises(HandleError):
        open_handle(token, key=key, now=NOW + 60)


def test_handle_is_bound_to_its_issuing_search_call():
    key = new_key()
    token = mint(URL, key=key, search_id="search-a", now=NOW)
    assert open_handle(token, key=key, now=NOW, search_id="search-a").url == URL
    with pytest.raises(HandleError):
        open_handle(token, key=key, now=NOW, search_id="search-b")


def test_every_failure_looks_the_same_so_it_is_not_an_oracle():
    key = new_key()
    expired = mint(URL, key=key, search_id="a", now=NOW, ttl_seconds=1)
    forged = mint(URL, key=new_key(), search_id="a", now=NOW)
    messages = set()
    for token, when in ((expired, NOW + 10), (forged, NOW), ("garbage", NOW)):
        with pytest.raises(HandleError) as caught:
            open_handle(token, key=key, now=when)
        messages.add(str(caught.value))
    assert messages == {"invalid handle"}


def test_nonce_is_fresh_so_identical_urls_do_not_produce_identical_handles():
    key, sid = new_key(), new_search_id()
    first = mint(URL, key=key, search_id=sid, now=NOW)
    second = mint(URL, key=key, search_id=sid, now=NOW)
    assert first != second


def test_short_key_is_refused():
    with pytest.raises(HandleError):
        mint(URL, key=b"tooshort", search_id="a", now=NOW)
