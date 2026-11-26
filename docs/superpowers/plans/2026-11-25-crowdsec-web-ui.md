# CrowdSec Web UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run [crowdsec-web-ui](https://github.com/TheDuffman85/crowdsec-web-ui) in the `security` namespace as a second, Grafana-independent view of the LAPI's decisions and alerts, reachable only at `https://crowdsec.kub.local` behind authentik OIDC.

**Architecture:** A pattern-A chart copied from `cluster/helm/media-stack/autobrr`, one Deployment on `nuc0` with a `local-path` claim for its own SQLite, reading the LAPI over the cluster network. Sign-in is the app's own OIDC (native first per `AGENTS.md` §4); the route keeps `lan-only` and gets no forward-auth middleware. authentik gets an OAuth2 provider from a blueprint in git, so nothing is clicked in the UI.

**Tech Stack:** Helm (hand-written chart), Traefik `IngressRoute` CRD, authentik blueprints, ArgoCD Application, the repo's `hack/` checkers, pytest.

**Spec:** no separate design doc; the decision record is the conversation of 2026-11-25 and this plan.

## Global Constraints

- Chart: `cluster/helm/security/crowdsec-web-ui`; release name `crowdsec-web-ui`; namespace `security`; `nodeSelector: kubernetes.io/hostname: nuc0`.
- Image `ghcr.io/theduffman85/crowdsec-web-ui`, tag `2026.11.12`; `appVersion` `2026.11.12` (requirement 1).
- Host `crowdsec.kub.local`, `websecure` + `tls.options: internal-tls` + `kube-system-lan-only@kubernetescrd`, plus the plain-HTTP redirect mirror. No public host.
- OIDC client id `crowdsec-web-ui`; issuer `https://authentik.kub.home.ashudev.com/application/o/crowdsec-web-ui/`; callback `https://crowdsec.kub.local/api/auth/oidc/callback` — the same string in the chart and the blueprint.
- Secrets: `security/crowdsec-web-ui` with `lapi-password` and `oidc-client-secret`; `authentik/authentik-sso` gains `CROWDSEC_CLIENT_SECRET` with the same client-secret value. Created by the operator, never in git.
- LAPI `http://crowdsec-service:8080`. No metrics tab: the LAPI's `6060` is scraped on the pod IP and no Service exposes it.
- Claim `crowdsec-web-ui-config`, `local-path`, `1Gi` (it mirrors a 168h alert lookback, not just settings).
- Probes on `/api/health`; the app has no dependency-aware endpoint, so liveness and readiness share it and the README says why.

## Review Focus

1. A missing Secret shows as `CreateContainerConfigError` — the chart README and `docs/secrets.md` must name both keys.
2. An OIDC redirect mismatch is a login loop with no log line in the app; the test pins the callback in both files.
3. A machine credential that was never created leaves the pod Running and the UI empty; the runbook step must come before the first sync.
4. `LIVE_INGRESS_ROUTES` and `security-exporter`'s `expected.ingressRoutes` are live measurements; they move with this route and the note must say the live read has not caught up yet.
5. The chart must not copy `autobrr`'s names, port or claim; the test greps for leftovers.

---

### Task 1: The chart, test first

**Files:**
- Create: `hack/tests/test_crowdsec_web_ui_chart.py`
- Create: `cluster/helm/security/crowdsec-web-ui/{Chart.yaml,values.yaml,README.md}`
- Create: `cluster/helm/security/crowdsec-web-ui/templates/{_helpers.tpl,deployment.yaml,persistentvolumeclaim.yaml,service.yaml,serviceaccount.yaml,ingressroute.yaml,NOTES.txt}`

**Interfaces:**
- Produces: the values keys the other tasks read back — `image`, `env`, `extraEnv`, `storage`, `probes`, `ingressRoute`, `resources`, `nodeSelector`.
- Produces: claim name `crowdsec-web-ui-config` (used by the backup row) and host (used by the blueprint).

- [x] **Step 1: Write the failing test** — assertions: chart `appVersion` equals `image.tag`; `env` carries the LAPI URL, the OIDC issuer, client id and host; `extraEnv` carries `CONFIG_INSTANCES_0_LAPI_AUTH_PASSWORD` and `CONFIG_AUTH_OIDC_CLIENT_SECRET` as `secretKeyRef` to `crowdsec-web-ui`; the route host ends `.kub.local` with `lan-only` and `internal-tls` and no `authentik-forwardauth`; `nodeSelector` is `nuc0`, strategy `Recreate`, claim `local-path`; probes point at `/api/health`; `runAsNonRoot` true with `capabilities.drop: [ALL]`; no rolling tag; no `autobrr`, `autobrr-config` or `7474` in the chart.
- [x] **Step 2: Run it to see it fail** — `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_crowdsec_web_ui_chart.py`
- [x] **Step 3: Implement the chart** — copy `autobrr`'s templates and helpers unchanged; values built for this image: port 3000, claim at `/app/data`, uid 1000 (`node`), no ServiceAccount token, three probes, `CONFIG_AUTH_PASSWORD_LOGIN_ENABLED: "false"`, `CONFIG_AUTH_OIDC_AUTO_LOGIN: "true"`, `CONFIG_AUTH_OIDC_ADMIN_GROUPS_0: "authentik Admins"`, `CONFIG_AUTH_OIDC_UNMATCHED_ROLE: "deny"`, `TZ=Europe/Paris`. Add the no-log-level note next to `env`.
- [x] **Step 4: Run the test to see it pass**, then `helm lint`, `helm template`, `hack/check-charts.sh --only cluster/helm/security/crowdsec-web-ui`, `hack/check-app-version.sh --only …`, `hack/check-exposure.sh --only …`.
- [x] **Step 5: Commit** — `🚀 feat(crowdsec-web-ui): add the chart`.

### Task 2: GitOps wiring and the pages that count

**Files:**
- Create: `cluster/argocd/projects/security/crowdsec-web-ui.yaml` (CRLF, like its neighbours)
- Modify: `cluster/helm/backup/values.yaml` (`nuc0` claims), `hack/gen_docs.py` (`LIVE_INGRESS_ROUTES`), `cluster/helm/security/security-exporter/values.yaml` (`expected.ingressRoutes`, `expectedNote`), `docs/how-it-works/applications.md`, then regenerate `docs/reference/routes.md` and `docs/backup/coverage.md`.

- [x] **Step 1: Add the test assertions** to the same test file: the Application exists with namespace `security` and path `cluster/helm/security/crowdsec-web-ui`; the backup matrix has a `sqlite` row for `crowdsec-web-ui-config` on `nuc0`; the catalogue names `crowdsec-web-ui`.
- [x] **Step 2: Run to see them fail.**
- [x] **Step 3: Implement** the files; `LIVE_INGRESS_ROUTES` 73 → 75 and `expected.ingressRoutes` 73 → 75 with a note that 73 was read live on 2026-11-25 and the two new objects arrive at the next sync.
- [x] **Step 4: Regenerate** `python3 hack/gen_docs.py --write`, run the tests and `hack/check-docs.sh`.
- [x] **Step 5: Commit** — `🚀 feat(crowdsec-web-ui): register the Application and its backup`.

### Task 3: authentik, documented

**Files:**
- Create: `cluster/helm/authentik/files/app-crowdsec-web-ui.yaml` (OAuth2 provider + application + admin policy binding)
- Modify: `docs/secrets.md`, `docs/reference/sso-applications.md`, `docs/runbooks/authentik-apps.md` (machine credential and Secret creation)

- [x] **Step 1: Add the test assertion**: the blueprint's first redirect URI equals the chart's `OIDC_REDIRECT_URL`, its `client_id` equals the chart's client id, and it declares no proxy provider (the outpost must not list it).
- [x] **Step 2: Run to see it fail.**
- [x] **Step 3: Implement** the blueprint and the three documentation edits; the runbook gains the `cscli machines add` command and the two Secret keys.
- [x] **Step 4: Run** the test, `pytest -q hack/tests/test_authentik_blueprints.py`, and `hack/check-docs.sh`.
- [x] **Step 5: Commit** — `🚀 feat(authentik): add the CrowdSec Web UI provider`.

### Task 4: The per-app record

**Files:**
- Modify: `docs/reference/chart-notes.md`

- [x] **Step 1: Write the section** — what renders, the shared probe and why, the missing log-level knob and what carries the outcome instead, the uid-1000 claim, and that sizing is unmeasured until the first night.
- [x] **Step 2: Commit** — `📝 docs(chart-notes): record crowdsec-web-ui`.

### Task 5: Verification and handover

- [x] `hack/check-charts.sh`, `hack/check-app-version.sh`, `hack/check-exposure.sh`, `hack/check-docs.sh`, `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests` — all clean.
- [x] `git rebase origin/develop`, push the branch, open the PR against `develop` with the operator steps named.
- [x] Move the board card to `Validation` with the branch, the PR and the verification.
