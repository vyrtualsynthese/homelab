# Argo CD manifest generation failing: design

Card 644 (#451) · branch `feature/argocd-alert` · 2026-11-22

## Intent

Since card 643 the `found in Chart.yaml, but missing in charts/ directory` line is known to be
routine: the repo-server logs it once per wrapper chart per new revision (789 lines in the 24 hours to
2026-11-22), runs `helm dependency build`, and the retry succeeds. The gap it leaves is that a render
that really fails reads exactly like that line to a person reading the log, and nothing else watches.

Nothing measured Argo CD at all: `count({__name__=~"argocd_.*"})` answered 0 and no Argo CD target
existed. This change puts the three components into Prometheus and adds one rule for an Application
whose manifests cannot be generated.

## Goals

- The controller, repo-server and server are scraped by the existing annotation-based discovery.
- One Grafana rule, `ArgoAppRenderFailing`, warning, group `homelab-core` (300 s), `for: 10m`, on an
  Application carrying a `ComparisonError` — the condition Argo CD sets when it cannot render the
  Application, so it stops reporting whether it is in sync.
- The routine retry stays quiet. Measured 2026-11-22: the dependency build and the second render
  happen inside one request, so no failed request is recorded and no condition is set — the
  repo-server had logged zero non-`OK` gRPC codes since it started at 04:00.
- No data and evaluation errors report OK: the expression is empty while every Application renders.
- A temporary Application pointing at a path that does not exist goes pending → firing, and deleting
  it resolves the rule.

## Non-goals

- **A share of failed repo-server requests.** It was the card's other suggestion and it does not earn
  its noise: it cannot name the Application, and the routine line would have been its only baseline.
  The condition is the fault itself, already labelled with the Application.
- **A dashboard for the new series.** Nothing reads them yet; a board is a separate card.
- **Dex, notifications, application-set and redis metrics.** The card names three components, and the
  condition comes from the controller. Those four have no reader.
- **Fixing the chart's own network policy upstream.** The wrapper records the workaround and why.

## What is there today (measured 2026-11-22)

| Component | Endpoint | Families | Series |
|---|---|---|---|
| application controller | pod `:8082` | 180 | 887 |
| repo-server | pod `:8084` | 48 | 125 |
| server | pod `:8083` | 51 | 2,372 |

Only the server's metrics Service exists (`argocd-server-metrics`, 8083) and it carries no
annotation. The controller's and repo-server's are disabled in the chart.

`argocd_app_condition` exists only when `controller.metrics.application.conditions` lists the
condition types to publish; with no list the controller publishes no condition metric at all.

**The second blocker.** The chart's NetworkPolicies allow the `metrics` port to any namespace through
an empty `namespaceSelector` peer. This k3s does not enforce that peer: with the chart's rule as the
only one in place, a probe from a pod in `monitoring` is refused on 8082, while a rule whose peer is
an `ipBlock` is allowed — with the named `metrics` port or with the numeric one, so the port form is
not the variable (measured 2026-11-22 with temporary policies, removed afterwards). Scraping alone
would have landed three `up == 0` targets.

## How the numbers get in

- **A — the chart's own annotations (chosen).** `global.addPrometheusAnnotations: true` makes the
  chart stamp `prometheus.io/scrape` and `prometheus.io/port` on every metrics Service it renders;
  enabling `controller.metrics` and `repoServer.metrics` brings the other two Services into being.
  The existing `kubernetes-service-endpoints` job already honours that annotation, so discovery is
  the house rule — *a new exporter needs the annotation, not a chart change* — and an Argo CD
  upgrade keeps working without a second edit.
- **B — static jobs in the Prometheus chart**, like Loki, Grafana and Fluent Bit. It needs the same
  two Services enabled anyway, and it copies Argo CD's service names into another chart where
  nothing keeps them in step with an upgrade. Rejected.

The two metrics Services are headless, so their endpoints are the pod IPs on the service port — which
is why the scrape is a pod-to-pod connection and the network policy matters.

## Parts

1. `cluster/helm/argocd/values.yaml`
   - `global.addPrometheusAnnotations: true`.
   - `controller.metrics.enabled: true`, `repoServer.metrics.enabled: true` (`server.metrics` is
     already on).
   - `configs.params: controller.metrics.application.conditions: "ComparisonError"` — the switch that
     publishes `argocd_app_condition`, and the reason the rule can read the condition rather than
     infer it from `sync_status="Unknown"`.
2. The same file, under `extraObjects`, one NetworkPolicy `argocd-metrics-scrape`: ingress on numeric
   ports **8082 and 8084** from `10.42.0.0/16` (the pod network), selecting the controller and
   repo-server pods. NetworkPolicies are additive, so the chart's own rule stays as it is. A comment
   records that the chart's `namespaceSelector` rule is not enforced on this k3s (measured
   2026-11-22, with the probe that showed it) and that the numeric rule is the workaround, not a
   preference. The server needs no extra policy: its own rule allows all ingress.
3. `cluster/helm/monitoring/prometheus/values.yaml`: drop
   `argocd;(go_.*|process_.*|grpc_server_.*|argocd_kubectl_.*|workqueue_.*|controller_runtime_.*|certwatcher_.*)`
   on the `kubernetes-service-endpoints` job (and its `-slow` twin, as the media rules are repeated).
   The scrape is service-based, so the pods job is the wrong place. Raw cost is 3,384 series; the
   largest single block is not the Go runtime but the server's pre-initialised gRPC matrix —
   **1,938 series**, one per method/service pair × 17 status codes, most of them permanently zero —
   followed by the controller's work-queue and client-go instrumentation. The regex keeps the
   Application, repository, cluster, git and redis families (350 series measured) and follows the
   seven media rules and
   the comment convention.
4. The rule, created through the Grafana provisioning API and recorded in
   `docs/reference/alerts.md`:
   - `argocd_app_condition{condition="ComparisonError"} > 0` — the gauge's value is the count of such
     conditions, so the series exists only while the condition does and the threshold is "above 0".
   - `for: 10m`, `homelab-core`, `severity: warning`, `noDataState: OK`, `execErrState: OK`.
   - summary names the Application; the description says the manifests cannot be generated, that the
     Application is not reporting its sync state, names its project, and gives the log command from
     `docs/runbooks/argocd.md`.
5. Docs, in the same change:
   - `docs/reference/alerts.md`: the rule row, a paragraph on why the condition and not the log line,
     the rule counts, and the measured baseline.
   - `docs/observability-conventions.md`: how Argo CD's numbers get in, and the network-policy fact —
     named-port-plus-`namespaceSelector` rules are not enforced on this k3s, so a cross-namespace
     allow has to be numeric and `ipBlock`-based.
   - `docs/runbooks/argocd.md`: the alert is the signal; the log grep stays as the diagnosis.
   - `docs/reference/metrics.md`: the new drop rule and the measured per-target series, after the
     drop.

## What the builder settles

- The rule title (`ArgoAppRenderFailing` is the proposal) and the exact summary/description wording.
- The measured series after the drop; the 3,384 (before) and 350 (after) above were sampled from the
  live endpoints on 2026-11-22, so the page records what the scrape shows after the sync rather than
  a prediction.
- Whether the alert text should quote the condition message. It is not a label on the metric, so it
  cannot; the description points at the log instead.

## Verification

1. `hack/check-charts.sh` renders and lints the argocd chart; a template of the three metrics Services
   shows the two annotations, and the policy shows numeric ports 8082 and 8084.
2. After the merge and the sync: `count(up{namespace="argocd"} == 1)` is 3,
   `count(argocd_app_info)` is 60, and `count(argocd_app_condition)` is 0 with every Application
   rendering.
3. The rule is created through the API, reads inactive, and its expression returns an empty vector.
4. For the firing proof: a temporary Application with a `repoURL`/`path` that does not exist and **no
   syncPolicy** is applied; the rule is temporarily at `for: 1m` so the test does not wait ten
   minutes; it goes pending → firing, the Discord message names the Application, and deleting the
   Application resolves it. `for` goes back to 10m afterwards.
5. The API calls and their answers, with the series counts, are written into card 644.
