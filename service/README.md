# hearthmem

The service behind the [`shared-memory` skill](../skills/shared-memory/SKILL.md). It holds
memory stores that several people's agents can read and write, while each person's private
memory stays in their own agent, untouched by this.

Python 3.11+, standard library only. Storage is markdown files with YAML frontmatter,
committed to git on every write.

## Running it

```sh
python3 -m hearthmem.server --root ./data --port 8765
```

Binds to `127.0.0.1` by default. Put it behind something that terminates TLS before letting
it off the machine.

## Access

A store is addressed by a secret token, generated at creation and returned once. The service
stores `sha256(token)` and finds a store by hashing whatever the caller presents, so the token
itself is never written to disk.

This is a bearer capability, and it is the whole of access control:

- anyone holding the token has full read and write access
- it cannot be revoked, because there is nothing recording who holds it
- attribution is self-asserted — an entry says who wrote it because the caller said so

Reasonable among people who already trust each other, which is who this is for. It is not
a substitute for authentication, and it should not be exposed to a network where that
distinction matters.

## API

| | |
|---|---|
| `GET /health` | liveness |
| `POST /stores` | `{purpose, author}` → `{token, ...}`, the only time the token is returned |
| `GET /stores/{token}` | store metadata and entry count |
| `POST /stores/{token}/entries` | `{content, author, tags}`; `201` for new, `200` if identical content already exists |
| `GET /stores/{token}/entries?q=&limit=` | search by overlapping terms across content and tags |

The token may also be sent as an `X-Store-Token` header instead of in the path.

## Behaviour worth knowing

- **Identical content is not duplicated.** Re-sharing the same fact returns the original,
  including its original author and timestamp.
- **Writes are serialised** behind a lock, so several agents writing at once will not
  corrupt the git repository.
- **Nothing is ever deleted or edited.** Correct a wrong entry by adding one that supersedes it.
- **Search is term overlap**, not semantic. It will miss paraphrases. That is a known limit,
  not an oversight — see the README's open questions about how much structure memory needs.

## Layout

```
data/
  stores/
    <sha256-of-token>/
      store.md              # purpose, created_at, created_by
      entries/
        2026-08-24-bin-collection-moved.md
```

## Tests

```sh
python3 -m pytest tests -q
```
