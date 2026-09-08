"""Wiring checks that would otherwise only fail in the container."""

import json
from importlib.resources import files

import hearthfetch.__main__ as entry


def test_the_openapi_document_ships_inside_the_package():
    """A path relative to __file__ resolves in the source tree and breaks once
    installed, which would start the container and then 404 the document
    OpenWebUI registers against."""
    spec = entry._openapi()
    assert set(spec["paths"]) == {"/v1/tools/search_web", "/v1/tools/fetch_result"}
    assert (files("hearthfetch") / "openapi.json").is_file()


def test_the_resolver_returns_every_address_so_a_mixed_answer_can_be_rejected():
    addresses = entry._resolver("localhost", 80)
    assert isinstance(addresses, list) and addresses
    assert all(isinstance(a, str) for a in addresses)


def test_required_settings_exit_rather_than_starting_half_configured(monkeypatch):
    for key in ("TOOL_TOKEN", "HANDLE_KEY", "SEARXNG_URL", "LITELLM_API_KEY"):
        monkeypatch.delenv(f"HEARTHFETCH_{key}", raising=False)
    import pytest

    with pytest.raises(SystemExit) as caught:
        entry._required("TOOL_TOKEN")
    assert caught.value.code == 2
