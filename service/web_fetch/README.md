# webfetch

URL in, content or nothing out.

Two stages, two processes, two sets of permissions:

```
request ─▶ webfetch-fetch ──artifact──▶ webfetch-inspect ─▶ envelope
           network egress               no network
           stores one response          decodes, scans, converts, scans
           produces no result           the only thing that returns content
```

* [`contracts/`](contracts) — the wire types both stages and the control plane
  share: the reusable result envelope, the `web_fetch:v1` request and payload,
  the stage handoff, the supported media set and the limits.
* [`fetch-worker/`](fetch-worker) — one bounded HTTP(S) GET, with destination
  policy applied to every hop and DNS pinned to the connection.
* [`inspector/`](inspector) — decode, scan, convert, scan again, and the gate
  that will not release content unless every required pass completed clean.
* [`rules/web-content-v1/`](rules/web-content-v1) — the shipped YARA-X bundle.
* [`schemas/`](schemas) and [`fixtures/`](fixtures) — the language-neutral copy
  of the contracts, and the fixtures the Rust types and the Python control-plane
  adapter both validate against the same manifest.

```sh
cargo test --workspace      # 58 unit and contract tests
./scripts/end-to-end.sh     # both binaries against a local fixture server
```

Operating it, tuning the rule bundle, the limits, the failure codes, and what is
still missing before this runs in a cluster: [`docs/runbooks/webfetch.md`](../../docs/runbooks/webfetch.md).

A non-match is not a safety verdict. It means the configured rules did not match;
the content is still external data.
