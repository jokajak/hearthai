# Portable Cross-Host Memory Implementation Plan

> **ARCHIVED — DO NOT EXECUTE.** The authoritative [`HearthAI roadmap`](../../ROADMAP.md) defines 0.2 as shareable-memory integration, not personal-memory replacement. Long-term personal-memory ownership is unresolved. This plan is retained only as historical implementation research.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Python 3.14 HearthAI personal-memory API that lets Claude Code and OpenWebUI explicitly save and recall the same records through one OpenAPI contract.

**Architecture:** Extend the existing standard-library Python service in place. Extract its Git transaction mechanics, add revocable host-key identity and a backend-neutral personal-memory store, then expose the approved `/v1/memories` contract without changing the existing shared-store API. OpenWebUI consumes the service OpenAPI document directly; Claude Code uses a script-free Agent Skill and standard `curl`.

**Tech Stack:** Python 3.14, Python standard library at runtime, pytest for tests, OpenAPI 3.1 JSON, Git-backed Markdown/frontmatter persistence, Docker, Helm, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-08-27-portable-cross-host-memory-design.md`

## Global Constraints

- Python 3.14 is the minimum supported and container runtime version.
- The service remains standard-library-only at runtime; pytest is development-only.
- Existing `shared-memory` skill behavior and `/stores` HTTP behavior remain intact.
- OpenAPI is the canonical model-facing contract.
- The first model-facing operations are `health`, `saveMemory`, `recallMemories`, and `listMemories`.
- Writes occur only after explicit user instruction.
- API keys are per-host, independently revocable, shown once, and stored only as hashes.
- Principal ID and source host are derived from the key, never trusted from request JSON.
- `MemoryRecord` contains exactly `id`, `content`, `created_at`, and `source_host`.
- Host-facing schemas never expose Git, filesystem, graph, workflow, or Python implementation details.
- No MCP, LiteLLM middleware, Neo4j, n8n, embeddings, automatic extraction, OIDC, or end-to-end encryption.
- HTTPS remains mandatory outside loopback; browser CORS is an explicit allowlist, never `*`.
- Keep the current one-replica, `Recreate`, `ReadWriteOnce`, non-root deployment invariants.

## Planned File Structure

### New files

| File | Responsibility |
|---|---|
| `service/pyproject.toml` | Python 3.14 package metadata and pytest development dependency. |
| `service/.python-version` | Local interpreter pin: `3.14`. |
| `service/hearthmem/repository.py` | One Git repository, one writer lock, atomic file replacement, commit boundary. |
| `service/hearthmem/search.py` | Shared term-overlap ranking used by shared and personal memory. |
| `service/hearthmem/auth.py` | Host-key issuance, hashing, authentication, and revocation. |
| `service/hearthmem/admin.py` | Operator-only local CLI for issuing and revoking host keys. |
| `service/hearthmem/personal.py` | `MemoryRecord`, storage protocol, and Git-backed personal-memory adapter. |
| `service/hearthmem/core.py` | Authentication-derived personal-memory use cases independent of HTTP. |
| `service/hearthmem/openapi.json` | Canonical OpenAPI 3.1 contract served by the service. |
| `service/tests/test_repository.py` | Git transaction and atomic-write behavior. |
| `service/tests/test_auth.py` | Key secrecy, identity mapping, and revocation behavior. |
| `service/tests/test_personal.py` | Backend-neutral personal-memory conformance behavior. |
| `service/tests/test_core.py` | Derived identity and use-case error behavior. |
| `skills/personal-memory/SKILL.md` | Script-free Agent Skill for explicit personal save and recall. |

### Modified files

| File | Responsibility after change |
|---|---|
| `service/hearthmem/store.py` | Existing shared-store domain behavior using shared Git/search primitives. |
| `service/hearthmem/server.py` | Legacy `/stores`, new `/v1/memories`, OpenAPI serving, CORS, error envelopes, telemetry. |
| `service/tests/test_store.py` | Existing behavior plus refactored repository fixture. |
| `service/tests/test_server.py` | Legacy regression tests plus personal API, CORS, schema, auth, and failure-honesty tests. |
| `service/Dockerfile` | Python 3.14 runtime and service version build argument. |
| `.github/workflows/ci.yaml` | Python 3.14 tests and old-plus-new container smoke paths. |
| `.github/workflows/release.yaml` | Pass release version into the container build. |
| `deploy/charts/hearthmem/values.yaml` | Optional exact OpenWebUI CORS origin. |
| `deploy/charts/hearthmem/templates/deployment.yaml` | Service version and optional CORS environment wiring. |
| `service/README.md` | Python 3.14, key administration, personal API, OpenWebUI configuration. |
| `README.md` | Current built surface: shared and cross-host personal memory. |
| `docs/ARCHITECTURE.md` | Mark first-increment components as implemented after acceptance passes. |

---

### Task 1: Pin the Service and Delivery Pipeline to Python 3.14

**Files:**
- Create: `service/pyproject.toml`
- Create: `service/.python-version`
- Modify: `service/Dockerfile:1`
- Modify: `.github/workflows/ci.yaml:13-30`
- Modify: `service/README.md:7-14`

**Interfaces:**
- Consumes: existing `python -m hearthmem.server` and pytest layout.
- Produces: a documented Python `>=3.14` package and a single Python 3.14 CI target used by every later task.

- [ ] **Step 1: Add the Python 3.14 package contract**

Create `service/pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=75"]
build-backend = "setuptools.build_meta"

[project]
name = "hearthmem"
version = "0.2.0"
description = "Self-hosted shared and personal memory for AI hosts"
requires-python = ">=3.14"
dependencies = []

[project.optional-dependencies]
test = ["pytest>=8.4,<9"]

[tool.setuptools.package-data]
hearthmem = ["openapi.json"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

Create `service/.python-version` with exactly:

```text
3.14
```

- [ ] **Step 2: Verify the old CI range is still present before changing it**

Run:

```bash
python3 -c 'from pathlib import Path; text=Path(".github/workflows/ci.yaml").read_text(); assert "3.11" in text and "3.13" in text'
```

Expected: exit 0, proving this task actually changes the supported interpreter range.

- [ ] **Step 3: Move the runtime and CI to Python 3.14**

Change `service/Dockerfile`:

```dockerfile
FROM python:3.14-slim
```

Replace the test matrix in `.github/workflows/ci.yaml`:

```yaml
strategy:
  fail-fast: false
  matrix:
    python: ["3.14"]
```

Replace the install step with:

```yaml
- name: Install test dependencies
  working-directory: service
  run: python -m pip install -e '.[test]'
```

Update `service/README.md` to say `Python 3.14+, standard library only at runtime`.

- [ ] **Step 4: Run the full existing suite on Python 3.14**

Run:

```bash
python3.14 -m pip install -e './service[test]'
python3.14 -m pytest service/tests -q
```

Expected: all existing tests pass unchanged.

- [ ] **Step 5: Build and smoke the Python 3.14 container**

Run:

```bash
docker build -t hearthmem:py314 service
docker run --rm --name hearthmem-py314 -d -p 18765:8765 hearthmem:py314
docker exec hearthmem-py314 python3 --version
docker stop hearthmem-py314
```

Expected: `Python 3.14.x`; container exits cleanly after `docker stop`.

- [ ] **Step 6: Commit**

```bash
git add service/pyproject.toml service/.python-version service/Dockerfile service/README.md .github/workflows/ci.yaml
git commit -m "build: require Python 3.14"
```

---

### Task 2: Extract One Atomic Git Repository Boundary

**Files:**
- Create: `service/hearthmem/repository.py`
- Create: `service/hearthmem/search.py`
- Create: `service/tests/test_repository.py`
- Modify: `service/hearthmem/store.py:14-172`
- Modify: `service/hearthmem/server.py:119-121`
- Modify: `service/tests/test_store.py:13-15`

**Interfaces:**
- Consumes: current Git-per-write behavior and term-overlap search.
- Produces:
  - `GitRepository(root: Path)`
  - `GitRepository.transaction(message: str)` context manager
  - `GitRepository.write_text_atomic(path: Path, text: str) -> None`
  - `rank_by_term_overlap(rows, query, limit, searchable_text) -> list[dict]`
  - `MemoryStore(repository: GitRepository)`

- [ ] **Step 1: Write failing repository tests**

Create `service/tests/test_repository.py`:

```python
import subprocess
from pathlib import Path

from hearthmem.repository import GitRepository


def test_transaction_commits_atomic_write(tmp_path: Path) -> None:
    repo = GitRepository(tmp_path / "data")
    target = repo.root / "example.md"

    with repo.transaction("write example"):
        repo.write_text_atomic(target, "complete\n")

    assert target.read_text() == "complete\n"
    log = subprocess.run(
        ["git", "-C", str(repo.root), "log", "--format=%s"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert log == ["write example"]
    assert not list(repo.root.rglob("*.tmp"))


def test_transaction_does_not_commit_after_exception(tmp_path: Path) -> None:
    repo = GitRepository(tmp_path / "data")
    target = repo.root / "example.md"

    try:
        with repo.transaction("must not commit"):
            repo.write_text_atomic(target, "written before failure\n")
            raise RuntimeError("injected failure")
    except RuntimeError:
        pass

    log = subprocess.run(
        ["git", "-C", str(repo.root), "log", "--oneline"],
        capture_output=True,
        text=True,
    )
    assert log.returncode != 0 or not log.stdout.strip()
    assert not target.exists()
```

- [ ] **Step 2: Run the tests to verify the new boundary is missing**

Run:

```bash
python3.14 -m pytest service/tests/test_repository.py -q
```

Expected: collection fails with `ModuleNotFoundError: No module named 'hearthmem.repository'`.

- [ ] **Step 3: Implement the repository boundary**

Create `service/hearthmem/repository.py` with this public shape:

```python
from __future__ import annotations

import os
import subprocess
import threading
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4


class GitRepository:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self._git_init()

    @contextmanager
    def transaction(self, message: str):
        with self.lock:
            try:
                yield
                self._commit(message)
            except Exception:
                self._rollback()
                raise

    def write_text_atomic(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)

    def _rollback(self) -> None:
        head = subprocess.run(
            ["git", "-C", str(self.root), "rev-parse", "--verify", "HEAD"],
            capture_output=True,
        )
        if head.returncode == 0:
            self._git("reset", "--hard", "-q", "HEAD")
        self._git("clean", "-f", "-d", "-q")
```

Move the existing `_git_init`, `_git`, and `_commit` logic from `MemoryStore` into `GitRepository` unchanged except for `self.root` references. Implement `_rollback()` exactly as above so an exception cannot leave a visible uncommitted record.

- [ ] **Step 4: Extract shared term-overlap ranking**

Create `service/hearthmem/search.py`:

```python
from __future__ import annotations

import re
from collections.abc import Callable, Iterable

_SAFE = re.compile(r"[^a-z0-9]+")


def tokenize(text: str) -> set[str]:
    return {token for token in _SAFE.sub(" ", text.lower()).split() if len(token) > 1}


def rank_by_term_overlap(
    rows: Iterable[dict],
    query: str,
    limit: int,
    searchable_text: Callable[[dict], str],
) -> list[dict]:
    ordered = list(rows)
    wanted = tokenize(query or "")
    if not wanted:
        return ordered[:limit]
    scored = []
    for row in ordered:
        overlap = len(wanted & tokenize(searchable_text(row)))
        if overlap:
            scored.append((overlap, row.get("created_at", ""), row))
    scored.sort(key=lambda item: (-item[0], item[1]), reverse=False)
    return [row for _, _, row in scored[:limit]]
```

Add a regression test with two equal-overlap rows and preserve the current oldest-`created_at`-first tie order encoded by `(-overlap, created_at)`.

- [ ] **Step 5: Migrate every existing caller**

Change `MemoryStore.__init__` to accept `GitRepository`, use `repository.root / "stores"`, replace `self._lock` blocks with `repository.transaction(...)`, replace direct writes with `write_text_atomic`, and call `rank_by_term_overlap` from `search()`.

Update fixtures:

```python
@pytest.fixture
def store(tmp_path):
    return MemoryStore(GitRepository(tmp_path / "data"))
```

Update `build_server()` to construct one `GitRepository` and pass it to `MemoryStore`.

- [ ] **Step 6: Verify repository and legacy shared-memory behavior**

Run:

```bash
python3.14 -m pytest service/tests/test_repository.py service/tests/test_store.py service/tests/test_server.py -q
```

Expected: all repository tests and all pre-existing shared-memory tests pass.

- [ ] **Step 7: Commit**

```bash
git add service/hearthmem/repository.py service/hearthmem/search.py service/hearthmem/store.py service/hearthmem/server.py service/tests/test_repository.py service/tests/test_store.py
git commit -m "refactor: isolate Git storage transactions"
```

---

### Task 3: Add Revocable Per-Host Identity

**Files:**
- Create: `service/hearthmem/auth.py`
- Create: `service/hearthmem/admin.py`
- Create: `service/tests/test_auth.py`

**Interfaces:**
- Consumes: `GitRepository`, `frontmatter.dumps`, and `frontmatter.loads`.
- Produces:
  - `HostIdentity(key_id, principal_id, host_id)`
  - `HostKeyRegistry.issue(principal_id, host_id) -> tuple[str, HostIdentity]`
  - `HostKeyRegistry.authenticate(secret) -> HostIdentity`
  - `HostKeyRegistry.revoke(key_id) -> HostIdentity`
  - `InvalidApiKey`
  - CLI: `python -m hearthmem.admin issue|revoke|list`

- [ ] **Step 1: Write failing key-lifecycle tests**

Create `service/tests/test_auth.py`:

```python
from pathlib import Path

import pytest

from hearthmem.auth import HostKeyRegistry, InvalidApiKey
from hearthmem.repository import GitRepository


def test_issue_authenticate_and_revoke_without_persisting_secret(tmp_path: Path) -> None:
    repo = GitRepository(tmp_path / "data")
    keys = HostKeyRegistry(repo)

    secret, issued = keys.issue("josh", "claude-code")
    assert secret.startswith("hkm_")
    assert keys.authenticate(secret) == issued
    assert secret not in "".join(
        path.read_text(errors="ignore") for path in repo.root.rglob("*") if path.is_file()
    )

    revoked = keys.revoke(issued.key_id)
    assert revoked == issued
    with pytest.raises(InvalidApiKey):
        keys.authenticate(secret)


def test_two_hosts_resolve_to_one_principal(tmp_path: Path) -> None:
    keys = HostKeyRegistry(GitRepository(tmp_path / "data"))
    claude_secret, claude = keys.issue("josh", "claude-code")
    webui_secret, webui = keys.issue("josh", "openwebui")

    assert keys.authenticate(claude_secret).principal_id == "josh"
    assert keys.authenticate(webui_secret).principal_id == "josh"
    assert claude.host_id != webui.host_id
    assert claude.key_id != webui.key_id
```

- [ ] **Step 2: Run tests to verify auth does not exist**

Run:

```bash
python3.14 -m pytest service/tests/test_auth.py -q
```

Expected: collection fails because `hearthmem.auth` does not exist.

- [ ] **Step 3: Implement the host-key registry**

Use immutable identity records:

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class HostIdentity:
    key_id: str
    principal_id: str
    host_id: str


class InvalidApiKey(PermissionError):
    pass
```

`issue()` must:

1. validate non-empty `principal_id` and `host_id`;
2. generate `secret = "hkm_" + secrets.token_urlsafe(32)`;
3. compute SHA-256 and derive `key_id = digest[:16]`;
4. atomically write `auth/host-keys/<key_id>.md` with `key_hash`, `principal_id`, `host_id`, and `created_at`;
5. commit without putting the secret in the commit message;
6. return the secret once.

`authenticate()` must derive `key_id`, load that one record, use `hmac.compare_digest`, and reject missing, malformed, or revoked records with the same `InvalidApiKey`.

`revoke()` must add `revoked_at`, atomically replace the record, and commit `revoke host key <key_id>`.

- [ ] **Step 4: Add the local administration CLI**

Create `service/hearthmem/admin.py` with exact commands:

```text
python -m hearthmem.admin --root /data issue --principal josh --host claude-code
python -m hearthmem.admin --root /data revoke 0123456789abcdef
python -m hearthmem.admin --root /data list
```

`issue` prints JSON containing `key_id`, `principal_id`, `host_id`, and the one-time `secret`. `list` never prints secrets or hashes. `revoke` prints the revoked non-secret identity.

- [ ] **Step 5: Add CLI behavior tests**

Use `subprocess.run([sys.executable, "-m", "hearthmem.admin", ...])` against a temporary root. Assert issue JSON contains a secret, list JSON does not, revoke exits 0, and the issued secret fails authentication afterward.

- [ ] **Step 6: Run focused and full tests**

Run:

```bash
python3.14 -m pytest service/tests/test_auth.py -q
python3.14 -m pytest service/tests -q
```

Expected: all tests pass; legacy store behavior is unchanged.

- [ ] **Step 7: Commit**

```bash
git add service/hearthmem/auth.py service/hearthmem/admin.py service/tests/test_auth.py
git commit -m "feat: add revocable host identities"
```

---

### Task 4: Implement the Personal-Memory Storage Port

**Files:**
- Create: `service/hearthmem/personal.py`
- Create: `service/tests/test_personal.py`

**Interfaces:**
- Consumes: `GitRepository`, frontmatter, and `rank_by_term_overlap`.
- Produces:
  - `MemoryRecord(id, content, created_at, source_host)`
  - `SaveResult(memory, created)`
  - `PersonalMemoryPort` protocol
  - `GitPersonalMemoryStore.save(principal_id, content, source_host) -> SaveResult`
  - `GitPersonalMemoryStore.recall(principal_id, query, limit) -> list[MemoryRecord]`
  - `GitPersonalMemoryStore.list(principal_id, limit) -> list[MemoryRecord]`

- [ ] **Step 1: Write the backend-conformance tests first**

Create `service/tests/test_personal.py` with fixed clocks so ordering is deterministic:

```python
from collections import deque
from pathlib import Path

import pytest

from hearthmem.personal import GitPersonalMemoryStore
from hearthmem.repository import GitRepository
from hearthmem.store import InvalidRequest


def make_store(tmp_path: Path) -> GitPersonalMemoryStore:
    times = deque(["2026-08-27T10:00:00+00:00", "2026-08-27T11:00:00+00:00"])
    return GitPersonalMemoryStore(
        GitRepository(tmp_path / "data"),
        clock=lambda: times.popleft(),
    )


def test_save_duplicate_recall_and_list(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    first = store.save("josh", "OpenAPI is canonical", "claude-code")
    duplicate = store.save("josh", "  OpenAPI is canonical  ", "openwebui")
    second = store.save("josh", "Files are only the first adapter", "openwebui")

    assert first.created is True
    assert duplicate.created is False
    assert duplicate.memory == first.memory
    assert first.memory.source_host == "claude-code"
    assert [m.id for m in store.recall("josh", "OpenAPI", 10)] == [first.memory.id]
    assert [m.id for m in store.list("josh", 10)] == [second.memory.id, first.memory.id]


def test_principals_are_isolated(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.save("josh", "private marker", "claude-code")
    assert store.recall("other", "marker", 10) == []


def test_empty_content_and_invalid_limit_are_rejected(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    with pytest.raises(InvalidRequest):
        store.save("josh", "   ", "claude-code")
    with pytest.raises(InvalidRequest):
        store.list("josh", 0)
```

- [ ] **Step 2: Run tests to verify the personal adapter is missing**

Run:

```bash
python3.14 -m pytest service/tests/test_personal.py -q
```

Expected: collection fails because `hearthmem.personal` does not exist.

- [ ] **Step 3: Define the backend-neutral records and protocol**

Use frozen dataclasses and a protocol:

```python
from dataclasses import asdict, dataclass
from typing import Protocol


@dataclass(frozen=True)
class MemoryRecord:
    id: str
    content: str
    created_at: str
    source_host: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class SaveResult:
    memory: MemoryRecord
    created: bool


class PersonalMemoryPort(Protocol):
    def save(self, principal_id: str, content: str, source_host: str) -> SaveResult: ...
    def recall(self, principal_id: str, query: str, limit: int) -> list[MemoryRecord]: ...
    def list(self, principal_id: str, limit: int) -> list[MemoryRecord]: ...
```

- [ ] **Step 4: Implement the Git-backed adapter**

Store personal memories under:

```text
data/personal/<sha256-principal>/entries/YYYY-MM-DD-mem_<24-hex>.md
```

Frontmatter contains only `id`, `created_at`, `source_host`, and `content_sha256`; the body is content. Derive the opaque ID from SHA-256 of `principal_id + NUL + normalized content`, so the same text deduplicates within a principal without coupling IDs across principals.

All writes occur inside `GitRepository.transaction()` and use `write_text_atomic()`. `list()` sorts descending by `created_at`; `recall()` passes rows through `rank_by_term_overlap` using content only.

- [ ] **Step 5: Add restart and atomic-failure coverage**

Re-open `GitPersonalMemoryStore` on the same root and assert IDs and order are unchanged. Monkeypatch `write_text_atomic` to raise before replacement and assert no record is visible afterward and no Git commit was added.

- [ ] **Step 6: Run focused and full tests**

Run:

```bash
python3.14 -m pytest service/tests/test_personal.py -q
python3.14 -m pytest service/tests -q
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add service/hearthmem/personal.py service/tests/test_personal.py
git commit -m "feat: add personal memory storage port"
```

---

### Task 5: Add the Authentication-Derived Memory Core

**Files:**
- Create: `service/hearthmem/core.py`
- Create: `service/tests/test_core.py`

**Interfaces:**
- Consumes: `HostKeyRegistry`, `HostIdentity`, and `PersonalMemoryPort`.
- Produces:
  - `MemoryCore.save(secret, content) -> SaveResult`
  - `MemoryCore.recall(secret, query, limit) -> list[MemoryRecord]`
  - `MemoryCore.list(secret, limit) -> list[MemoryRecord]`

- [ ] **Step 1: Write failing core tests**

Create `service/tests/test_core.py`:

```python
from pathlib import Path

import pytest

from hearthmem.auth import HostKeyRegistry, InvalidApiKey
from hearthmem.core import MemoryCore
from hearthmem.personal import GitPersonalMemoryStore
from hearthmem.repository import GitRepository


def test_core_derives_principal_and_source_host_from_key(tmp_path: Path) -> None:
    repo = GitRepository(tmp_path / "data")
    keys = HostKeyRegistry(repo)
    memories = GitPersonalMemoryStore(repo)
    core = MemoryCore(keys, memories)
    claude_secret, _ = keys.issue("josh", "claude-code")
    webui_secret, _ = keys.issue("josh", "openwebui")

    saved = core.save(claude_secret, "cross-host marker")
    recalled = core.recall(webui_secret, "marker", 10)

    assert saved.memory.source_host == "claude-code"
    assert recalled == [saved.memory]


def test_revoking_one_host_does_not_break_the_other(tmp_path: Path) -> None:
    repo = GitRepository(tmp_path / "data")
    keys = HostKeyRegistry(repo)
    core = MemoryCore(keys, GitPersonalMemoryStore(repo))
    claude_secret, claude = keys.issue("josh", "claude-code")
    webui_secret, _ = keys.issue("josh", "openwebui")
    core.save(webui_secret, "still available")

    keys.revoke(claude.key_id)
    with pytest.raises(InvalidApiKey):
        core.list(claude_secret, 10)
    assert len(core.list(webui_secret, 10)) == 1
```

- [ ] **Step 2: Run tests to verify the core is missing**

Run:

```bash
python3.14 -m pytest service/tests/test_core.py -q
```

Expected: collection fails because `hearthmem.core` does not exist.

- [ ] **Step 3: Implement the thin use-case layer**

Create `MemoryCore` with no HTTP concepts:

```python
class MemoryCore:
    def __init__(self, keys: HostKeyRegistry, memories: PersonalMemoryPort):
        self.keys = keys
        self.memories = memories

    def save(self, secret: str, content: str) -> SaveResult:
        identity = self.keys.authenticate(secret)
        return self.memories.save(identity.principal_id, content, identity.host_id)

    def recall(self, secret: str, query: str, limit: int) -> list[MemoryRecord]:
        identity = self.keys.authenticate(secret)
        return self.memories.recall(identity.principal_id, query, limit)

    def list(self, secret: str, limit: int) -> list[MemoryRecord]:
        identity = self.keys.authenticate(secret)
        return self.memories.list(identity.principal_id, limit)
```

Do not accept `principal_id` or `source_host` from callers.

- [ ] **Step 4: Add cross-principal isolation coverage**

Issue a third key for `other`, save a unique marker, and prove Josh's two keys cannot retrieve it and the other key cannot retrieve Josh's records.

- [ ] **Step 5: Run focused and full tests**

Run:

```bash
python3.14 -m pytest service/tests/test_core.py -q
python3.14 -m pytest service/tests -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add service/hearthmem/core.py service/tests/test_core.py
git commit -m "feat: derive personal memory identity from host keys"
```

---

### Task 6: Serve the OpenAPI Personal-Memory API

**Files:**
- Create: `service/hearthmem/openapi.json`
- Modify: `service/hearthmem/server.py:24-121`
- Modify: `service/tests/test_server.py:15-96`

**Interfaces:**
- Consumes: `MemoryCore`, `HostKeyRegistry`, `GitPersonalMemoryStore`, existing `MemoryStore`.
- Produces:
  - `GET /openapi.json`
  - `GET /health`
  - `POST /v1/memories`
  - `GET /v1/memories/recall?q=&limit=`
  - `GET /v1/memories?limit=`
  - `OPTIONS` with exact-origin CORS
  - stable `{error: {code, message}}` envelopes
  - content-free structured operation events

- [ ] **Step 1: Extend the HTTP test helper and write failing personal API tests**

Change `call()` to accept additional headers. Add tests that:

```python
def test_personal_round_trip_across_two_host_keys(api):
    base, keys = api
    claude = {"Authorization": f"Bearer {keys['claude-code']}"}
    webui = {"Authorization": f"Bearer {keys['openwebui']}"}

    code, saved = call(
        base, "POST", "/v1/memories", {"content": "cross-host marker"}, claude
    )
    assert code == 201
    assert saved["created"] is True
    assert saved["memory"]["source_host"] == "claude-code"

    code, recalled = call(base, "GET", "/v1/memories/recall?q=marker", headers=webui)
    assert code == 200
    assert recalled["memories"] == [saved["memory"]]


def test_revoked_key_is_401_with_stable_error(api):
    base, keys = api
    keys["registry"].revoke(keys["claude_identity"].key_id)
    code, body = call(
        base,
        "GET",
        "/v1/memories",
        headers={"Authorization": f"Bearer {keys['claude-code']}"},
    )
    assert code == 401
    assert body["error"]["code"] == "invalid_api_key"
```

Add cases for `400 invalid_request`, empty recall as `200`, duplicate save as `200`, unauthenticated `/health`, authenticated memory routes, exact CORS origin, wildcard refusal, and legacy `/stores` regression.

- [ ] **Step 2: Run the new HTTP tests to verify routes are absent**

Run:

```bash
python3.14 -m pytest service/tests/test_server.py -q
```

Expected: new personal API tests fail with `404` while legacy tests pass.

- [ ] **Step 3: Write the canonical OpenAPI document**

Create `service/hearthmem/openapi.json` as valid OpenAPI 3.1 with:

- server-relative paths;
- bearer security on every `/v1/memories` operation;
- no security on `/health`;
- operation IDs `health`, `saveMemory`, `recallMemories`, `listMemories`;
- closed request objects (`additionalProperties: false`);
- `MemoryRecord`, `SaveMemoryResponse`, `MemoryListResponse`, and `ErrorResponse` schemas;
- tool descriptions that say `saveMemory` is only for explicit user save requests and failures must be reported honestly.

Add a test that loads the JSON, checks `openapi == "3.1.0"`, checks the four operation IDs, asserts bearer security only on memory routes, and asserts `MemoryRecord.required` is exactly `id`, `content`, `created_at`, `source_host`.

- [ ] **Step 4: Wire one repository into all services**

`build_server()` must create exactly one `GitRepository`, then construct:

```python
repository = GitRepository(root)
shared = MemoryStore(repository)
keys = HostKeyRegistry(repository)
personal = GitPersonalMemoryStore(repository)
core = MemoryCore(keys, personal)
```

Bind those dependencies onto the handler class. Preserve the existing `build_server(root, host, port)` call by extending it only with keyword-only `version`, `cors_origin`, and `event_sink` test seams; migrate all internal callers in the same change.

- [ ] **Step 5: Implement bearer auth, routes, and stable errors**

Parse only `Authorization: Bearer <secret>` for personal routes. Keep `X-Store-Token` only for legacy shared routes.

Map exceptions:

```python
except InvalidApiKey:
    self._send_error(401, "invalid_api_key", "The API key is missing, invalid, or revoked.")
except InvalidRequest as exc:
    self._send_error(400, "invalid_request", str(exc))
except OSError:
    self._send_error(503, "storage_unavailable", "The memory store is unavailable.")
```

Do not expose exception class names or raw internal error text for `500`/`503` responses. Keep a generic `500 internal_error` guard and write the internal exception only to stderr.

- [ ] **Step 6: Add health, schema serving, and exact-origin CORS**

`GET /health` returns:

```json
{"status":"ok","version":"dev"}
```

`GET /openapi.json` returns the checked-in document. `OPTIONS` and all responses include `Access-Control-Allow-Origin` only when the request `Origin` exactly equals configured `HEARTHMEM_CORS_ORIGIN`. Include `Authorization, Content-Type` in allowed headers. Never return `*`.

- [ ] **Step 7: Add content-free operation telemetry**

Inject an `event_sink` callable, defaulting to one-line JSON on stdout. Emit only:

```python
{
    "timestamp": timestamp,
    "operation": operation_id,
    "key_id": identity.key_id,
    "principal_id": identity.principal_id,
    "source_host": identity.host_id,
    "status": status,
    "latency_ms": latency_ms,
    "result_count": result_count,
    "created": created,
}
```

Exclude authorization headers, query text, request bodies, response bodies, and memory content. Test captured events against a forbidden-key set: `{"secret", "authorization", "query", "content", "memory", "memories"}`.

- [ ] **Step 8: Run API, schema, and regression tests**

Run:

```bash
python3.14 -m pytest service/tests/test_server.py -q
python3.14 -m pytest service/tests -q
```

Expected: all new API tests and all legacy tests pass.

- [ ] **Step 9: Run a direct API smoke test**

Start the service with a temporary root, issue two keys using the local CLI, save with one key, and recall with the other:

```bash
ROOT=$(mktemp -d)
python3.14 -m hearthmem.admin --root "$ROOT" issue --principal josh --host claude-code
python3.14 -m hearthmem.admin --root "$ROOT" issue --principal josh --host openwebui
HEARTHMEM_ROOT="$ROOT" HEARTHMEM_PORT=18765 python3.14 -m hearthmem.server
```

In a second terminal, use the emitted secrets with `curl` and verify the same record ID crosses hosts. Stop the service and remove only the temporary root created by this step.

- [ ] **Step 10: Commit**

```bash
git add service/hearthmem/openapi.json service/hearthmem/server.py service/tests/test_server.py
git commit -m "feat: expose authenticated personal memory API"
```

---

### Task 7: Package the Script-Free Personal-Memory Skill

**Files:**
- Create: `skills/personal-memory/SKILL.md`
- Modify: `README.md:146-180`
- Modify: `service/README.md:56-114`

**Interfaces:**
- Consumes: `HEARTHMEM_URL`, a user-provisioned curl config containing the bearer header, and the four OpenAPI operations.
- Produces: a standard Agent Skill with no `scripts/` directory and no client-library dependency.

- [ ] **Step 1: Create the skill with explicit narrow triggers**

Create `skills/personal-memory/SKILL.md`:

```markdown
---
name: personal-memory
description: Save personal memory only when the user explicitly says to remember or save something, and recall HearthAI memory when the user explicitly asks what it remembers or prior stored context is clearly required. Use for personal cross-host continuity. Do not use for sharing with other people.
---

# Personal memory

HearthAI is the user's explicit personal memory across AI hosts.

## Configuration

- `HEARTHMEM_URL` is the service base URL.
- `HEARTHMEM_CURL_CONFIG` is a mode-0600 curl config containing the host's `Authorization: Bearer ...` header.

Never print either credential or include it in a response.

## Save

Save only after an unambiguous user instruction such as “remember this.” Send exactly one self-contained fact to `POST /v1/memories`. Report the returned content and whether it was new or already present. If the request fails, report the failure; never say it was remembered.

## Recall

Use `GET /v1/memories/recall` with a specific query when the user asks what HearthAI remembers or the answer clearly depends on stored context. Use `GET /v1/memories` only when the user asks for recent memories. An empty result means HearthAI has no matching stored memory; it is not a service failure.

Use these command shapes:

```bash
curl --silent --show-error --fail-with-body \
  --config "$HEARTHMEM_CURL_CONFIG" \
  --header 'Content-Type: application/json' \
  --request POST \
  --json '{"content":"HearthAI uses OpenAPI."}' \
  "$HEARTHMEM_URL/v1/memories"

curl --silent --show-error --fail-with-body \
  --config "$HEARTHMEM_CURL_CONFIG" \
  --get --data-urlencode 'q=OpenAPI' --data-urlencode 'limit=10' \
  "$HEARTHMEM_URL/v1/memories/recall"
```

For a real save, replace only the example JSON string with the user's exact memory using valid JSON escaping.

## Limits

- No automatic capture.
- No sharing.
- No editing or deletion.
- No claim of semantic search; recall is term-based for now.
```

The skill contains those exact `curl` shapes and references only `$HEARTHMEM_URL` and `$HEARTHMEM_CURL_CONFIG`; no secret, machine-specific path, or backend concept appears in the file.

- [ ] **Step 2: Document secret-safe curl configuration**

In `service/README.md`, document creating a mode-0600 config outside the skill:

```bash
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/hearthmem"
mkdir -p "$CONFIG_DIR"
printf 'header = "Authorization: Bearer %s"\n' "$ONE_TIME_HOST_KEY" > "$CONFIG_DIR/claude-code.curl.conf"
chmod 600 "$CONFIG_DIR/claude-code.curl.conf"
export HEARTHMEM_CURL_CONFIG="$CONFIG_DIR/claude-code.curl.conf"
export HEARTHMEM_URL="https://memory.example.internal"
```

The skill's curl calls use `--config "$HEARTHMEM_CURL_CONFIG"`; the secret never appears literally in skill content or the agent-generated command.

- [ ] **Step 3: Add a packaging audit**

Run:

```bash
test -f skills/personal-memory/SKILL.md
test ! -d skills/personal-memory/scripts
python3.14 - <<'PY'
from pathlib import Path
text = Path('skills/personal-memory/SKILL.md').read_text()
for forbidden in ('X-Store-Token', '/data/', 'git commit', 'Neo4j', 'n8n'):
    assert forbidden not in text, forbidden
assert 'HEARTHMEM_CURL_CONFIG' in text
assert 'explicit' in text.lower()
PY
```

Expected: exit 0.

- [ ] **Step 4: Install and smoke the skill in Claude Code**

Install the skill under a temporary Claude Code skill directory or link the repository skill according to Claude Code's documented skill discovery. Start a clean session with `HEARTHMEM_URL` and `HEARTHMEM_CURL_CONFIG`, then:

1. ask it to remember a unique marker and verify `saveMemory` is called;
2. ask what HearthAI remembers about the marker and verify `recallMemories` is called;
3. make an unrelated request and verify no save occurs;
4. revoke the key and verify the skill reports authentication failure rather than success.

Record the marker IDs and observed results in the implementation PR or ISA verification, not in permanent tests.

- [ ] **Step 5: Update repository documentation**

Update the root README's built-surface table to list both `skills/shared-memory/` and `skills/personal-memory/`. State that personal memory is operator-readable in the first self-hosted increment and that OpenAPI—not a client script—is the portable contract.

Update `service/README.md` with personal endpoints, key issuance/revocation commands, record shape, and explicit trust boundary while retaining legacy shared-store documentation.

- [ ] **Step 6: Commit**

```bash
git add skills/personal-memory/SKILL.md README.md service/README.md
git commit -m "feat: add portable personal memory skill"
```

---

### Task 8: Wire Deployment, CI, and Bidirectional Acceptance

**Files:**
- Modify: `service/Dockerfile:11-30`
- Modify: `.github/workflows/ci.yaml:32-92`
- Modify: `.github/workflows/release.yaml:51-63`
- Modify: `deploy/charts/hearthmem/values.yaml:23-35`
- Modify: `deploy/charts/hearthmem/templates/deployment.yaml:34-45`
- Modify: `docs/ARCHITECTURE.md`

**Interfaces:**
- Consumes: Python 3.14 image, `HEARTHMEM_VERSION`, `HEARTHMEM_CORS_ORIGIN`, local admin CLI, personal API, and existing shared API.
- Produces: deployable versioned service, configurable OpenWebUI origin, CI container proof for both memory modes, and real OpenWebUI acceptance evidence.

- [ ] **Step 1: Add version and CORS container inputs**

Add to `service/Dockerfile`:

```dockerfile
ARG VERSION=dev
ENV HEARTHMEM_VERSION=$VERSION
```

Keep the existing root, host, port, non-root user, read-only-root compatibility, healthcheck, and entrypoint.

In `.github/workflows/release.yaml`, pass:

```yaml
build-args: |
  VERSION=${{ steps.version.outputs.semver }}
```

- [ ] **Step 2: Add the optional exact CORS origin to Helm**

Add to `values.yaml`:

```yaml
# Exact browser origin for an OpenWebUI user tool server, for example
# https://chat.example.internal. Empty disables cross-origin requests.
corsOrigin: ""
```

Add to the Deployment environment:

```yaml
- name: HEARTHMEM_VERSION
  value: {{ .Chart.AppVersion | quote }}
{{- if .Values.corsOrigin }}
- name: HEARTHMEM_CORS_ORIGIN
  value: {{ .Values.corsOrigin | quote }}
{{- end }}
```

- [ ] **Step 3: Extend Helm assertions**

Render with `--set corsOrigin=https://chat.example.internal` and assert the deployment contains that exact environment value and never `*`. Preserve existing single-writer and hardening assertions.

Run:

```bash
helm lint deploy/charts/hearthmem
helm template hm deploy/charts/hearthmem --set corsOrigin=https://chat.example.internal >/tmp/hearthmem.yaml
```

Expected: chart lints and renders with exact CORS origin.

- [ ] **Step 4: Extend the container smoke test without removing legacy coverage**

After the existing shared-store round trip, issue two host keys inside the container:

```bash
CLAUDE_JSON=$(docker exec hm python3 -m hearthmem.admin --root /data issue --principal josh --host claude-code)
WEBUI_JSON=$(docker exec hm python3 -m hearthmem.admin --root /data issue --principal josh --host openwebui)
CLAUDE_KEY=$(printf '%s' "$CLAUDE_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["secret"])')
WEBUI_KEY=$(printf '%s' "$WEBUI_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["secret"])')
```

Save with the Claude key, capture the record ID, recall with the OpenWebUI key, and compare IDs. Repeat in reverse. Revoke the Claude key and assert it returns `401` while the OpenWebUI key still returns `200`. Do not echo either key.

- [ ] **Step 5: Verify restart persistence for personal memory and revocation**

Restart the same container volume. Confirm both personal markers remain and the revoked Claude key is still rejected. Keep the existing shared-store restart assertion.

- [ ] **Step 6: Build, test, and render the complete artifact set**

Run:

```bash
python3.14 -m pytest service/tests -q
docker build --build-arg VERSION=plan-smoke -t hearthmem:plan-smoke service
helm lint deploy/charts/hearthmem
helm template hm deploy/charts/hearthmem --set corsOrigin=https://chat.example.internal >/tmp/hearthmem.yaml
```

Expected: all tests pass, image builds, chart lints, and rendered deployment contains `HEARTHMEM_VERSION=0.1.0` plus the exact CORS origin.

- [ ] **Step 7: Perform the real OpenWebUI acceptance**

Deploy the image to the self-hosted environment, issue an `openwebui` key, and configure HearthAI as an OpenWebUI user tool server using the served `/openapi.json` and bearer key. Run:

1. Claude Code save → OpenWebUI recall with the same record ID and content.
2. OpenWebUI save → Claude Code recall with the same record ID and content.
3. Revoke one host key → that host reports authentication failure; the other still recalls both records.
4. Stop the service → both hosts report service failure and neither claims a save succeeded.

Capture the four outcomes as ISA/PR verification evidence. Do not commit the keys or memory contents.

- [ ] **Step 8: Update the architecture status**

In `docs/ARCHITECTURE.md`, preserve the boundary diagram and mark continuity as implemented only after Step 7 passes. Keep graph storage and workflow engine nodes deferred.

- [ ] **Step 9: Commit**

```bash
git add service/Dockerfile .github/workflows/ci.yaml .github/workflows/release.yaml deploy/charts/hearthmem/values.yaml deploy/charts/hearthmem/templates/deployment.yaml docs/ARCHITECTURE.md
git commit -m "deploy: verify cross-host personal memory"
```

---

## Final Verification Gate

After all eight tasks:

- [ ] Run `python3.14 -m pytest service/tests -q`; expected: all tests pass.
- [ ] Build `hearthmem:plan-smoke` and run `docker run --rm --entrypoint python3 hearthmem:plan-smoke --version`; expected: Python 3.14.x.
- [ ] Run the existing shared-memory CLI against the built container; expected: create, share, recall, and list still work.
- [ ] Run personal API bidirectional save/recall with two keys; expected: identical record IDs cross hosts.
- [ ] Revoke one key; expected: revoked host gets `401 invalid_api_key`, other host remains functional.
- [ ] Inspect `service/hearthmem/openapi.json`; expected: only approved fields and operation IDs.
- [ ] Inspect structured telemetry; expected: no secret, query, request content, or response content.
- [ ] Run `helm lint` and render default, CORS-enabled, existing-claim, persistence-disabled, and ingress-enabled paths.
- [ ] Run real Claude Code and OpenWebUI acceptance; expected: all four live scenarios in Task 8 pass.
- [ ] Confirm `skills/personal-memory/` contains no scripts or third-party client dependency.
- [ ] Confirm no Neo4j, n8n, MCP, LiteLLM middleware, automatic extraction, or E2EE code was added.
