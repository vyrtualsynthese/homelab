# Vikunja task board — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up Vikunja in the cluster as the single place task state lives, wire it to the agents
over its own MCP endpoint, and make the operator's only job a review queue.

**Architecture:** A wrapper chart (pattern D) pins the upstream `vikunja` chart, published as an OCI
artifact, and adds the one thing the repo requires and the upstream chart cannot render: a Traefik
`IngressRoute`. State lives in the shared PostgreSQL, not in a pod volume. One Vikunja project per
GitHub repository, each with a four-bucket kanban view, plus one cross-project saved filter that is
the operator's screen. Agents reach it through `POST /api/v2/mcp` with a scoped token; the protocol
is five lines in the global `AGENTS.md` and one page in this repo.

**Tech Stack:** Helm 4 wrapper chart, upstream chart `vikunja` 2.6.0 (app 2.8.0) from
`oci://ghcr.io/go-vikunja/helm-chart`, bjw-s `common` 1.5.1 (a dependency of the upstream chart),
Traefik v2 CRDs, PostgreSQL 18 in `databases`, Vikunja's built-in MCP endpoint.

**Spec:** `docs/superpowers/specs/2026-11-15-agent-work-tracking-and-docs-site-design.md` (§9, and
§11 for secrets, placement and verification)

## Global Constraints

- The board is the only record of task state. The four buckets are `Ready`, `Doing`,
  `Waiting for validation`, `Done`, in that order; `Done` is the view's done bucket.
- Only the operator moves a ticket to `Done`. Only an operator move can put a ticket back into
  `Doing` after a refusal.
- Chart shape: wrapper chart in `cluster/helm/vikunja/`, upstream chart pinned by exact `version:`,
  image pinned by tag. `charts/` and `Chart.lock` are never committed.
- Ingress: Traefik v2 CRDs only, `apiVersion: traefik.containo.us/v1alpha1`. Host
  `vikunja.kub.local` over TLS on `websecure` with `tls.options.name: internal-tls` in `kube-system`
  and **no** `secretName`/`certResolver`, so the cluster's default internal certificate serves it
  (`docs/runbooks/internal-ca.md`). A second `IngressRoute` on `web` redirects to HTTPS with
  `kube-system-lan-only` then `kube-system-redirect-to-https`. The TLS router carries two rules:
  `/` with `kube-system-lan-only` + `kube-system-authentik-forwardauth`, and `/api` at `priority: 100`
  with `kube-system-lan-only` only. Never `networking.k8s.io/v1`.
- The upstream chart's own ingress is disabled — it renders an `Ingress`.
- Monitoring: `metrics.enabled: true` in `config.yml`; pod annotations
  `prometheus.io/scrape: "true"`, `prometheus.io/port: "3456"`,
  `prometheus.io/path: /api/v1/metrics`.
- Logging: `log.level: INFO`, `log.format: text`, `log.http: stdout`, `log.database: off`,
  `log.events: off`.
- `service.publicurl` is `https://vikunja.kub.local` — HTTPS, or Vikunja hands out HTTP links.
- Stateful: `strategy: Recreate`, one `local-path` claim of 5 GiB at `/app/vikunja/files`,
  `nodeSelector: kubernetes.io/hostname: nuc0`.
- Every container sets a memory request and limit. No image uses a floating tag.
- Secrets are created by hand and never committed. The database password never appears in a values
  file or in rendered output.
- Files under `cluster/argocd/**` keep CRLF line endings.
- The builder has no cluster write access. Every cluster-side step is a runbook the operator
  executes and pastes back.

## Review Focus

- **The API route shipping without its gate.** `/api` deliberately skips Authentik, so `lan-only` is
  the only thing between the internet and a token endpoint. If the API rule is rendered without
  `kube-system-lan-only`, the board answers the internet. Task 2 greps the rendered route for the
  middleware and the priority, and Task 3 verifies from off-LAN.
- **A ticket that says done when it is not.** The `Done` bucket marks a task done through the view's
  done-bucket setting. If the bucket is created without that flag, tasks in it stay open and the
  board lies. Task 4 verifies the bucket id is the view's `done_bucket_id`.
- **An agent moving someone else's ticket.** The MCP token's permission preset decides what the
  agent can write. Full access would let it delete projects. Task 5 verifies the token is on the
  read+write preset and that `explore` is denied the whole server.
- **The password leaking through the chart.** The value has to reach the container as a
  `secretKeyRef`. A pasted literal in `values.yaml` is the failure this repo has avoided everywhere
  else; Task 2 greps the rendered Deployment for both `secretKeyRef` and the absence of the literal.
- **A refused ticket that never moves.** The refusal path is a comment plus a move back to `Doing`.
  If the agent never reads comments, refusals are silent. Task 7 exercises the refusal once, and the
  protocol document says where the agent must look.
- **First start without its database.** Vikunja exits if the database is missing, and the pod then
  crashloops while the operator hunts a cause. Task 1 runs before Task 2's first sync, and Task 3
  checks the log line that means the connection was made.

---

### Task 1: Database, namespace and credential

**Files:**
- Create: `docs/runbooks/vikunja-database.md`

**Interfaces:**
- Produces: PostgreSQL role `vikunja` owning database `vikunja`; Kubernetes Secret `vikunja-db` in
  namespace `vikunja` with the single key `VIKUNJA_DATABASE_PASSWORD`; namespace `vikunja` exists.

- [ ] **Step 1: Read the procedure this runbook extends**

Read `docs/runbooks/postgres-add-database.md`. The new runbook follows its structure exactly and
reuses its SQL block; only the names and the consumer differ.

- [ ] **Step 2: Write `docs/runbooks/vikunja-database.md`**

Contents, in this order:

1. Purpose: the board's database, created once before Vikunja's first start.
2. Names, as a table: role `vikunja`, database `vikunja`, owner `vikunja`, namespace `vikunja`,
   Secret `vikunja-db`, key `VIKUNJA_DATABASE_PASSWORD`.
3. **Namespace first.** `kubectl create namespace vikunja` — the Secret cannot exist before its
   namespace, and the Application's `CreateNamespace=true` only acts at sync time. One line saying
   that, so nobody deletes the line as redundant.
4. The SQL block, copied verbatim from the postgres runbook, with the password generated by
   `openssl rand -base64 24`.
5. The Secret:
   ```bash
   kubectl -n vikunja create secret generic vikunja-db \
     --from-literal=VIKUNJA_DATABASE_PASSWORD="$PGPASSWORD"
   ```
6. Verification, with the exact expected output:
   ```bash
   kubectl -n databases exec statefulset/postgres -- \
     psql -U postgres -tAc "select datname from pg_database where datname='vikunja'"
   # expect: vikunja
   kubectl -n vikunja get secret vikunja-db -o jsonpath='{.data.VIKUNJA_DATABASE_PASSWORD}' | base64 -d | wc -c
   # expect: 32 (24 bytes of randomness, base64-encoded and decoded back)
   ```
7. Rollback: drop the database and delete the Secret. The board has no data yet.

- [ ] **Step 3: Operator executes the runbook and pastes both verification outputs back**

- [ ] **Step 4: Commit**

```bash
git add docs/runbooks/vikunja-database.md
git commit -m "📝 docs: runbook to create the board's database"
```

---

### Task 2: The wrapper chart and its Application

**Files:**
- Create: `cluster/helm/vikunja/Chart.yaml`
- Modify: `.gitignore`
- Create: `cluster/helm/vikunja/values.yaml`
- Create: `cluster/helm/vikunja/templates/ingressroute.yaml`
- Create: `cluster/helm/vikunja/README.md`
- Create: `cluster/argocd/projects/vikunja.yaml` (CRLF)

**Interfaces:**
- Consumes: Secret `vikunja-db` key `VIKUNJA_DATABASE_PASSWORD` (Task 1).
- Produces: Service `vikunja` port `3456` in namespace `vikunja`; `IngressRoute` `vikunja` at
  `vikunja.kub.local`.

- [ ] **Step 1: Ignore the built dependency before it appears**

`helm dependency build` writes `cluster/helm/vikunja/charts/` and `Chart.lock`, which must never be
committed. Add to `.gitignore`, following the existing per-chart blocks:

```
# Built by ArgoCD at sync time
cluster/helm/vikunja/charts/
cluster/helm/vikunja/Chart.lock
```

- [ ] **Step 2: Write `Chart.yaml`**

```yaml
apiVersion: v2
name: vikunja
description: Vikunja task board — the only place task state lives
type: application
version: 0.1.0
appVersion: "2.8.0"

dependencies:
  - name: vikunja
    version: 2.6.0
    repository: oci://ghcr.io/go-vikunja/helm-chart
```

`oci://` dependencies are already used in this repo (`discourse`, `ashuvidz-website`). Do not commit
`charts/` or `Chart.lock`.

- [ ] **Step 3: Write `values.yaml`**

The upstream chart merges its values under the key `vikunja:` onto the bjw-s `common` schema, and its
hardcoded values already set the Service on port `3456`, the config at `/etc/vikunja/config.yml`,
probes on `/api/v1/info`, and `podSecurityContext.fsGroup: 1000`. Everything below overrides that
base; all of it is standard bjw-s `common` schema. Set:

- `vikunja.image.repository: vikunja/vikunja`, `vikunja.image.tag: "2.8.0"` — the MCP endpoint
  requires Vikunja 2.7.0 or later, so never pin below it.
- `vikunja.controllers.main.strategy: Recreate`.
- `vikunja.controllers.main.pod.nodeSelector: {kubernetes.io/hostname: nuc0}`.
- `vikunja.controllers.main.containers.main.resources`: requests `cpu: 50m`, `memory: 128Mi`;
  limits `memory: 512Mi`.
- `vikunja.controllers.main.containers.main.securityContext`:
  `allowPrivilegeEscalation: false`, `capabilities: {drop: [ALL]}`, `readOnlyRootFilesystem: false`
  (the image writes a cache under `/tmp`).
- `vikunja.ingress.main.enabled: false` — the upstream renders a `networking.k8s.io/v1` Ingress.
- `vikunja.persistence.data.enabled: true`, `storageClass: local-path`, `size: 5Gi`,
  `accessMode: ReadWriteOnce` (default mount path `/app/vikunja/files` is already correct).
- `vikunja.persistence.database.enabled: false` — no SQLite.
- `vikunja.configMaps.config.data['config.yml']`:
  ```yaml
  service:
    publicurl: "https://vikunja.kub.local"
    enableregistration: false
  metrics:
    enabled: true
  log:
    level: INFO
    format: text
    http: stdout
    database: off
    events: off
  ```
- Pod annotations, so Prometheus picks it up the way it picks up every other app in this repo:
  `vikunja.controllers.main.pod.annotations` = `prometheus.io/scrape: "true"`,
  `prometheus.io/port: "3456"`, `prometheus.io/path: /api/v1/metrics`.
- `vikunja.env`:
  ```yaml
  VIKUNJA_DATABASE_TYPE: postgres
  VIKUNJA_DATABASE_HOST: postgres.databases.svc.cluster.local
  VIKUNJA_DATABASE_DATABASE: vikunja
  VIKUNJA_DATABASE_USER: vikunja
  VIKUNJA_DATABASE_PASSWORD:
    secretKeyRef:
      name: vikunja-db
      key: VIKUNJA_DATABASE_PASSWORD
  ```
- `ingressRoute:` block for our own template:
  ```yaml
  ingressRoute:
    apiVersion: traefik.containo.us/v1alpha1
    enabled: true
    host: vikunja.kub.local
    service: vikunja
    port: 3456
    apiPathPrefix: /api
    uiMiddlewares:
      - {name: lan-only, namespace: kube-system}
      - {name: authentik-forwardauth, namespace: kube-system}
    apiMiddlewares:
      - {name: lan-only, namespace: kube-system}
    tls:
      enabled: true
      options: internal-tls
      optionsNamespace: kube-system
    redirectToHttps: true
  ```

- [ ] **Step 4: Write `templates/ingressroute.yaml`**

Two `IngressRoute` objects in one file, modelled on
`cluster/helm/media-stack/tautulli/templates/ingressroute.yaml` (TLS, default certificate, redirect
router) and the `/api` rule in `cluster/helm/media-stack/bazarr/templates/ingressroute.yaml`:

1. `{{ include "vikunja.fullname" . }}` on `websecure`, `tls.options.name` and
   `tls.options.namespace` from values, **no** `secretName`, **no** `certResolver`. Two routes:
   - `Host(...)` with the UI middlewares,
   - `Host(...) && PathPrefix(...apiPathPrefix)` with `priority: 100` and the API middlewares. Keep the
     comment that explains why the forward-auth is absent here and why `lan-only` is not.
2. `{{ include "vikunja.fullname" . }}-redirect` on `web`, same host, middlewares
   `kube-system-lan-only@kubernetescrd` then `kube-system-redirect-to-https@kubernetescrd` only —
   redirecting must not send the browser through Authentik first.

Both use `metadata.namespace: {{ .Release.Namespace }}`, the two `app.kubernetes.io/*` labels, and the
Service name and port from values.

- [ ] **Step 5: Render it and prove the six things that matter**

```bash
helm dependency build cluster/helm/vikunja
helm template vikunja cluster/helm/vikunja -n vikunja > /tmp/vikunja.yaml
kubeconform -strict -summary /tmp/vikunja.yaml
```

Then check each of these, and fix the chart if any fails:

```bash
grep -c 'kind: Ingress$' /tmp/vikunja.yaml              # expect 0
grep -c 'kind: IngressRoute$' /tmp/vikunja.yaml         # expect 2
grep -A3 'VIKUNJA_DATABASE_PASSWORD' /tmp/vikunja.yaml  # secretKeyRef, never a literal
grep -n 'kubernetes.io/hostname: nuc0' /tmp/vikunja.yaml
grep -n 'strategy: Recreate' /tmp/vikunja.yaml
grep -n 'name: internal-tls' /tmp/vikunja.yaml
grep -n 'redirect-to-https' /tmp/vikunja.yaml
grep -n 'priority: 100' /tmp/vikunja.yaml
grep -n 'prometheus.io/path: /api/v1/metrics' /tmp/vikunja.yaml
grep -n 'tag: "2.8.0"' /tmp/vikunja.yaml
```

Then read the two routes back out of the rendered file and confirm with your eyes that the `priority:
100` route lists `kube-system-lan-only@kubernetescrd` and **not** `kube-system-authentik-forwardauth`,
and that the `/` route lists both. `helm template` needs the secret to exist only at runtime, so it
renders without a cluster.

If `helm dependency build` pulls `vikunja` but not its own bjw-s `common` dependency, `helm template`
fails with `common … found in Chart.yaml, but missing in charts/ directory`. That means the published
OCI artifact was packaged without its subcharts: add `common` to *our* `Chart.yaml` as a second
dependency, pinned to `1.5.1` from `https://bjw-s-labs.github.io/helm-charts`, and re-render.


- [ ] **Step 6: Write `README.md`**

One page: what the board is for, that state lives in PostgreSQL and not in the claim, why the
upstream ingress is disabled, that the route is TLS on the internal certificate with HTTP redirecting
to it, that `/` carries `lan-only` **and** the Authentik gate while `/api` carries `lan-only` only (so
a token client works and the internet still cannot reach it), that metrics are scraped from
`/api/v1/metrics`, and that the MCP endpoint requires app 2.7.0+.

- [ ] **Step 7: Write `cluster/argocd/projects/vikunja.yaml`**

Copy `cluster/argocd/projects/filebrowser.yaml` and change the name to `vikunja`, the namespace to
`vikunja`, and the path to `cluster/helm/vikunja`. Keep `CreateNamespace=true`, keep
`targetRevision: develop`, add no `syncPolicy.automated`. Write the file with CRLF endings.

- [ ] **Step 8: Sanity-check the Application reference (CRLF-safe)**

```bash
grep -h '^    path:' cluster/argocd/projects/vikunja.yaml | sed 's/^    path: *//' | tr -d "\"' \r" | while read -r p; do [ -d "$p" ] || echo "BROKEN PATH $p"; done
```

- [ ] **Step 9: Commit**

```bash
git add cluster/helm/vikunja cluster/argocd/projects/vikunja.yaml
git commit -m "🚀 feat(vikunja): wrapper chart and Application for the task board"
```

---

### Task 3: First start

**Files:**
- Create: `docs/runbooks/vikunja-first-start.md`

**Interfaces:**
- Consumes: the chart and Application from Task 2.

- [ ] **Step 1: Write the runbook**

Steps for the operator, each with the output that means success:

1. Commit lands on `develop`; wait for the Application to report `Synced`/`Healthy` in the ArgoCD UI.
2. `kubectl -n vikunja get pods` → the single pod `Running`, `1/1`.
3. `kubectl -n vikunja logs deploy/vikunja | grep -i 'connected\|migrat'` → the migration output and
   the database connection line, with no `connection refused`. Log lines are text at `INFO`.
4. Plain HTTP redirects, without stopping at Authentik first:
   ```bash
   curl -sI http://vikunja.kub.local/ | grep -i '^location\|^HTTP'
   # expect: HTTP/1.1 301 and Location: https://vikunja.kub.local/
   ```
5. The certificate is the cluster's own:
   ```bash
   openssl s_client -connect vikunja.kub.local:443 -servername vikunja.kub.local </dev/null 2>/dev/null \
     | openssl x509 -noout -subject -issuer
   # expect: subject CN=kub.local, issuer CN=ashulab internal CA
   ```
6. The API answers a machine without a session, and the UI does not:
   ```bash
   curl -sk https://vikunja.kub.local/api/v1/info | head -c 120
   # expect: JSON with a "version" of 2.8.0 or later
   curl -sk -o /dev/null -w '%{http_code} %{redirect_url}\n' https://vikunja.kub.local/
   # expect: a redirect to the Authentik sign-in, not 200
   ```
   The first call is the proof that `/api` skips the gate; the second is the proof that `/` does not.
7. Metrics are exposed and scraped:
   ```bash
   curl -sk https://vikunja.kub.local/api/v1/metrics | head -5
   # expect: HELP / TYPE lines, not an auth prompt
   ```
   Then in Prometheus: `up{job=~".*vikunja.*"}` → `1`.
8. Open `https://vikunja.kub.local`, sign in through Authentik, and complete Vikunja's first-run form:
   username, email, password. This account is the board's owner.
9. Confirm registration is closed: `curl -sk -o /dev/null -w '%{http_code}' https://vikunja.kub.local/api/v1/register`
   → `404`.
10. Off-LAN refusal: from a network without the LAN gate (or with the WireGuard tunnel down), confirm
    both `https://vikunja.kub.local/` and `https://vikunja.kub.local/api/v1/info` are refused with
    `403`. The API bypasses Authentik, not the LAN gate.
11. Rollback, if the board is ever abandoned: delete the Application, then the namespace, then drop the
    database and the role. The dump in the PostgreSQL nightly backup holds the board until then.
12. Add a row for each of the three Vikunja runbooks to the table in `docs/runbooks/README.md`:
    `vikunja-database.md`, `vikunja-first-start.md`, `vikunja-board-seed.md`. Also add the `vikunja` /
    `vikunja-db` row to the inventory table in `docs/secrets.md` (keys, consumer, never the value).

- [ ] **Step 2: Operator executes it and pastes outputs 2, 3, 4, 6, 7, 8 and 10 back**

- [ ] **Step 3: Commit**

```bash
git add docs/runbooks/vikunja-first-start.md
git commit -m "📝 docs: first-start runbook for the board"
```

---

### Task 4: Seed the boards

**Files:**
- Create: `hack/vikunja-seed.sh`
- Create: `docs/runbooks/vikunja-board-seed.md`

**Interfaces:**
- Consumes: `VIKUNJA_API_TOKEN` in the environment (an operator API token, not the agent's MCP one),
  `VIKUNJA_URL` (default `https://vikunja.kub.local`).
- Produces: for each of `homelab`, `config`, `ashuvidz-website`, `ashudev-website`,
  `selfhosted-helmcharts`, `ashuworld`, `ashuvidz-creator`: one project with one kanban view whose
  buckets are `Ready`, `Doing`, `Waiting for validation`, `Done`; plus saved filter
  `Awaiting my review`. `scripts` is not a GitHub repository and gets no board.

- [ ] **Step 1: Write the script against the live instance**

`hack/vikunja-seed.sh` is idempotent — it matches every object by title and creates only what is
missing — and prints one line per object with `created` or `existing`. It uses these endpoints, in
this order, against the API group that the instance exposes (`/api/v1` on 2.8.0):

| Purpose | Call |
|---|---|
| create project | `PUT /projects` `{"title": "<repo>"}` |
| list views | `GET /projects/<id>/views` |
| create kanban view | `PUT /projects/<id>/views` `{"title": "Board", "view_kind": "kanban"}` |
| list buckets | `GET /projects/<id>/views/<view>/buckets` |
| create bucket | `PUT /projects/<id>/views/<view>/buckets` `{"title": "Ready"}` |
| mark done bucket | `POST /projects/<id>/views/<view>` `{"done_bucket_id": <id>}` |
| create saved filter | `PUT /filters` `{"title": "Awaiting my review", "filters": {...}}` |

The saved filter body: `{"sort_by": ["created"], "order_by": ["asc"], "filter": "done = false"}`.
Bucket membership cannot be expressed in the filter, so the filter selects every open task; the
operator's real screen is the `Waiting for validation` column of each board, and the filter is the
"nothing forgotten" backstop. Say that in the runbook rather than pretending the filter is the queue.

- [ ] **Step 2: Run it against the instance**

```bash
export VIKUNJA_URL=https://vikunja.kub.local
export VIKUNJA_API_TOKEN=<operator token from Settings → API Tokens>
./hack/vikunja-seed.sh
```

Expected: six blocks of `created`, then a second run that prints `existing` for every line and
creates nothing.

- [ ] **Step 3: Prove the done bucket is wired**

```bash
./hack/vikunja-seed.sh --verify
```

`--verify` reads back each kanban view and fails unless `done_bucket_id` equals the id of the bucket
titled `Done`. This is the check that stops the board from reporting work as open that is closed.

- [ ] **Step 4: Write `docs/runbooks/vikunja-board-seed.md`**

How to run it, how to add a seventh repository later (add one line to the repo list at the top of the
script, re-run, nothing else changes), and what the saved filter does and does not cover.

- [ ] **Step 5: Commit**

```bash
git add hack/vikunja-seed.sh docs/runbooks/vikunja-board-seed.md
git commit -m "🚀 feat(vikunja): idempotent board seeding"
```

---

### Task 5: Agent access over MCP

**Files:**
- Modify: `~/.config/opencode/opencode.jsonc` (outside the repo, not committed)
- Modify: `~/.config/opencode/agents/explore.md` if it exists, otherwise the `agents` block of
  `opencode.jsonc` (outside the repo, not committed)
- Modify: `~/.config/secrets/homelab.env` (outside the repo, not committed)

**Interfaces:**
- Produces: MCP server `vikunja` reachable from OpenCode at `https://vikunja.kub.local/api/v2/mcp`,
  denied to `explore`, allowed to the primary agent, `implementer` and `reviewer`.

- [ ] **Step 1: Create the token in Vikunja**

Settings → MCP → Create a token, name `OpenCode`, preset **Typed tools read + write** (not Full
access). Copy it — it is shown once.

- [ ] **Step 2: Store it and register the server**

Add `VIKUNJA_MCP_TOKEN=<token>` to `~/.config/secrets/homelab.env`. Add to `opencode.jsonc`:

```jsonc
"mcp": {
  "servers": {
    "vikunja": {
      "type": "remote",
      "url": "https://vikunja.kub.local/api/v2/mcp",
      "oauth": false,
      "headers": { "Authorization": "Bearer {env:VIKUNJA_MCP_TOKEN}" }
    }
  }
}
```

Merge into the existing `mcp` block; do not replace other servers. The URL is `https` and under
`/api`, which is why it must not go through the Authentik gate — a bearer token cannot complete a
sign-in redirect.

- [ ] **Step 3: Deny the server to `explore`**

In the `explore` agent definition add a permission rule denying `vikunja_*` on `*`. OpenCode drops a
server's instructions from an agent's prompt when every one of its tools is denied, so `explore`
pays nothing.

- [ ] **Step 4: Verify the connection**

```bash
opencode mcp list
```

Expected: `vikunja` listed as `connected`. If it reports `needs authentication`, the header is not
being sent — check the token is in the wrapper's environment.

- [ ] **Step 5: Verify a tool call from a session**

In a session, ask for a tool call through `execute`: create a task titled `MCP smoke test` in the
`homelab` project, then read it back, then delete it. Expected: three successes, and no task left on
the board. If creation is denied, the token preset is wrong, not the URL.

---

### Task 6: The protocol

**Files:**
- Create: `docs/agent-protocol.md`
- Modify: `~/.config/opencode/AGENTS.md` (outside the repo, not committed)
- Modify: `docs/README.md` (§"Where each kind of knowledge lives")

**Interfaces:**
- Consumes: the four buckets and the saved filter from Task 4.

- [ ] **Step 1: Write `docs/agent-protocol.md`**

Contents:

1. The four states and the transition table from the spec §9.4, including who may make each move.
2. What a ticket carries: title, description fields, bucket, assignee, labels, priority (§9.3).
3. One worked example of a handover: what the agent writes when it parks a ticket in
   `Waiting for validation` — the branch, the PR link, and the verification command with its output.
4. One worked example of a refusal: the comment that says what is wrong, the ticket back in `Doing`,
   and what the agent reads first when it picks the ticket up again.
5. What an agent does when it cannot finish: leaves the ticket in `Doing` with a comment naming what
   blocks it. No fifth state exists.
6. That plan checkboxes are the plan and the board is the state, and that the board wins.

- [ ] **Step 2: Add the five lines to the global `AGENTS.md`**

Under a heading `## Task board`, these five rules, verbatim, followed by
`Long form: homelab docs/agent-protocol.md`:

```
- Every plan task is one ticket on the Vikunja board, in the project named after the repository.
- Starting a task moves its ticket to Doing and assigns it to you.
- Finishing a task moves it to Waiting for validation with the branch, the PR link and the verification output.
- Never move a ticket to Done. Never move one out of Done. That is the operator's.
- If a task cannot finish, leave it in Doing with a comment naming the blocker.
```

- [ ] **Step 3: Update `docs/README.md`**

Replace "Vikunja only, once it exists" with `Vikunja (\`vikunja.kub.local\`)`, and add one sentence
under the table: plan checkboxes record what was planned and executed; they are never consulted to
answer whether a task is done.

- [ ] **Step 4: Commit**

```bash
git add docs/agent-protocol.md docs/README.md
git commit -m "📝 docs: the operator↔agent protocol for the task board"
```

---

### Task 7: Acceptance, once, end to end

**Files:** none.

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Pick one real plan task**

Take the first unfinished task from an active plan, e.g. `docs/superpowers/plans/2026-11-10-w0-cleanup.md`
Task 1, and give it to a session as its work.

- [ ] **Step 2: Watch the agent side**

Expected: a ticket appears in the `homelab` project titled with the task name, moves to `Doing`,
carries the role it played in a label, is assigned to the account the token acts as, and lands in `Waiting for validation` with a branch name, a PR
link and pasted verification output.

- [ ] **Step 3: Refuse it once, on purpose**

The operator comments "not done: <a real reason>" and moves the ticket back to `Doing`. Expected: the
next agent session reads the comment, says what it is doing about it, and moves the ticket forward
again. If it does not read the comment, the protocol's wording is the defect — fix the wording, not
the agent.

- [ ] **Step 4: Accept it**

Operator moves the ticket to `Done`. Expected: the card shows done on the board without a second
click, and the plan's checkbox is ticked only because the work is done, not because the board said so.

- [ ] **Step 5: Record the outcome**

Append a short section to `docs/runbooks/vikunja-board-seed.md` naming the date, the task, and
anything the protocol had to say twice. Commit.
