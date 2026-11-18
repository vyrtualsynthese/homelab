# Observability load — stage 1 (metric drops) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop Prometheus collecting 66,391 series (44 % of its head) that no dashboard and no alert rule reads, without changing any panel or rule.

**Architecture:** Seven `metric_relabel_configs` drop rules in `cluster/helm/monitoring/prometheus/values.yaml`, one per scrape job, rendered into the chart's ConfigMap and reloaded by Prometheus' config-reloader. Each rule lists the families it drops so the next audit can check one line instead of re-deriving a regex.

**Tech Stack:** Helm wrapper chart (`prometheus` 29.47.2, pattern D), Prometheus 3.16.0, annotation-based scraping, ArgoCD GitOps on `develop`.

**Spec:** [`docs/superpowers/specs/2026-11-18-observability-load-review-design.md`](../specs/2026-11-18-observability-load-review-design.md)

## Global Constraints

- Every file in `cluster/argocd/**` is CRLF; `cluster/helm/**` is LF. Do not convert either.
- `prometheus.scrapeConfigs` is the only supported surface: no `ServiceMonitor`, no Operator CRD.
- No Application gains `syncPolicy.automated`. None has it and a PR that adds it is a regression.
- A dropped family must keep its `_sum` and `_count` beside it, so average latency stays derivable.
- A blank panel is a defect, not a saving: every affected panel query is executed against Prometheus after the sync.
- Chart render: `cd cluster/helm/monitoring/prometheus && helm dependency build . && helm template test . -f values.yaml`.
- Commit prefixes: `🚀` feature, `🐛` fix, `♻️` refactor, `📝` docs.

## Review Focus

- **A regex that matches nothing.** A typo in a drop regex renders fine and removes nothing; the cost is a silent no-op, and the reader believes the load is gone. Pinned by Step 3 of each task: the rendered regex is grepped and compared to the intended text, and Task 6 asserts the family count is 0 after the sync.
- **A drop that misses a `-slow` job.** `kubernetes-pods-slow` and `kubernetes-service-endpoints-slow` carry their own `metric_relabel_configs`, and a target annotated `prometheus.io/scrape_slow` is scraped only there, so the drop on the fast job would not reach it. Today no target carries that annotation; pinned by Task 4 Step 4, which fails the task if one appears.
- **Prometheus rejecting the whole config.** A malformed scrape config leaves Prometheus running its previous config and answers `reloadConfigSuccess: false`; the cluster looks healthy and collects everything. Pinned by Task 6 Step 2.
- **A family dropped that a *rule* reads.** The reader check in the spec covers the 35 current rules; a rule added between the audit and the merge would not be covered. Pinned by Task 6 Step 3, which re-reads the rule expressions before the measurement is accepted.
- **The qBittorrent exporter's own cardinality.** Dropping `qbittorrent_torrent_*` at scrape leaves the exporter computing 365 torrents per scrape for nothing. Expected: acceptable in stage 1 (the exporter is 32Mi), and named in the card as the follow-up if its CPU shows up.

---

### Task 1: Control-plane histogram buckets

**Files:**
- Modify: `cluster/helm/monitoring/prometheus/values.yaml` (`prometheus.scrapeConfigs.kubernetes-api-servers`, `...kubernetes-nodes`)

**Interfaces:**
- Consumes: nothing.
- Produces: the two jobs' `metric_relabel_configs` entries; Tasks 2–5 add the same key to other jobs and must not reorder or merge these.

- [ ] **Step 1: Extend the api-servers drop, and add one to the nodes job**

Replace the `kubernetes-api-servers` regex and append a second entry to `kubernetes-nodes`:

```yaml
    kubernetes-api-servers:
      metric_relabel_configs:
        - action: drop
          source_labels:
            - __name__
          regex: "(apiserver|etcd|workqueue|scheduler|storage_operation|kubelet|rest_client).*_bucket"
    kubernetes-nodes:
      metric_relabel_configs:
        - action: drop
          source_labels:
            - __name__
          regex: "apiserver_.*|etcd_.*|scheduler_.*|controller_.*|workqueue_.*|rest_client_.*|go_.*|process_.*|apiextensions_.*|aggregator_.*|authentication_.*|authorization_.*|field_validation_.*|watch_cache_.*|node_authorizer_.*|kubernetes_feature_enabled"
        - action: drop
          source_labels:
            - __name__
          regex: "(kubelet|storage_operation|volume_operation|kubeproxy).*_bucket"
```

Extend the existing comment above `kubernetes-api-servers` to say that the buckets are gone and only `_sum`/`_count` remain, because no panel or rule reads a bucket.

- [ ] **Step 2: Render**

Run: `cd cluster/helm/monitoring/prometheus && helm template test . -f values.yaml | grep -A3 'job_name: kubernetes-nodes$'`
Expected: the two drop entries, the second with regex `(kubelet|storage_operation|volume_operation|kubeproxy).*_bucket`, and `helm template` exits 0.

- [ ] **Step 3: Confirm the regex is the one intended**

Run: `helm template test . -f values.yaml | grep -c 'etcd|workqueue|scheduler'`
Expected: `1`. A `0` means the edit landed in the wrong job or the YAML key is misspelled.

- [ ] **Step 4: Commit**

```bash
git add cluster/helm/monitoring/prometheus/values.yaml
git commit -m "🚀 feat(monitoring): drop control-plane histogram buckets nothing reads"
```

---

### Task 2: Unused cAdvisor families

**Files:**
- Modify: `cluster/helm/monitoring/prometheus/values.yaml` (`...scrapeConfigs.kubernetes-nodes-cadvisor`)

**Interfaces:**
- Consumes: Task 1's file state.
- Produces: nothing other tasks read.

- [ ] **Step 1: Add the drop to the cadvisor job**

```yaml
    kubernetes-nodes-cadvisor:
      metric_relabel_configs:
        - action: drop
          source_labels:
            - __name__
          regex: "container_(tasks_state|memory_failures_total|memory_failcnt|memory_max_usage_bytes|memory_mapped_file|memory_swap|memory_kernel_usage|memory_cache|blkio_device_usage_total|fs_reads_total|fs_writes_total|cpu_load_average_10s|file_descriptors|processes|sockets|threads|threads_max)"
```

Comment it with the reader: the Applications board reads `container_cpu_usage_seconds_total` and `container_memory_working_set_bytes`; the other 17 families have no panel and no rule.

- [ ] **Step 2: Render and read the rule back**

Run: `cd cluster/helm/monitoring/prometheus && helm template test . -f values.yaml | grep -A4 'job_name: kubernetes-nodes-cadvisor'`
Expected: the `metric_relabel_configs` block with the 17 names; exit 0.

- [ ] **Step 3: Check the kept families are not in the regex**

Run: `helm template test . -f values.yaml | sed -n '/kubernetes-nodes-cadvisor/,/kubernetes-pods$/p' | grep 'container_(' | grep -cE 'cpu_usage|working_set|last_seen|oom_events'`
Expected: `0`. A `1` means the regex is broader than the 17 names and would drop a series a panel reads.

Then count the names it does carry: `... | grep 'container_(' | tr '|' '\n' | wc -l` → `17`.

- [ ] **Step 4: Commit**

```bash
git add cluster/helm/monitoring/prometheus/values.yaml
git commit -m "🚀 feat(monitoring): drop 17 unread cAdvisor families"
```

---

### Task 3: kube-state-metrics families

**Files:**
- Modify: `cluster/helm/monitoring/prometheus/values.yaml` (`...scrapeConfigs.kubernetes-service-endpoints`, `...kubernetes-service-endpoints-slow`)

**Interfaces:**
- Consumes: Task 1–2's file state.
- Produces: nothing other tasks read.

- [ ] **Step 1: Add the drop to the service-endpoints job and its slow twin**

```yaml
    kubernetes-service-endpoints:
      metric_relabel_configs:
        - action: drop
          source_labels:
            - __name__
          regex: "kube_secret_.*|kube_pod_tolerations|kube_replicaset_.*|kube_pod_status_qos_class"
    kubernetes-service-endpoints-slow:
      metric_relabel_configs:
        - action: drop
          source_labels:
            - __name__
          regex: "kube_secret_.*|kube_pod_tolerations|kube_replicaset_.*|kube_pod_status_qos_class"
```

Comment it: replica panels read Deployments and StatefulSets, Secrets have no reader at all, and the PVC rule reads `kubelet_volume_stats_*`, not `kube_persistentvolume_*` (which stays, for the Longhorn cleanup). The `-slow` job mirrors the fast one because a Service annotated `prometheus.io/scrape_slow` is scraped only there.

- [ ] **Step 2: Render**

Run: `cd cluster/helm/monitoring/prometheus && helm template test . -f values.yaml | grep -c 'kube_secret_'`
Expected: `2` (the two drop rules). One means the slow job is missing its copy.

- [ ] **Step 3: Confirm which targets the slow job actually scrapes**

Run: `curl -s http://prometheus.kub.local/api/v1/targets | python3 -c 'import sys,json;d=json.load(sys.stdin)["data"]["activeTargets"];print(sum(1 for t in d if "slow" in (t.get("labels",{}).get("job") or "")))'`
Expected: `0` — nothing is scraped only by a slow job today, which is why step 1's second rule saves 0 series and exists as a guard. Do not grep the raw JSON for `-slow`: the 1,086 dropped targets carry slow pool names in `discoveredLabels` and make that grep always match.

- [ ] **Step 4: Commit**

```bash
git add cluster/helm/monitoring/prometheus/values.yaml
git commit -m "🚀 feat(monitoring): drop unread kube-state-metrics families"
```

---

### Task 4: Pod-scraped families (qBittorrent, n8n, Traefik)

**Files:**
- Modify: `cluster/helm/monitoring/prometheus/values.yaml` (`...scrapeConfigs.kubernetes-pods`, `...kubernetes-pods-slow`)

**Interfaces:**
- Consumes: Task 1–3's file state.
- Produces: nothing other tasks read.

- [ ] **Step 1: Add the drop to the pods job and its slow twin**

```yaml
    kubernetes-pods:
      metric_relabel_configs:
        - action: drop
          source_labels:
            - __name__
          regex: "qbittorrent_torrent_.*|n8n_.*_bucket|traefik_.*_bucket"
    kubernetes-pods-slow:
      metric_relabel_configs:
        - action: drop
          source_labels:
            - __name__
          regex: "qbittorrent_torrent_.*|n8n_.*_bucket|traefik_.*_bucket"
```

`n8n_.*_bucket` and not one family name: n8n publishes three bucket families on 2026-11-18
(`n8n_http_request_duration_seconds_bucket` 1,610, `n8n_nodejs_gc_duration_seconds_bucket` 21,
`n8n_workflow_execution_duration_seconds_bucket` 17), so a single name leaves two behind.

Comment it: the Media board reads four `qbittorrent_global_*` totals and the exporter publishes 365 torrents' worth of series nobody charts; the n8n and Traefik histograms have no panel and no rule. The `-slow` job mirrors the fast one because a pod annotated `prometheus.io/scrape_slow` is scraped only there.

- [ ] **Step 2: Render**

Run: `cd cluster/helm/monitoring/prometheus && helm template test . -f values.yaml | grep -A4 'job_name: kubernetes-pods$'`
Expected: the drop rule with the three alternations; exit 0.

- [ ] **Step 3: Confirm the global totals survive and both jobs carry the rule**

Run: `helm template test . -f values.yaml | grep -c 'qbittorrent_torrent_'`
Expected: `2` (the two drop rules). No template may name `qbittorrent_global_`, because those panels
are the reason the exporter stays scraped.

- [ ] **Step 4: Confirm which targets the slow job actually scrapes**

Run: `curl -s http://prometheus.kub.local/api/v1/targets | python3 -c 'import sys,json;d=json.load(sys.stdin)["data"]["activeTargets"];print(sum(1 for t in d if "slow" in (t.get("labels",{}).get("job") or "")))'`
Expected: `0` — nothing is scraped only by a slow job today, so the second rule is a guard. Do not
grep the raw JSON for `-slow`: the 1,086 dropped targets carry slow pool names in `discoveredLabels`.

- [ ] **Step 5: Commit**

```bash
git add cluster/helm/monitoring/prometheus/values.yaml
git commit -m "🚀 feat(monitoring): drop per-torrent and unread exporter histograms"
```

---

### Task 5: The stack's own histogram buckets

**Files:**
- Modify: `cluster/helm/monitoring/prometheus/values.yaml` (`...scrapeConfigs.grafana`, `...loki`, `...prometheus`)

**Interfaces:**
- Consumes: Task 1–4's file state.
- Produces: nothing other tasks read.

- [ ] **Step 1: Drop `_bucket` on the three self-scrape jobs**

```yaml
    loki:
      metric_relabel_configs:
        - action: drop
          source_labels:
            - __name__
          regex: ".*_bucket"
    grafana:
      metric_relabel_configs:
        - action: drop
          source_labels:
            - __name__
          regex: ".*_bucket"
```

and the same entry under the existing `prometheus:` job (`prometheus` here is the scrape job for the server's own `/metrics`, not the chart section).

Comment it: the two stack rules read counters — `loki_request_duration_seconds_count` and
`grafana_alerting_rule_evaluation_failures_total` — and no panel reads a bucket from these jobs.

- [ ] **Step 2: Render**

Run: `cd cluster/helm/monitoring/prometheus && helm template test . -f values.yaml | grep -c 'regex: .\*_bucket'`
Expected: `3` (grafana, loki, prometheus self-scrape).

- [ ] **Step 3: Confirm the counters the rules read are not buckets**

Run: `helm template test . -f values.yaml | grep -c 'loki_request_duration_seconds_count'`
Expected: `0`. The counter is not named in any relabel rule, so it survives; if this prints `1`, the regex has grown a counter into it.

- [ ] **Step 4: Commit**

```bash
git add cluster/helm/monitoring/prometheus/values.yaml
git commit -m "🚀 feat(monitoring): drop the stack's own histogram buckets"
```

---

### Task 6: Merge, measure, and record the result

**Files:**
- Modify: `docs/reference/metrics.md` ("Self-monitoring" and the series counts)
- Modify: `docs/observability-conventions.md` (the "Measured on 2026-11-13" paragraph)

**Interfaces:**
- Consumes: all five drop rules, live on `develop`.
- Produces: the measured numbers stages 2 and 3 argue from.

**Precondition:** the pull request from Tasks 1–5 is merged, and ArgoCD has synced the `prometheus` Application. Do not run the measurement steps before both are true; a number taken from the old config is a lie in a living page.

- [ ] **Step 1: Confirm the config that is live is the config in git**

Run: `curl -s http://prometheus.kub.local/api/v1/status/config | grep -c 'kube_secret_'`
Expected: `1`, and the job it sits in is `kubernetes-service-endpoints`.

- [ ] **Step 2: Confirm Prometheus accepted it**

Run: `curl -s http://prometheus.kub.local/api/v1/status/runtimeinfo | python3 -c 'import sys,json;d=json.load(sys.stdin)["data"];print(d["reloadConfigSuccess"], d["lastConfigTime"])'`
Expected: `True` with a timestamp after the merge. `False` means Prometheus kept the old config and nothing was dropped.

- [ ] **Step 3: Re-read the rule expressions before trusting the drop**

Run the Grafana API call from the spec (`GET /api/v1/provisioning/alert-rules`) and grep the expressions for the dropped prefixes (`kube_secret_`, `kube_replicaset_`, `container_tasks_state`, `qbittorrent_torrent_`, `traefik_`, `n8n_http`).
Expected: no match. A match means a rule was added after the audit; stop and restore that family.

- [ ] **Step 4: Confirm every dropped group is empty, by query, not by reading the config**

One call per rule; each must print `0`. A group that still returns rows is a regex that matches
nothing, and the rendered config cannot tell you that — only the head can.

```bash
for expr in \
  'count({job="kubernetes-api-servers", __name__=~"(apiserver|etcd|workqueue|scheduler|storage_operation|kubelet|rest_client).*_bucket"})' \
  'count({job="kubernetes-nodes", __name__=~"(kubelet|storage_operation|volume_operation|kubeproxy).*_bucket"})' \
  'count({job="kubernetes-nodes-cadvisor", __name__=~"container_(tasks_state|memory_failures_total|memory_failcnt|memory_max_usage_bytes|memory_mapped_file|memory_swap|memory_kernel_usage|memory_cache|blkio_device_usage_total|fs_reads_total|fs_writes_total|cpu_load_average_10s|file_descriptors|processes|sockets|threads|threads_max)"})' \
  'count({__name__=~"kube_secret_.*|kube_pod_tolerations|kube_replicaset_.*|kube_pod_status_qos_class"})' \
  'count({job="kubernetes-pods", __name__=~"qbittorrent_torrent_.*|n8n_.*_bucket|traefik_.*_bucket"})' \
  'count({job=~"grafana|loki|prometheus", __name__=~".*_bucket"})' \
; do
  curl -sG http://prometheus.kub.local/api/v1/query --data-urlencode "query=$expr" \
    | python3 -c 'import sys,json;r=json.load(sys.stdin)["data"]["result"];print("EMPTY" if not r else int(float(r[0]["value"][1])))'
done
```

Expected: six lines of `EMPTY`. `EMPTY` and `0` are different answers — a query that matches no
series at all returns no result, which is the success case; a number above zero is the failure.

- [ ] **Step 5: Measure**

```bash
curl -s http://prometheus.kub.local/api/v1/query --data-urlencode 'query=prometheus_tsdb_head_series'
curl -s http://prometheus.kub.local/api/v1/query --data-urlencode 'query=sum(rate(prometheus_tsdb_head_samples_appended_total[5m]))'
curl -s http://prometheus.kub.local/api/v1/query --data-urlencode 'query=sum by (job) (scrape_samples_scraped)/60'
curl -s http://prometheus.kub.local/api/v1/query --data-urlencode 'query=quantile_over_time(0.95, container_memory_working_set_bytes{namespace="monitoring",pod=~"prometheus-server-.*"}[1h])'
```

Expected, against the 151,103 / 2,230 s⁻¹ / 1,456 MiB baseline: head series near 85,000, sample rate near 1,120 s⁻¹, and a fall in the memory figure. Record the four numbers whatever they are; a number that does not move is a finding, not a failure to hide.

- [ ] **Step 6: Confirm every panel still returns rows**

Execute the six affected expressions against Prometheus: `container_cpu_usage_seconds_total`, `container_memory_working_set_bytes`, `kubelet_volume_stats_used_bytes`, `apiserver_request_duration_seconds_sum`, `etcd_request_duration_seconds_sum`, and the four `qbittorrent_global_*`. Expected: each returns at least one row.

- [ ] **Step 7: Write the numbers into the two pages, with the date**

Update the "Measured on 2026-11-13" paragraph in `observability-conventions.md` and the series counts in `metrics.md`, and add one sentence naming what was dropped and why. Both pages are living pages: state the measurement date, not "now".

- [ ] **Step 8: Gates and handover**

```bash
hack/check-exposure.sh
while read -r p; do [ -d "$p" ] || echo "BROKEN PATH $p"; done < <(
  grep -rh '^    path:' cluster/argocd/projects | sed 's/^    path: *//' | tr -d "\"' \r")
git fetch origin && git rebase origin/develop && git push origin feature/observability-load
```

Then open the pull request against `develop`, move card #367 to `Validation` with the measurement output in its description, and stop there.
