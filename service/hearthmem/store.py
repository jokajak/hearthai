"""Git-backed storage for shared memory stores.

A store is addressed by a secret token the creator hands out. The service never
persists that token: it keeps ``sha256(token)`` and locates a store by hashing
whatever the caller presents. Losing the token means losing the store, which is
the intended trade for not holding a credential we would have to protect.

Knowing a token is the whole of access control at this stage. It is a bearer
capability: it can be passed on, and it cannot be taken back.

A store may also have one read token, minted with the write token. It can
describe and search the store and nothing else. The service keeps only its
digest, as a pointer file ``readers/<sha256(read token)>`` holding the name of
the store directory, so a presented read token finds its store without scanning
every store. The store directory holds ``reader`` with the current read digest,
which lets a new mint remove the old pointer. Both live in the git tree.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path

from . import frontmatter

TOKEN_BYTES = 32
_SAFE = re.compile(r"[^a-z0-9]+")


class StoreNotFound(LookupError):
    pass


class InvalidRequest(ValueError):
    pass


class ReadOnlyToken(PermissionError):
    """A read token was presented where a write token is required."""


def token_digest(token: str) -> str:
    return hashlib.sha256(token.strip().encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _slug(text: str, fallback: str) -> str:
    slug = _SAFE.sub("-", text.strip().lower()).strip("-")[:48]
    return slug or fallback


def _tokenize(text: str) -> set[str]:
    return {t for t in _SAFE.sub(" ", text.lower()).split() if len(t) > 1}


class MemoryStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.stores = self.root / "stores"
        self.readers = self.root / "readers"
        self.stores.mkdir(parents=True, exist_ok=True)
        self.readers.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._git_init()

    # ---- git -------------------------------------------------------------
    def _git(self, *args: str) -> None:
        subprocess.run(
            ["git", "-C", str(self.root), *args],
            check=True,
            capture_output=True,
        )

    def _git_init(self) -> None:
        if (self.root / ".git").exists():
            return
        self._git("init", "-q")
        self._git("config", "user.email", "hearthmem@localhost")
        self._git("config", "user.name", "hearthmem")

    def _commit(self, message: str) -> None:
        self._git("add", "-A")
        status = subprocess.run(
            ["git", "-C", str(self.root), "status", "--porcelain"],
            check=True,
            capture_output=True,
            text=True,
        )
        if status.stdout.strip():
            self._git("commit", "-q", "-m", message)

    # ---- stores ----------------------------------------------------------
    def _dir_for(self, token: str, write: bool = False) -> Path:
        """Find the store a token opens.

        A write token always works. A read token works only when ``write`` is
        false; presenting one for a write raises ReadOnlyToken.
        """
        digest = token_digest(token)
        path = self.stores / digest
        if path.is_dir():
            return path
        pointer = self.readers / digest
        if pointer.is_file():
            target = self.stores / pointer.read_text(encoding="utf-8").strip()
            if target.is_dir():
                if write:
                    raise ReadOnlyToken("this token is read-only")
                return target
        raise StoreNotFound("no store matches that token")

    def mint_read_token(self, token: str) -> str:
        """Mint the store's read token, revoking any previous one.

        Requires the write token. The read token is returned once and never kept.
        """
        with self._lock:
            path = self._dir_for(token, write=True)
            read_token = secrets.token_urlsafe(TOKEN_BYTES)
            digest = token_digest(read_token)
            marker = path / "reader"
            if marker.is_file():
                (self.readers / marker.read_text(encoding="utf-8").strip()).unlink(
                    missing_ok=True
                )
            (self.readers / digest).write_text(path.name + "\n", encoding="utf-8")
            marker.write_text(digest + "\n", encoding="utf-8")
            self._commit("rotate read token")
        return read_token

    def create_store(self, purpose: str, author: str) -> dict:
        purpose = (purpose or "").strip()
        if not purpose:
            raise InvalidRequest("a store needs a purpose")
        token = secrets.token_urlsafe(TOKEN_BYTES)
        with self._lock:
            path = self.stores / token_digest(token)
            path.mkdir(parents=True)
            (path / "entries").mkdir()
            meta = {
                "purpose": purpose,
                "created_at": _now(),
                "created_by": (author or "unknown").strip(),
            }
            (path / "store.md").write_text(
                frontmatter.dumps(meta, f"# {purpose}\n"), encoding="utf-8"
            )
            self._commit(f"create store: {purpose}")
        return {"token": token, **meta, "entry_count": 0}

    def describe(self, token: str) -> dict:
        path = self._dir_for(token)
        meta, _ = frontmatter.loads((path / "store.md").read_text(encoding="utf-8"))
        return {**meta, "entry_count": len(list((path / "entries").glob("*.md")))}

    # ---- entries ---------------------------------------------------------
    def add_entry(self, token: str, content: str, author: str, tags=None) -> dict:
        self._dir_for(token, write=True)
        content = (content or "").strip()
        if not content:
            raise InvalidRequest("an entry needs content")
        author = (author or "unknown").strip()
        tags = [str(t).strip() for t in (tags or []) if str(t).strip()]

        with self._lock:
            path = self._dir_for(token, write=True)
            digest = hashlib.sha256(content.encode("utf-8")).hexdigest()

            for existing in sorted((path / "entries").glob("*.md")):
                meta, _ = frontmatter.loads(existing.read_text(encoding="utf-8"))
                if meta.get("content_sha256") == digest:
                    return {**meta, "duplicate": True}

            entry_id = digest[:12]
            meta = {
                "id": entry_id,
                "author": author,
                "created_at": _now(),
                "tags": tags,
                "content_sha256": digest,
            }
            name = f"{meta['created_at'][:10]}-{_slug(content[:48], entry_id)}.md"
            (path / "entries" / name).write_text(
                frontmatter.dumps(meta, content), encoding="utf-8"
            )
            self._commit(f"{author}: add entry {entry_id}")
        return {**meta, "duplicate": False}

    def entries(self, token: str) -> list[dict]:
        path = self._dir_for(token)
        out = []
        for file in sorted((path / "entries").glob("*.md")):
            meta, body = frontmatter.loads(file.read_text(encoding="utf-8"))
            out.append({**meta, "content": body})
        out.sort(key=lambda e: e.get("created_at", ""), reverse=True)
        return out

    def search(self, token: str, query: str, limit: int = 10) -> list[dict]:
        wanted = _tokenize(query or "")
        rows = self.entries(token)
        if not wanted:
            return rows[:limit]
        scored = []
        for row in rows:
            haystack = _tokenize(row["content"]) | _tokenize(" ".join(row.get("tags", [])))
            overlap = len(wanted & haystack)
            if overlap:
                scored.append((overlap, row))
        scored.sort(key=lambda pair: (-pair[0], pair[1].get("created_at", "")))
        return [row for _, row in scored[:limit]]
