"""Worker entry point: ``ai-jobs-worker <profile> [profile arguments]``.

Runs inside a sandbox pod. It imports only the standard library and
``ai_jobs.envelope`` (which needs only ``ai_jobs.contracts``): no Kubernetes
client, no telemetry, nothing of the control plane. Input is the files the
executor mounted; output is one envelope as the last line of stdout.

Profile arguments come from the reviewed profile's container command, never from a
request. ``memory-recall`` takes the hearthmem URL that way.
"""

from __future__ import annotations

import http.client
import json
import re
import socket
import sys
from pathlib import Path
from urllib.parse import quote, urlsplit

from ai_jobs.contracts import ContractError
from ai_jobs.envelope import (
    RECALL_MAX_AUTHOR_CHARS,
    RECALL_MAX_CONTENT_CHARS,
    RECALL_MAX_CREATED_AT_CHARS,
    RECALL_MAX_ENTRIES,
    RECALL_MAX_QUERY_CHARS,
    RECALL_MAX_TAG_CHARS,
    RECALL_MAX_TAGS,
    RECALL_MAX_TOTAL_CONTENT_CHARS,
    Envelope,
    ErrorCode,
    Inspection,
    InspectionStatus,
    SharedTrust,
    validate_recall_data,
)

REQUEST_PATH = Path("/run/ai-jobs/request.json")
CREDENTIAL_PATH = Path("/run/ai-jobs/credential")

#: The profile's deadline is 15 s and the pod needs a moment to start, so the one call to
#: hearthmem gets less than that.
HEARTHMEM_TIMEOUT_SECONDS = 8
#: hearthmem returns whole entries; more than this from a store is not a recall result.
HEARTHMEM_RESPONSE_LIMIT_BYTES = 1 << 20
#: Stays under the profile's 64 KiB result limit with room for the envelope's own fields.
RESULT_LINE_BUDGET_BYTES = 60_000

_TOKEN = re.compile(r"[A-Za-z0-9_-]{16,256}")


def _noop(request: object) -> dict[str, object]:
    if request != {}:
        return _internal_error("noop")
    return Envelope.ok("noop", 1, {"ok": True}, "noop").to_dict()


def _internal_error(tool: str) -> dict[str, object]:
    return Envelope.failure(
        tool, 1, ErrorCode.INTERNAL_ERROR, Inspection(InspectionStatus.NOT_RUN, None)
    ).to_dict()


# ---- memory-recall ---------------------------------------------------------


def _recall_failure(code: ErrorCode) -> dict[str, object]:
    return Envelope.failure(
        "memory_recall",
        1,
        code,
        Inspection(InspectionStatus.NOT_RUN, None),
        trust=SharedTrust.HOUSEHOLD_SHARED,
    ).to_dict()


def _recall(request: object, url: str, credential_path: Path) -> dict[str, object]:
    """One ``GET /stores/-/entries`` with the read token in ``X-Store-Token`` only.

    Whatever hearthmem says on failure is dropped: every failure is a fixed envelope code.
    """
    query, limit = _recall_request(request)
    target = urlsplit(url)
    if (
        query is None
        or target.scheme not in ("http", "https")
        or not target.hostname
        or target.username is not None
        or target.password is not None
    ):
        return _recall_failure(ErrorCode.INTERNAL_ERROR)
    try:
        token = credential_path.read_text(encoding="ascii").strip()
    except (OSError, ValueError):
        return _recall_failure(ErrorCode.INTERNAL_ERROR)
    if not _TOKEN.fullmatch(token):
        return _recall_failure(ErrorCode.INTERNAL_ERROR)

    # "-" is a placeholder path segment: the server prefers the header, and a bare
    # /stores/entries would reach the describe route instead.
    path = f"/stores/-/entries?q={quote(query, safe='')}&limit={limit}"
    connection_class = http.client.HTTPSConnection if target.scheme == "https" else http.client.HTTPConnection
    connection = connection_class(target.hostname, target.port, timeout=HEARTHMEM_TIMEOUT_SECONDS)
    try:
        connection.request("GET", path, headers={"X-Store-Token": token, "Accept": "application/json"})
        response = connection.getresponse()
        if response.status != 200:  # redirects are not followed either
            return _recall_failure(ErrorCode.FETCH_FAILED)
        body = response.read(HEARTHMEM_RESPONSE_LIMIT_BYTES + 1)
    except (socket.timeout, TimeoutError):
        return _recall_failure(ErrorCode.DEADLINE_EXCEEDED)
    except (OSError, http.client.HTTPException):
        return _recall_failure(ErrorCode.FETCH_FAILED)
    finally:
        connection.close()
    if len(body) > HEARTHMEM_RESPONSE_LIMIT_BYTES:
        return _recall_failure(ErrorCode.RESPONSE_LIMIT_EXCEEDED)

    try:
        entries = _recall_entries(json.loads(body), limit)
        data = validate_recall_data({"entries": entries})
    except (ValueError, ContractError):
        return _recall_failure(ErrorCode.INTERNAL_ERROR)
    # Entries are dropped from the end, never cut mid-field, until the line fits the budget.
    while True:
        envelope = Envelope.ok_household("memory_recall", 1, data).to_dict()
        if len(_line(envelope)) <= RESULT_LINE_BUDGET_BYTES or not data["entries"]:
            return envelope
        data = {"entries": data["entries"][:-1]}


def _recall_request(request: object) -> tuple[str | None, int]:
    if not isinstance(request, dict) or set(request) != {"query", "limit"}:
        return None, 0
    query, limit = request["query"], request["limit"]
    if (
        not isinstance(query, str)
        or not query.strip()
        or len(query) > RECALL_MAX_QUERY_CHARS
        or isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= RECALL_MAX_ENTRIES
    ):
        return None, 0
    return query, limit


def _recall_entries(payload: object, limit: int) -> list[dict[str, object]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("entries"), list):
        raise ValueError("unexpected response shape")
    remaining = RECALL_MAX_TOTAL_CONTENT_CHARS
    entries: list[dict[str, object]] = []
    for row in payload["entries"][:limit]:
        if not isinstance(row, dict) or not isinstance(row.get("content"), str):
            raise ValueError("unexpected entry shape")
        content = row["content"].strip()[: min(RECALL_MAX_CONTENT_CHARS, remaining)].strip()
        if not content:
            continue
        remaining -= len(content)
        author = row.get("author")
        created_at = row.get("created_at")
        tags = row.get("tags")
        entries.append(
            {
                "content": content,
                "author": author.strip()[:RECALL_MAX_AUTHOR_CHARS] if isinstance(author, str) else "",
                "created_at": created_at.strip()[:RECALL_MAX_CREATED_AT_CHARS] if isinstance(created_at, str) else "",
                "tags": [
                    tag.strip()[:RECALL_MAX_TAG_CHARS]
                    for tag in (tags if isinstance(tags, list) else [])
                    if isinstance(tag, str) and tag.strip()
                ][:RECALL_MAX_TAGS],
            }
        )
        if remaining <= 0:
            break
    return entries


def _line(envelope: dict[str, object]) -> bytes:
    return json.dumps(envelope, separators=(",", ":")).encode("ascii") + b"\n"


# ---- entry point -----------------------------------------------------------


def _run(
    name: str, request: object, extra: list[str], credential_path: Path
) -> dict[str, object]:
    if name == "noop":
        return _noop(request)
    return _recall(request, extra[0], credential_path)


#: Profile name -> how many profile arguments it takes.
WORKERS: dict[str, int] = {"noop": 0, "memory-recall": 1}


def main(
    argv: list[str] | None = None,
    *,
    request_path: Path = REQUEST_PATH,
    credential_path: Path = CREDENTIAL_PATH,
) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args or args[0] not in WORKERS or len(args) != 1 + WORKERS[args[0]]:
        print(f"usage: ai-jobs-worker {{{'|'.join(sorted(WORKERS))}}} [profile arguments]", file=sys.stderr)
        return 2
    try:
        request = json.loads(request_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        request = None
    envelope = _run(args[0], request, args[1:], credential_path)
    sys.stdout.write(_line(envelope).decode("ascii"))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
