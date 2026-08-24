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

Binds to `127.0.0.1` by default; set `HEARTHMEM_HOST=0.0.0.0` to accept connections from
elsewhere, which is what the container image does. `HEARTHMEM_ROOT` and `HEARTHMEM_PORT` are
read from the environment too.

## On Kubernetes

Agents run per person and reach one shared service, so it is meant to be deployed on a cluster.
The chart is published to GHCR on each tagged release:

```sh
helm install hearthmem oci://ghcr.io/jokajak/charts/hearthmem --version 0.1.0
```

Or from a checkout, [`deploy/charts/hearthmem/`](../deploy/charts/hearthmem/):

```sh
helm install hearthmem deploy/charts/hearthmem
```

Worth setting: `persistence.size`, `persistence.storageClass`, and — only if agents run
outside the cluster — `ingress.enabled=true` with `ingress.host`. The chart refuses to render
an ingress with TLS disabled, because an unrevocable bearer token in clear text is not a
trade-off worth offering.

There is no `replicaCount` value. Two constraints are load-bearing rather than conventional:

- **Exactly one replica, with the `Recreate` strategy.** The store is a git repository on a
  filesystem guarded by a single in-process writer lock. A second pod on the same volume
  corrupts it, and a rolling update briefly runs two — which is why the strategy is not the
  default.
- **`ReadWriteOnce`** on the claim, for the same reason.

The pod runs as uid 10001, non-root, with a read-only root filesystem and all capabilities
dropped. Only `/data` and `/tmp` are writable. Git works under those conditions because the
store sets repository-local identity and never needs a writable `HOME`.

`terminationGracePeriodSeconds: 30` gives an in-flight commit time to finish. The service
handles `SIGTERM` by draining, so pods stop in well under a second rather than being killed
mid-write.

## Access

A store is addressed by a secret token, generated at creation and returned once. The service
stores `sha256(token)` and finds a store by hashing whatever the caller presents, so the token
itself is never written to disk.

This is a bearer capability, and it is the whole of access control:

- anyone holding the token has full read and write access
- it cannot be revoked, because there is nothing recording who holds it
- attribution is self-asserted — an entry says who wrote it because the caller said so

Reasonable among people who already trust each other, which is who this is for. It is not
a substitute for authentication.

**Once this is on a network rather than a loopback interface, that matters more.** Send the
token in the `X-Store-Token` header, not the URL path — paths are recorded by every proxy and
ingress controller in between, and a logged token is a permanent one. The CLI already does
this; the path form exists for debugging by hand. Terminate TLS in front of the service, or
the token crosses the network in clear text.

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
