import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hearthmem import frontmatter
from hearthmem.store import InvalidRequest, MemoryStore, StoreNotFound, token_digest


@pytest.fixture
def store(tmp_path):
    return MemoryStore(tmp_path / "data")


def test_frontmatter_roundtrip():
    meta = {"id": "x", "tags": ["a", 'quote " and: colon'], "empty": []}
    text = frontmatter.dumps(meta, "body\nlines")
    back, body = frontmatter.loads(text)
    assert back == meta
    assert body == "body\nlines"


def test_frontmatter_rejects_garbage():
    with pytest.raises(frontmatter.FrontmatterError):
        frontmatter.loads("no delimiter here")
    with pytest.raises(frontmatter.FrontmatterError):
        frontmatter.loads("---\nid: 'x'\nunterminated")


def test_create_requires_purpose(store):
    with pytest.raises(InvalidRequest):
        store.create_store("   ", "alice")


def test_token_is_never_written_to_disk(store, tmp_path):
    token = store.create_store("Family", "alice")["token"]
    store.add_entry(token, "something", "alice")
    hits = subprocess.run(
        ["grep", "-r", token, str(tmp_path)], capture_output=True, text=True
    )
    assert hits.returncode != 0, "the raw token must not be persisted"
    assert (tmp_path / "data" / "stores" / token_digest(token)).is_dir()


def test_unknown_token_is_rejected(store):
    store.create_store("Family", "alice")
    with pytest.raises(StoreNotFound):
        store.describe("some-other-token")


def test_stores_are_isolated_from_each_other(store):
    a = store.create_store("A", "alice")["token"]
    b = store.create_store("B", "bob")["token"]
    store.add_entry(a, "only in A", "alice")
    assert [e["content"] for e in store.entries(b)] == []
    assert [e["content"] for e in store.entries(a)] == ["only in A"]


def test_identical_content_is_not_duplicated(store):
    token = store.create_store("Family", "alice")["token"]
    first = store.add_entry(token, "Bin day is Wednesday", "alice")
    second = store.add_entry(token, "  Bin day is Wednesday  ", "bob")
    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert second["author"] == "alice", "the original author is preserved"
    assert store.describe(token)["entry_count"] == 1


def test_entry_records_attribution(store):
    token = store.create_store("Family", "alice")["token"]
    store.add_entry(token, "Bob's fact", "bob", ["house"])
    entry = store.entries(token)[0]
    assert entry["author"] == "bob"
    assert entry["tags"] == ["house"]


def test_empty_entry_rejected(store):
    token = store.create_store("Family", "alice")["token"]
    with pytest.raises(InvalidRequest):
        store.add_entry(token, "   ", "alice")


def test_search_matches_content_and_tags(store):
    token = store.create_store("Family", "alice")["token"]
    store.add_entry(token, "Dentist for Sam on 12 March", "alice", ["calendar"])
    store.add_entry(token, "Bin day moved to Wednesday", "bob", ["house"])
    assert len(store.search(token, "dentist")) == 1
    assert len(store.search(token, "house")) == 1
    assert store.search(token, "helicopter") == []
    assert len(store.search(token, "")) == 2


def test_every_write_is_committed(store, tmp_path):
    token = store.create_store("Family", "alice")["token"]
    store.add_entry(token, "one", "alice")
    store.add_entry(token, "two", "bob")
    log = subprocess.run(
        ["git", "-C", str(tmp_path / "data"), "log", "--oneline"],
        capture_output=True, text=True, check=True,
    ).stdout.strip().splitlines()
    assert len(log) == 3
    assert "bob" in log[0]


def test_content_with_frontmatter_delimiters_survives(store):
    token = store.create_store("Family", "alice")["token"]
    nasty = "---\nnot: real frontmatter\n---\nstill body"
    store.add_entry(token, nasty, "alice")
    assert store.entries(token)[0]["content"] == nasty
