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


# ---- read tokens -----------------------------------------------------------
from hearthmem.store import ReadOnlyToken  # noqa: E402


def test_read_token_can_describe_and_search(store):
    token = store.create_store("Family", "alice")["token"]
    store.add_entry(token, "Bin day is Wednesday", "alice")
    read = store.mint_read_token(token)
    assert read != token
    assert store.describe(read)["purpose"] == "Family"
    assert [e["content"] for e in store.search(read, "bin")] == ["Bin day is Wednesday"]


def test_read_token_is_refused_for_writes_and_minting(store):
    token = store.create_store("Family", "alice")["token"]
    read = store.mint_read_token(token)
    with pytest.raises(ReadOnlyToken):
        store.add_entry(read, "planted", "mallory")
    with pytest.raises(ReadOnlyToken):
        store.mint_read_token(read)
    assert store.describe(token)["entry_count"] == 0


def test_minting_again_revokes_the_previous_read_token(store):
    token = store.create_store("Family", "alice")["token"]
    old = store.mint_read_token(token)
    new = store.mint_read_token(token)
    assert old != new
    with pytest.raises(StoreNotFound):
        store.describe(old)
    assert store.describe(new)["purpose"] == "Family"


def test_write_token_keeps_all_rights_after_minting(store):
    token = store.create_store("Family", "alice")["token"]
    store.mint_read_token(token)
    store.add_entry(token, "still works", "alice")
    assert store.describe(token)["entry_count"] == 1
    assert store.mint_read_token(token)


def test_describe_reveals_no_token_or_digest(store):
    token = store.create_store("Family", "alice")["token"]
    read = store.mint_read_token(token)
    for presented in (token, read):
        text = repr(store.describe(presented))
        for secret in (token, read, token_digest(token), token_digest(read)):
            assert secret not in text


def test_read_token_for_one_store_finds_nothing_in_another(store):
    a = store.create_store("A", "alice")["token"]
    b = store.create_store("B", "bob")["token"]
    store.add_entry(a, "only in A", "alice")
    store.add_entry(b, "only in B", "bob")
    read_a = store.mint_read_token(a)
    assert [e["content"] for e in store.entries(read_a)] == ["only in A"]
    assert store.describe(read_a)["purpose"] == "A"


def test_read_token_is_never_written_to_disk_and_is_committed(store, tmp_path):
    token = store.create_store("Family", "alice")["token"]
    read = store.mint_read_token(token)
    store.add_entry(token, "something", "alice")
    hits = subprocess.run(["grep", "-r", read, str(tmp_path)], capture_output=True, text=True)
    assert hits.returncode != 0, "the raw read token must not be persisted"
    assert (tmp_path / "data" / "readers" / token_digest(read)).is_file()
    status = subprocess.run(
        ["git", "-C", str(tmp_path / "data"), "status", "--porcelain"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert status.strip() == "", "the pointer must be committed"


def test_revoked_pointer_is_removed(store, tmp_path):
    token = store.create_store("Family", "alice")["token"]
    old = store.mint_read_token(token)
    store.mint_read_token(token)
    assert not (tmp_path / "data" / "readers" / token_digest(old)).exists()
