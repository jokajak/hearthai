"""Worker entry point: ``ai-jobs-worker <profile>``.

Runs inside a sandbox pod. It imports only the standard library and
``ai_jobs.envelope`` (which needs only ``ai_jobs.contracts``): no Kubernetes
client, no telemetry, nothing of the control plane. Input is the file the
executor mounted; output is one envelope as the last line of stdout.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Callable

from ai_jobs.envelope import Envelope, ErrorCode, Inspection, InspectionStatus

REQUEST_PATH = Path("/run/ai-jobs/request.json")


def _noop(request: object) -> dict[str, object]:
    if request != {}:
        return _internal_error("noop")
    return Envelope.ok("noop", 1, {"ok": True}, "noop").to_dict()


def _internal_error(tool: str) -> dict[str, object]:
    return Envelope.failure(
        tool, 1, ErrorCode.INTERNAL_ERROR, Inspection(InspectionStatus.NOT_RUN, None)
    ).to_dict()


WORKERS: dict[str, Callable[[object], dict[str, object]]] = {"noop": _noop}


def main(argv: list[str] | None = None, *, request_path: Path = REQUEST_PATH) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1 or args[0] not in WORKERS:
        print(f"usage: ai-jobs-worker {{{'|'.join(sorted(WORKERS))}}}", file=sys.stderr)
        return 2
    try:
        request = json.loads(request_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        request = None
    envelope = WORKERS[args[0]](request)
    sys.stdout.write(json.dumps(envelope, separators=(",", ":")) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
