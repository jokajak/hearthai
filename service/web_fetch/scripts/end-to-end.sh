#!/usr/bin/env bash
# Run both webfetch stages as separate processes against a local fixture server.
#
# This is the check that the handoff works the way the deployment will use it:
# one process fetches and exits, a second process reads what it left behind and
# is the only one that produces a result. It needs a test destination policy,
# because the fixture server is on loopback.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"; [[ -n "${server_pid:-}" ]] && kill "$server_pid" 2>/dev/null || true' EXIT

cargo build --manifest-path "$here/Cargo.toml" --quiet
fetch="$here/target/debug/webfetch-fetch"
inspect="$here/target/debug/webfetch-inspect"

python3 -m http.server --bind 127.0.0.1 --directory "$here/fixtures/html" 0 >"$work/server.log" 2>&1 &
server_pid=$!
for _ in $(seq 1 50); do
  port="$(sed -n 's/.*port \([0-9]\+\).*/\1/p' "$work/server.log" | head -1)"
  [[ -n "$port" ]] && break
  sleep 0.1
done
[[ -n "${port:-}" ]] || { echo "fixture server did not start"; cat "$work/server.log"; exit 1; }

run_case() {
  local name="$1" path="$2" format="$3" expected_status="$4"
  local run_id="run-${name}"
  local artifacts="$work/$name"
  mkdir -p "$artifacts"
  printf '{"url":"http://127.0.0.1:%s/%s","format":"%s"}' "$port" "$path" "$format" >"$artifacts/request.json"

  set +e
  "$fetch" --run-id "$run_id" --request "$artifacts/request.json" \
    --policy "$here/fixtures/test-destination-policy.json" --artifact-dir "$artifacts" 2>"$artifacts/fetch.log"
  local fetch_status=$?
  set -e
  if [[ $fetch_status -gt 1 ]]; then
    echo "FAIL $name: fetch stage could not run"; cat "$artifacts/fetch.log"; exit 1
  fi

  set +e
  "$inspect" --run-id "$run_id" --artifact-dir "$artifacts" \
    --rules "$here/rules/web-content-v1" --out "$artifacts/envelope.json" 2>"$artifacts/inspect.log"
  set -e

  python3 - "$artifacts/envelope.json" "$expected_status" "$name" <<'PY'
import json, sys
envelope = json.load(open(sys.argv[1]))
expected, name = sys.argv[2], sys.argv[3]
assert envelope["envelope_version"] == 1, envelope
assert envelope["tool"] == "web_fetch" and envelope["tool_version"] == 1, envelope
assert envelope["trust"] == "external_untrusted", envelope
assert envelope["status"] == expected, f"{name}: expected {expected}, got {envelope['status']}"
if expected == "ok":
    assert envelope["inspection"]["status"] == "no_match", envelope
    assert envelope["error"] is None and envelope["data"]["content"], envelope
else:
    assert envelope["data"] is None, envelope
    body = json.dumps(envelope)
    for leak in ("attacker.example", "Ignore all previous", "Release notes"):
        assert leak not in body, f"{name}: rejection leaked {leak!r}"
print(f"  {name}: {envelope['status']} ({envelope['inspection']['status']})")
PY
}

echo "webfetch end to end:"
run_case docs docs.html markdown ok
run_case source docs.html html ok
run_case plain docs.html text ok
run_case injected injected.html markdown rejected
run_case quoted quotes-an-injection.html markdown ok

# The structure of the documentation page has to survive conversion, or the tool
# is returning something a reader would not recognise.
python3 - "$work/docs/envelope.json" <<'PY'
import json, sys
content = json.load(open(sys.argv[1]))["data"]["content"]
for expected in ("# Deploying HearthAI", "## Values", "| webfetch.enabled", "helm upgrade --install", "](https://example.com/manual)"):
    assert expected in content, f"conversion lost {expected!r}:\n{content}"
assert ".nav { display: flex }" not in content, "stylesheet survived cleanup"
print("  docs: structure preserved")
PY

# A refused destination must fail before any connection is made.
mkdir -p "$work/refused"
printf '{"url":"http://169.254.169.254/latest/meta-data/"}' >"$work/refused/request.json"
set +e
"$fetch" --run-id run-refused --request "$work/refused/request.json" \
  --policy "$here/fixtures/test-destination-policy.json" --artifact-dir "$work/refused" 2>/dev/null
set -e
"$inspect" --run-id run-refused --artifact-dir "$work/refused" \
  --rules "$here/rules/web-content-v1" --out "$work/refused/envelope.json" 2>/dev/null || true
python3 - "$work/refused/envelope.json" <<'PY'
import json, sys
envelope = json.load(open(sys.argv[1]))
assert envelope["status"] == "error" and envelope["error"]["code"] == "unsafe_source", envelope
assert envelope["inspection"]["status"] == "not_run", envelope
print("  metadata endpoint: unsafe_source")
PY

# A production policy refuses loopback, so the same fixture URL is unreachable.
mkdir -p "$work/production"
printf '{"url":"http://127.0.0.1:%s/docs.html"}' "$port" >"$work/production/request.json"
set +e
"$fetch" --run-id run-production --request "$work/production/request.json" \
  --policy "$here/deploy-destination-policy.example.json" --artifact-dir "$work/production" 2>/dev/null
set -e
python3 - "$work/production/stage.json" <<'PY'
import json, sys
outcome = json.load(open(sys.argv[1]))
assert outcome["code"] == "unsafe_source", outcome
print("  production policy: loopback refused")
PY

echo "end to end: ok"
