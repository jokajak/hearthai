"""Search, brokered through self-hosted SearXNG.

Self-hosted rather than a commercial SERP key: no account, no per-query cost,
no spend cap to police, no credential to rotate, and the household's queries
never reach a vendor's logs.

It changes nothing about trust. An attacker who ranks for a query controls the
title and snippet SearXNG returns, so both pass the same output scrub as page
content. Owning the aggregator removes a vendor relationship, not a threat.

The model receives handles, never URLs — see `handles.py`.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable, Protocol, Sequence

from hearthfetch.contracts import SearchResult
from hearthfetch.fetch import FetchError, validate_url
from hearthfetch.handles import mint, new_search_id
from hearthfetch.scrub_out import DistillationDropped
from hearthfetch.scrub_out import scrub as scrub_out

MAX_TITLE_CHARS = 300
MAX_SNIPPET_CHARS = 600


@dataclass(frozen=True, slots=True)
class RawResult:
    url: str
    title: str
    snippet: str


class SearchProvider(Protocol):
    def search(self, query: str, count: int) -> Sequence[RawResult]: ...


# Injected so tests never open a socket.
Transport = Callable[[str, float], bytes]


def _urlopen(url: str, timeout: float) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "hearthfetch"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


@dataclass(frozen=True, slots=True)
class SearxngProvider:
    """SearXNG's JSON API.

    ⚠️ The JSON format is disabled by default and an unset format returns 403,
    not an error that explains itself. The deployment must set
    `search.formats: [html, json]`.
    """

    base_url: str
    timeout_seconds: float = 10.0
    transport: Transport = _urlopen

    def search(self, query: str, count: int) -> Sequence[RawResult]:
        params = urllib.parse.urlencode({"q": query, "format": "json"})
        url = f"{self.base_url.rstrip('/')}/search?{params}"
        try:
            payload = json.loads(self.transport(url, self.timeout_seconds))
        except urllib.error.HTTPError as exc:
            # Status only. 403 here almost always means the JSON format is not
            # enabled rather than anything about the query.
            raise SearchUnavailable(f"search returned status {exc.code}") from None
        except Exception as exc:
            raise SearchUnavailable(f"search unreachable: {type(exc).__name__}") from None

        if not isinstance(payload, dict):
            raise SearchUnavailable("search returned an unexpected shape")
        results = payload.get("results")
        if not isinstance(results, list):
            raise SearchUnavailable("search returned an unexpected shape")

        out: list[RawResult] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            url_value = item.get("url")
            title = item.get("title") or ""
            # SearXNG calls the excerpt `content`, not `snippet`.
            snippet = item.get("content") or ""
            if not isinstance(url_value, str) or not isinstance(title, str):
                continue
            if not isinstance(snippet, str):
                snippet = ""
            out.append(RawResult(url=url_value, title=title, snippet=snippet))
            if len(out) >= count:
                break
        return out


class SearchUnavailable(Exception):
    """The provider could not be reached or answered unusably."""


def _scrub_field(text: str, limit: int) -> str | None:
    """Scrub attacker-authored text, or None if it cannot be made safe."""
    if not text.strip():
        return ""
    try:
        return scrub_out(text)[:limit]
    except DistillationDropped:
        return None


def search_web(
    query: str,
    count: int,
    *,
    provider: SearchProvider,
    handle_key: bytes,
    now: float,
    handle_ttl_seconds: int = 900,
) -> list[SearchResult]:
    """Search, and return handles rather than URLs.

    Returns an empty list when the provider fails, matching how a search result
    set degrades everywhere else: a missing answer, not a failed conversation.
    """
    try:
        raw = provider.search(query, count)
    except SearchUnavailable:
        return []

    search_id = new_search_id()
    results: list[SearchResult] = []
    for item in raw:
        try:
            validate_url(item.url)
        except FetchError:
            # A result we could never fetch is not worth a handle.
            continue
        title = _scrub_field(item.title, MAX_TITLE_CHARS)
        if title is None or not title:
            # The title is how a person recognises a result, so a result whose
            # title cannot be made safe is dropped outright.
            continue
        snippet = _scrub_field(item.snippet, MAX_SNIPPET_CHARS)
        results.append(
            SearchResult(
                handle=mint(
                    item.url,
                    key=handle_key,
                    search_id=search_id,
                    now=now,
                    ttl_seconds=handle_ttl_seconds,
                ),
                title=title,
                # A snippet that cannot be made safe is emptied rather than
                # costing the whole result: the handle and title still work.
                snippet=snippet or "",
            )
        )
    return results
