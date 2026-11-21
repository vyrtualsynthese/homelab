# Traefik stability and observability — design

**Status:** draft for approval · **Date:** 2026-11-21 · **Audited:** live cluster, same day
**Board:** homelab #13 (Traefik v3), #467 (export dashboards and rules), #531 (metrics 404, done),
#532 (dead dashboard middleware), #470 (`sslRedirect`)

## Intent

Make the Traefik install trustworthy *before* the v3 upgrade, and give it what it is missing to be
watched: a reachable dashboard, a Grafana view, alert rules, and an export of both in git. The v3
upgrade changes the metrics surface and drops config fields without an error; doing it on an
ungoverned install means finding that out in production.

## What the audit found

Full evidence in `docs/audit/traefik-2026-11-21.md`.

Sound today: chart 25.0.3 / Traefik 2.10.7, one Deployment replica on `streaming` (`Recreate`,
`pvc-traefik-nfs`), 68 legacy-group routes all ArgoCD-tracked, 59/60 Applications
`Synced/Healthy`, `/metrics` answering 200 with 270 series and `up=1`, 35 certificates,
`hack/check-exposure.sh` clean over 72 routes.

Gaps this design closes:

| # | Gap | Where |
|---|---|---|
| 1 | Dashboard is HTTP only; `https://traefik.kub.local` answers 404, the basic-auth password crosses the LAN in clear | `values.yaml` + new redirect manifest |
| 2 | No Grafana view: 0 of 10 dashboards read a `traefik_*` metric | Grafana DB |
| 3 | Thin alerting: only `CertificateExpiring` reads Traefik, nothing watches error rate, latency or config reloads | Grafana DB |
| 4 | Metrics port 9101 published on the Service although Prometheus scrapes the pod's 9100 | `values.yaml` |
| 5 | Dead and duplicate manifests: `dashboard/middleware.yaml` (names a Secret that does not exist), `common/middleware-security.yaml` (`default-security`, no reader, not live), `common/redirect-https.yaml` (duplicate of `middleware/https-redirect.yaml`), `sslRedirect` in six files | `cluster/infrastructure/traefik/`, four charts |
| 6 | No memory request or limit on the container (`resources: {}`) | `values.yaml` |
| 7 | Latency buckets are dropped by Prometheus (`traefik_.*_bucket` in the pods job), so a p95 panel is impossible as configured | `cluster/helm/monitoring/prometheus/values.yaml` |

Deferred, each with a card filed in `Backlog`: the 18 dead certificates in `acme.json`, the CRD
group rename (628 refs, #12), `ipWhiteList` → `ipAllowList` (v3 only), access logs, and ArgoCD
ownership of the release (#363).

## Goals

1. The dashboard answers on `https://traefik.kub.local`, behind basic auth and `lan-only`, with the
   HTTP port redirecting to it; Dashy's tile points at the HTTPS URL.
2. Metrics remain scraped from the pod, and stop being reachable through the load-balancer Service.
3. One Grafana dashboard, `Homelab · Traefik`, in the `Homelab` folder, reading the families the
   install already publishes.
4. Three new rules that would have caught the `/metrics` 404 of #399 and would catch a broken router
   during the upgrade.
5. The dashboard and the rules exist as JSON in git, marked as an export.
6. The repo carries no Traefik manifest that cannot be applied cleanly, and no v2 field that v3
   drops silently.
7. The container carries a memory budget sized from measurement: 63 MiB working set.

## Non-goals

- The v3 upgrade itself, and the 2.11 bump. This design makes them safe; it does not perform them.
- The CRD group rename (`traefik.containo.us` → `traefik.io`, 628 refs): mechanical, every chart, its
  own PR after this one.
- Removing the 19 orphaned certificates from `acme.json`. The store is a live file on a Retain NFS
  volume; that needs a write path and a backup taken first.
- Traefik under ArgoCD (card #363), access logs, and any change to which apps are public.
- Dashboards for the other nine views.

## Anti-goals

- A wrapper chart of our own around the Traefik chart. #363 owns that decision, and a chart owns the
  values file better than `helm` by hand does only once it is a real feature.
- Provisioning dashboards and rules from configmaps in git: the other ten dashboards and all 39 rules
  live in Grafana's database, so provisioning would create two sources of truth in one folder.
- Any change that edits the certificate store or the claim from a git manifest.

## Constraints

- `cluster/infrastructure/**` is applied by hand. The release moves with `helm upgrade` per
  `docs/runbooks/traefik-release.md`; the operator runs that step after the PR merges.
- The installed chart is K3s's repack of upstream 25.0.3 (`25.0.3+up25.0.0`); its values file is
  upstream `v25.0.0`, whose `ingressRoute.dashboard.tls` accepts a TLSOption. Confirmed upstream.
- On 2.10 the CRD has no `ipAllowList`; `lan-only` keeps `ipWhiteList` until the v3 upgrade. The
  rename is a required pre-upgrade step, written down rather than guessed at upgrade time.
- `hack/check-exposure.sh` reads the release's values for the dashboard gate; the redirect manifest
  it does not read must carry `lan-only` too, and the checker's fixture stays valid.
- No route changes behaviour: every route keeps its host, entry point and middleware set.

## Approaches considered

**A — Grafana API for the view, export JSON to git (chosen).** Build the dashboard and rules through
Grafana's HTTP API with `GRAFANA_TOKEN`, then commit `dashboard.json` and `rules.json` under
`cluster/helm/monitoring/grafana/export/traefik/` with a README saying they are a snapshot, not
provisioning. One source of truth (the database), a reviewable record in git, and it is what #364
asks for.

**B — Provision from the chart.** A configmap holding the dashboard JSON and a rules file in the
Grafana chart. True GitOps, but the other ten dashboards and 39 rules stay in the database, so the
folder would have two provenances and a UI edit would silently lose to a sync.

**C — Fix only the dashboard and metrics exposure, file the rest.** Cheapest, but leaves the upgrade
blind and #364 unfulfilled.

## Decisions

**Two pull requests, in order.**

1. `feature/traefik-audit` — configuration correctness (gaps 1, 4, 5, 6, 7). It ends with an operator
   step: one `helm upgrade` from the updated values.
2. Observability (gaps 2, 3, and the export) once the release that renders those values is live.

Reason: the first PR changes what Traefik *is* and needs a release; the second changes only Grafana
and files. Separate PRs keep the release auditable and let the dashboard be verified against a
router that has already been cleaned up. PR2 therefore starts after the operator has run
`helm upgrade` from PR1's values and confirmed the release is live — the certificates, the widened
buckets and the stored `_bucket` series all come from that release.

**PR 1 changes**

- `values.yaml`: `ingressRoute.dashboard.entryPoints: ["websecure"]` with
  `tls.options: {name: internal-tls, namespace: kube-system}`; `ports.metrics.expose: false`;
  `resources.requests: {cpu: 10m, memory: 64Mi}`, `resources.limits: {memory: 256Mi}`. Values chosen
  from measurement: 2 m CPU, 63 MiB working set, and the 256 MiB ceiling is four times the measured
  set, the same headroom Prometheus and Grafana carry.
- New `cluster/infrastructure/traefik/dashboard/redirect.yaml`: an `IngressRoute` in `kube-system`
  on `traefik.containo.us/v1alpha1` (the group rename is deferred) for `traefik.kub.local`, carrying
  `{name: lan-only, namespace: kube-system}` and `{name: redirect-to-https, namespace: kube-system}`,
  so the HTTP URL keeps answering with a `301` to `https://` (measured 2026-11-21:
  `redirectScheme.permanent: true` makes Traefik permanent-redirect with `301`). `redirect-to-https` is the `traefik-gate` object the 18
  other redirect routers already name; `redirect-https` stays for the charts that name it.
- Delete `dashboard/middleware.yaml` (the `#532` dead file), `common/middleware-security.yaml`
  (`default-security`, no reader, never applied) and `common/redirect-https.yaml` (same object as
  `middleware/https-redirect.yaml`, which stays).
- Drop `sslRedirect: true` from `middleware/security.yaml` and the four chart templates; every route
  that uses `security` already has a redirect router.
- `cluster/helm/dashy/files/conf.yml`: the Traefik tile points at `https://traefik.kub.local/dashboard/`.
- `cluster/helm/monitoring/prometheus/values.yaml`: remove `traefik_.*_bucket` from the pods job's
  drop regex **in both `kubernetes-pods` and `kubernetes-pods-slow`**, which carry the same list, so
  the latency histogram survives. Cost measured: 336 bucket series once the buckets widen, +0.4 % of
  the 84,747 the head holds today. `docs/reference/metrics.md`'s drop table row moves with it.
- `values.yaml` `metrics.prometheus.buckets` widened to `0.1,0.25,0.5,1.0,2.5,5.0,10.0` so a p95
  panel is not saturating its last finite bucket. The 336 above assumes this widening: 42
  router/code/method/protocol combinations × 8 `le` values on the wire today.
- Docs: the trap table in `docs/runbooks/traefik-release.md` gains the traps this run found
  (dashboard served on `web` only, metrics published on 9101, orphans keep renewing), and its
  after-check line "dashboard 401" becomes "http 301 → https 401" with the HTTPS URL named;
  `AGENTS.md` §4's internal-TLS paragraph gains the dashboard as an example;
  `docs/reference/routes.md` names it.
- Checks: `helm template` against the chart already extracted at `/tmp/traefik-25.0.3/traefik`
  (re-extracted per the runbook when absent), `hack/check-exposure.sh`,
  `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests`.

**PR 2 changes**

- Dashboard `Homelab · Traefik`, uid `homelab-traefik`, folder `Homelab`, refresh 30 s, default
  6 h. Panels, in reading order: requests per second by router (top 10); response class mix
  (2xx/3xx/4xx/5xx); error ratio by router; p95 and p99 latency by router; bytes in and out;
  open connections by router; certificate expiry table (days left and `sans`, sorted by days, with a
  panel description saying the 18 orphans are the deferred cleanup and that a name disappearing is
  the cleanup landing); config reloads and last
  successful reload; scrape target up, with a link to the native dashboard at
  `https://traefik.kub.local/dashboard/`.
- Rules, in group `homelab-core` (300 s evaluation, as `docs/reference/alerts.md` fixes it; every
  rule there uses a `for` of 10 m or more, so the slow cadence costs detection time and no accuracy),
  severity `warning`, routed by the existing policy to `discord-homelab`:
  - `TraefikRouterErrorRate` — 5xx share above 10 % for 10 min, per router, with a request floor so a
    single failed probe on an idle router does not page.
  - `TraefikRouterLatencyHigh` — p95 above 1 s for 10 min, per router. Measured today: the busiest
    router averages 76 ms, so 1 s is an order of magnitude above normal; the threshold is re-tuned
    after a week of the widened buckets, which is when a real p95 first exists.
  - `TraefikConfigReloadFailing` — any increase of `traefik_config_reloads_failure_total` over 15 min,
    or no successful reload for 24 h. This is the rule that would have spoken during #531.
- Export: `dashboard.json` (the exact API payload) and `rules.json` (the group as returned by
  `/api/v1/provisioning/alert-rules`), plus a README stating they are a snapshot taken 2026-11-21 and
  that the database is authoritative.
- Docs: `docs/reference/dashboards.md` becomes eleven screens and its "not in git" line gains the
  export as the exception; `docs/reference/alerts.md` moves 39 rules and 20+19 per group to 42 and
  23+19, and gains the three rows with their thresholds;
  `docs/reference/metrics.md`'s drop table loses `traefik_*_bucket` and its count.
  `docs/observability-conventions.md` is left alone: it carries no per-dashboard hole list.

**What is left to the builder.** Panel layout inside the shape above; the exact PromQL for the
request floor and the certificate ordering; the wording of the rule annotations (they are read in
Discord at 3 a.m., so they say what to check and where); and the audit page itself,
`docs/audit/traefik-2026-11-21.md`, written from the evidence already gathered.

## Open questions for the operator

- One: **when PR1's release goes live.** PR2 is blocked on it. If the `helm upgrade` cannot happen in
  this session, this session ends with PR1 merged and PR2 opened later against the live release.
