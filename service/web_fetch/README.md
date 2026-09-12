# web_fetch

The isolated webfetch tool: one URL in, inspected content or a fixed error out.

Design: [safer webfetch](../../docs/superpowers/specs/2026-09-12-isolated-webfetch-design.md).
Plan: [implementation steps](../../docs/superpowers/plans/2026-09-12-isolated-webfetch.md).
Operations: [runbook](../../docs/runbooks/webfetch.md).

## Layout

| Path | What it is |
|---|---|
| `schemas/` | Language-neutral JSON Schemas for the request, the reusable envelope, the webfetch payload and the error |
| `fixtures/` | Shared request and envelope corpus, with `manifest.json` naming each case and its expected verdict |
| `contracts/` | Rust wire types for `web_fetch:v1` and the versioned tool-result envelope |
| `fetch-worker/` | The bounded HTTP(S) stage: destination policy, pinned connections, limits, artifact handoff |
| `inspector/` | The offline stage: decoding, YARA-X detection, conversion chain, result gate |
| `inspector/rules/` | The shipped rule bundle |

`fixtures/manifest.json` is replayed by `contracts/tests/fixtures.rs` and by
`service/ai_jobs/tests/test_web_fetch.py`. Both implementations must agree on
every case, which is what keeps the Python control plane and the Rust workers
from drifting apart without a shared library between them.

## Working on it

```sh
cargo fmt --all
cargo clippy --workspace --all-targets -- -D warnings
cargo test --workspace
```

The toolchain is pinned in `rust-toolchain.toml`; rustup installs it on the
first `cargo` invocation in this directory.

The fetch worker's live connector is behind the default-on `network` feature
and is the only code in the workspace that can open a socket. `cargo build
--workspace --no-default-features` builds everything else without it, and no
test in the workspace touches the network in either configuration: resolution
and the single HTTP exchange are injected, so redirects, DNS rebinding and
limit behaviour are exercised against scripted answers.
