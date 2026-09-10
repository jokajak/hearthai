# Deploy HearthAI as one release

`deploy/charts/hearthai` is the household deployment entry point. It packages the
pinned upstream Open WebUI, LiteLLM, and Meridian images, a single-instance CNPG
Postgres cluster, and the local hearthmem chart into one Helm
release. HearthAI owns their manifests, configuration, storage defaults, probes,
and version compatibility. The standalone hearthmem chart remains available for
CLI-only consumers.

The cluster supplies namespace, DNS/TLS and ingress controller, storage classes or
existing claims, authentication credentials, and the CNPG operator/CRDs.
LiteLLM is bundled and Open WebUI's internal URL is generated automatically.
The identity provider and CNPG operator are not bundled. Postgres defaults on as
one server; set `postgres.enabled=false` to use an external database. Meridian is
bundled by default. The imported model catalogue is retained: only its original
Meridian address is rewritten to the release-local Service. It is not a newly
selected or automatically refreshed model list.

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
For a fresh install, omit all four `existingClaim` entries to create retained PVCs.

| Input | Purpose |
| --- | --- |
| `url` | HTTPS origin without a port, path, or trailing slash; drives ingress and OIDC callback |
| `sessionSecret.name` / `.key` | Stable Open WebUI signing/encryption key |
| `litellm.existingSecret` | Existing proxy/database Secret using the home-ops env contract |
| `litellm.persistence` | Token claim, default 64Mi on nfs-csi, or an existing claim |
| `litellm.ingress` | Optional proxy hostname/TLS for other clients and the admin UI |
| `litellm.config` | Optional full config replacement; blank uses the exact bundled home-ops config |
| `llm.baseUrl` | Only used with `litellm.enabled=false`; otherwise wired to the bundled Service |
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

## Bundled database and Meridian

home-ops supplies the **CloudNativePG operator**, not a HearthAI-managed copy of
that operator. HearthAI renders a `postgresql.cnpg.io/v1` Cluster with
`instances: 1`. There is no HA replica set, extra Postgres Deployment, operator
installation, or cross-namespace adoption of the shared home-ops database.

`postgres.enabled` defaults to `true`. The image pin is the existing home-ops
`ghcr.io/cloudnative-pg/postgresql:16.0-10`; storage defaults to 10Gi on
`openebs-hostpath`. CNPG bootstraps the `litellm` database and owner and creates
`<release-fullname>-postgres-app`. LiteLLM reads that Secret's `uri` key as
`DATABASE_URL`, overriding any old URL in its envFrom Secret. The postgres-init
container is omitted in this mode, so the new database never needs credentials
for the shared cluster or a superuser. CNPG's generated application Secret supplies
the connection URI and credentials ([CNPG documentation](https://cloudnative-pg.io/docs/devel/applications/)).

The CNPG Cluster has a Helm keep policy: disabling Postgres or uninstalling the
release leaves the database running. Retention is not backup. Configure backups
before storing important data; this simple deployment deliberately does not copy
home-ops' shared Barman destination/history or install a monitoring operator.
A single server has downtime during restarts or node failure.

With `postgres.enabled=false`, no new Cluster is rendered; LiteLLM uses
`DATABASE_URL` from `litellm.existingSecret`. The existing postgres-init
container remains enabled unless `litellm.initDb.enabled=false`.
Changing this switch does **not** copy any data. The migration examples explicitly
set it false to preserve your current proxy database and virtual keys. For a fresh
installation, remove that override. New databases require provisioning a LiteLLM
consumer virtual key and supplying it through `llm.apiKeySecret`; the package
does not silently give Open WebUI the proxy master key.

Meridian defaults on with `ghcr.io/rynfar/meridian:1.68.0`, uid/gid 1000, the
same probes/resources, and a retained 64Mi credential claim mounted at
`/home/claude/.claude`. Reuse `meridian-auth` for migration; a fresh claim requires
the existing one-time Claude login process. Its required `MERIDIAN_API_KEY`
defaults to the key in `litellm.existingSecret`, or use `meridian.existingSecret`
with the same key value. No credentials are copied from your workstation by this
change.

The bundled Cilium policy admits only this release's LiteLLM pods and node probes,
matching home-ops' policy with release-scoped selectors. Cilium remains cluster
infrastructure. Non-Cilium deployments can explicitly disable
`meridian.networkPolicy.enabled` and supply equivalent network controls.
Meridian has no ingress. `meridian.enabled=false` preserves the original external
Meridian address; an explicit full `litellm.config` override is never rewritten.

## Known-working home-ops proxy

The source of truth for this import is home-ops commit
[`6221eb6`](https://github.com/jokajak/home-ops/tree/6221eb6daa67f51c1aa84e3d43fe91486db7a4ee/kubernetes/apps/ai/litellm/app).
`files/litellm-config.yaml` preserves the source ConfigMap's `config.yaml` value
byte-for-byte, including comments. A regression test checks its SHA-256; render tests allow only the bundled
Meridian address substitution. No upstream model discovery, renaming, or upgrades were
performed for this import.

- Proxy: `ghcr.io/berriai/litellm-database:v1.99.1`.
- Database initializer: `ghcr.io/home-operations/postgres-init:18.6`.
- Same amd64 placement, resources, startup/readiness/liveliness probes, arguments,
  `STORE_MODEL_IN_DB=True`, and `CHATGPT_TOKEN_DIR=/token`.
- OpenAI routes: `gpt-6-astra`, `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna`,
  `gpt-5.5`, and `gpt-5.4-mini`, with their original `chatgpt/` provider identifiers,
  Responses mode, and capability flags.
- Same `drop_params: true`, two retries, 600-second timeout, database-backed model
  storage, and 300-second background health checks.
- Claude entries remain unchanged: `claude-opus-5`, `claude-sonnet-5`, and
  `claude-haiku-4-5`, using the existing Meridian service and `MERIDIAN_API_KEY`.
  Meridian is now deployed by this chart and the default routes are wired to its
  internal Service. Its credential volume must contain a valid Claude login.

The packaging changes Kubernetes resource names and internal service discovery,
adds a configuration rollout checksum, disables service-account token mounting,
and uses `Recreate` so token-cache writers never overlap during upgrades. The
proxy's provider/model configuration and runtime pins are otherwise preserved.

With external Postgres, the same `litellm-secret` can be reused unchanged. Its environment contract is:

| Keys | Purpose |
| --- | --- |
| `LITELLM_MASTER_KEY`, `LITELLM_SALT_KEY` | Existing proxy authority and encryption keys; preserve both during migration |
| `UI_USERNAME`, `UI_PASSWORD` | Existing proxy administration credentials |
| `DATABASE_URL` | Existing LiteLLM database URL, with appropriately escaped credentials |
| `MERIDIAN_API_KEY` | Existing external Meridian gate, not an Anthropic credential |
| `INIT_POSTGRES_DBNAME`, `INIT_POSTGRES_HOST`, `INIT_POSTGRES_USER`, `INIT_POSTGRES_PASS`, `INIT_POSTGRES_SUPER_PASS` | Existing postgres-init environment |

Like home-ops, the init and application containers receive this Secret via
`envFrom`. Provider Secret creation remains a cluster input. Database fields below are only
needed in external-Postgres mode; bundled Postgres supplies its own app Secret. Set
`litellm.initDb.enabled=false` in external mode if the database/role is already provisioned and the
initializer is intentionally unnecessary. The chart never creates a new virtual
key or substitutes the proxy master key for Open WebUI's consumer key: preserve
the existing database and `OPENAI_API_KEYS` Secret to keep that relationship.

Reusing `litellm-token` preserves the existing ChatGPT OAuth login. A fresh token
volume requires the same one-time interactive device-code login as home-ops;
installing a chart cannot manufacture that subscription credential. No login or
live model request is performed by this repository change.

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
maintenance window; all application Deployments use one replica and `Recreate` to avoid concurrent
writers. `ReadWriteOnce` alone does **not** prevent two pods on the same node from
writing to SQLite or the git store.

1. Back up the data volumes and LiteLLM database. Record the current Open WebUI image version and session
   secret, and confirm the claims are independently managed by home-ops (or arrange
   their retention before uninstalling their owning release). Keep the existing
   `WEBUI_SECRET_KEY` and OIDC client to preserve encrypted data and account identity.
   Keep `litellm-secret`, the LiteLLM database, its master/salt keys, the Open WebUI
   virtual key, and the existing `litellm-token` and `meridian-auth` claims. Keep
   `postgres.enabled=false` for the initial migration. Do not initialize a blank
   proxy database for this cutover: it would discard existing virtual keys/budgets.
2. Prepare complete site values with `openwebui.persistence.existingClaim` and
   `hearthmem.persistence.existingClaim`, plus `litellm.persistence.existingClaim` and `meridian.persistence.existingClaim`.
   Claims must be in the new release's namespace.
   Keep the existing public URL and use the same WebUI image for the initial cutover.
3. Suspend the old Open WebUI, hearthmem, LiteLLM, and Meridian Flux HelmReleases and stop their pods. Suspension alone leaves
   workloads running. Remove the old ingress route before enabling the new one;
   confirm no old writer can restart during cutover.
4. Install the HearthAI release against those claims. Do not import the old workload
   objects into Helm ownership; the new release creates its own workloads and Services.
5. Verify OIDC login, streaming chat against the configured LLM, old conversation
   history, LiteLLM readiness/model calls, and hearthmem `/health` plus access to a known store.
   Open WebUI is wired to the new proxy Service automatically. Other in-cluster
   consumers must switch to `hearthai-litellm:4000` (for release `hearthai`) or keep
   using the existing proxy hostname through the new optional ingress. The
   example preserves the `llm` hostname shape; the bundled Meridian policy already targets the new release.
   Update any external
   CLI consumers to the bundled hearthmem Service name (`hearthai-hearthmem` with
   release name `hearthai`, unless overridden).
6. Remove the retired release definitions from home-ops, preserving the PVCs and
   Secrets. That later repository change should leave one HearthAI release and its
   environment inputs, not separate Open WebUI or LiteLLM manifests. Retire the old Meridian release after verifying Claude requests. Keep the shared
   home-ops Postgres cluster in place: it serves other applications. Moving LiteLLM
   data to the optional dedicated instance requires a separate backup/restore cutover.

Before resuming an old release for rollback, stop the new writers and remove its
route. Open WebUI database migrations may make image downgrades unsafe; restore the
pre-upgrade backup when necessary. Helm rollback does not roll back volume contents.
Chart-created claims carry `helm.sh/resource-policy: keep`; existing claims remain
externally owned. Back up Open WebUI data, hearthmem, and the LiteLLM database;
their formats differ. The token and Meridian claims contain live credentials; its loss requires
login again rather than restoring conversation data.

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
