# Deploy HearthAI as one release

`deploy/charts/hearthai` is the household deployment entry point. It packages the
pinned upstream Open WebUI image and the local hearthmem chart into one Helm
release. HearthAI owns their manifests, configuration, storage defaults, probes,
and version compatibility. The standalone hearthmem chart remains available for
CLI-only consumers.

The cluster supplies namespace, DNS/TLS and ingress controller, storage classes or
existing claims, authentication credentials, and an OpenAI-compatible LLM endpoint
(such as LiteLLM). Neither LiteLLM nor the identity provider is bundled. There are
no home-ops-specific domains, secret providers, or cluster addresses in the chart
defaults.

**Current capability boundary:** this deploys a working Open WebUI chat surface
with its built-in personal memory and the existing shared-memory service. The
shared-memory Open WebUI adapter and deployable `ai-jobs` runtime are still pending;
there is no automatic shared-memory tool registration, Git execution, or research
worker in this release. Merely setting a memory URL in Open WebUI would not create
that integration. Future runtime components and their internal wiring belong in
this package, not in home-ops. Code execution/interpreter and ordinary-user
workspace tool import/access are disabled by default; trusted administrators must
not install in-process tools as a substitute for the planned sandbox substrate.

## Inputs

Start with [the site values example](examples/hearthai-values.yaml). It uses existing
claim and secret names from the current deployment but placeholder public domains.
For a fresh install, omit both `existingClaim` entries to create retained PVCs.

| Input | Purpose |
| --- | --- |
| `url` | HTTPS origin without a port, path, or trailing slash; drives ingress and OIDC callback |
| `sessionSecret.name` / `.key` | Stable Open WebUI signing/encryption key |
| `llm.baseUrl` | One OpenAI-compatible endpoint, including its `/v1` path |
| `llm.apiKeySecret.name` / `.key` | One matching API key; use a nonempty placeholder for a keyless backend |
| `auth.mode` | `oidc` (default) or `local`; never anonymous |
| `auth.oidc.discoveryUrl` | Provider's HTTPS discovery URL |
| `auth.oidc.credentialsSecret` | Secret name and client ID/secret key mappings |
| `auth.local.adminSecret` | Secret name and bootstrap email/password key mappings for local mode |
| `ingress.className`, `.annotations`, `.tls.secretName` | Cluster ingress/TLS inputs; blank secret uses the controller default certificate |
| `openwebui.persistence` | Size, storage class, or existing claim for conversations/accounts/uploads |
| `hearthmem.persistence` | Existing hearthmem chart storage inputs |
| `openwebui.nodeSelector`, `hearthmem.nodeSelector` | Optional placement; the example keeps WebUI on amd64 |

Secrets must already exist in the release namespace. External Secrets, SOPS, or
another cluster-specific mechanism can deliver them. The chart references individual
keys rather than copying credentials into values or a ConfigMap. Default keys are
`WEBUI_SECRET_KEY`, `OPENAI_API_KEYS`, `OAUTH_CLIENT_ID`, and `OAUTH_CLIENT_SECRET`.
Local mode instead requires `WEBUI_ADMIN_EMAIL` and `WEBUI_ADMIN_PASSWORD`.
Do not commit actual credentials in site values.

OIDC mode disables the password login form and local signup, enables OIDC account
creation, and derives the redirect URI as `<url>/oauth/oidc/callback`. Register that
exact callback at the identity provider. Restrict sign-in through the provider's
application policy. Upstream first-user admin and subsequent-user approval rules
still apply: bootstrap the intended administrator first and approve household
users in Open WebUI. Local mode creates an administrator on an empty database from
the bootstrap Secret; it does not reset existing passwords. Administrators can add
accounts; public signup stays disabled.

Configuration is GitOps-owned (`ENABLE_PERSISTENT_CONFIG=false` and
`ENABLE_OAUTH_PERSISTENT_CONFIG=false`). UI edits to those settings are not the
authoritative configuration. Configuration changes roll the pod through a checksum;
Secret value changes need a restart or a controller such as Reloader. The example
opts into an already-installed Reloader via a Deployment annotation.

## Install from source

```sh
helm dependency build deploy/charts/hearthai
helm upgrade --install hearthai deploy/charts/hearthai \
  --namespace ai --create-namespace \
  --values site-values.yaml --wait --timeout 15m
```

The release workflow publishes `oci://ghcr.io/jokajak/charts/hearthai` alongside the
standalone memory chart on the next tagged release. After publication, the same
command can use that OCI URL with an explicit published `--version`. Source installs
pin the existing hearthmem `0.1.0` image; tagged releases set the bundled memory image
to the release version. Open WebUI is independently pinned to `v0.11.3`.

For Flux before an OCI release is published, use the complete
[GitRepository and HelmRelease example](examples/flux-hearthai.yaml). Replace the
placeholder domains and confirm the Secret/claim names. It uses the same site
values as the Helm example, with no additional application manifests. Pin the
GitRepository to a reviewed tag or commit for deployment. Flux resolves the local
chart dependency from the same Git source artifact. After OCI publication, replace
the Git source/chart reference with your pinned OCI chart source.

## Migrate from separate home-ops releases

This repository does not change or reconcile home-ops automatically. Plan a short
maintenance window; both services use one replica and `Recreate` to avoid concurrent
writers. `ReadWriteOnce` alone does **not** prevent two pods on the same node from
writing to SQLite or the git store.

1. Back up both volumes. Record the current Open WebUI image version and session
   secret, and confirm the claims are independently managed by home-ops (or arrange
   their retention before uninstalling their owning release). Keep the existing
   `WEBUI_SECRET_KEY` and OIDC client to preserve encrypted data and account identity.
2. Prepare complete site values with `openwebui.persistence.existingClaim` and
   `hearthmem.persistence.existingClaim`. Claims must be in the new release's namespace.
   Keep the existing public URL and use the same WebUI image for the initial cutover.
3. Suspend the old Flux HelmReleases and stop their pods. Suspension alone leaves
   workloads running. Remove the old ingress route before enabling the new one;
   confirm no old writer can restart during cutover.
4. Install the HearthAI release against those claims. Do not import the old workload
   objects into Helm ownership; the new release creates its own workloads and Services.
5. Verify OIDC login, streaming chat against the configured LLM, old conversation
   history, and hearthmem `/health` plus access to a known store. Update any external
   CLI consumers to the bundled hearthmem Service name (`hearthai-hearthmem` with
   release name `hearthai`, unless overridden).
6. Remove the retired release definitions from home-ops, preserving the PVCs and
   Secrets. That later repository change should leave one HearthAI release and its
   environment inputs, not separate Open WebUI manifests.

Before resuming an old release for rollback, stop the new writers and remove its
route. Open WebUI database migrations may make image downgrades unsafe; restore the
pre-upgrade backup when necessary. Helm rollback does not roll back volume contents.
Chart-created claims carry `helm.sh/resource-policy: keep`; existing claims remain
externally owned. Back up both Open WebUI data and hearthmem; their formats differ.

The Open WebUI image retains upstream branding and its upstream license. HearthAI
packages the image without forking or relabeling it. The stock image runs as root;
its chart drops capabilities and disables service-account token mounting, but does
not claim the non-root/read-only-root hardening of the hearthmem image.

## Validate

```sh
helm dependency build deploy/charts/hearthai
helm lint deploy/charts/hearthai -f deploy/examples/hearthai-values.yaml
uv run --no-project --with 'pytest>=8,<10' --with pyyaml \
  python -m pytest deploy/tests -q
```

Tests render both authentication modes, verify selector/storage/Secret wiring and
configuration-triggered rollout, reject incomplete inputs, and render the packaged
chart without access to sibling source directories. CI also validates rendered
Kubernetes resources with kubeconform. These are manifest tests, not a live-cluster
OIDC or LLM connectivity test.

Upstream configuration references:
[environment variables](https://docs.openwebui.com/reference/env-configuration/),
[SSO](https://docs.openwebui.com/features/authentication-access/auth/sso/).
Environment names and bootstrap behavior were checked against the pinned
[Open WebUI v0.11.3 source](https://github.com/open-webui/open-webui/tree/v0.11.3).
