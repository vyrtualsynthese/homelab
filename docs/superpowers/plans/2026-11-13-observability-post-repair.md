# Observability after the media chart repairs — follow-up plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the four findings from the 2026-11-13 observability inspection that are not already fixed: the unverified `streaming` temperature alert, the missing workload-down alert for `media-stack`, the qBittorrent DNS gap that breaks the cross-seed hook, and the app-side misconfigurations the rebuilt charts expose.

**Architecture:** Grafana owns the rules and silences (nothing in git). The two chart-side changes stay in `cluster/helm/media-stack/qbittorrent`. The repaired apps are configured through their own UIs; those steps are marked as operator actions and are not executable by an agent.

**Tech Stack:** Grafana 13 unified alerting HTTP API, Prometheus 3.16, Loki 3.7, Helm 4.2.4, qBittorrent behind WireGuard.

**Spec:** `docs/observability-conventions.md` (live facts and rules) plus the inspection evidence in this plan.

**Status:** executed 2026-11-13. Tasks 1–3 and 5 are complete; Task 4 is
operator-pending. The checkboxes stay unticked because they record the plan as
written, not its execution state.

## Global Constraints

- Never `kubectl apply` a dashboard or a rule. Use the Grafana HTTP API with `$GRAFANA_TOKEN`.
- Never silence `NodeTemperatureHigh` before the sensor is identified. It is the only unsilenced signal today.
- Rules must return a **positive value when firing**; threshold is "above 0".
- `noDataState: OK` and `execErrState: OK` for ordinary app rules; only the monitoring-stack rules use `Alerting`.
- Warning repeats every 12 h; critical every 1 h. Grouping is `[alertname, instance]`.
- `media-stack` work is pinned to `streaming`; every workload sets a memory limit and no CPU limit.
- Commit prefixes: `🐛 fix:`, `🚀 feat:`, `📝 docs:`, `🔧 chore:`.
- Grafana rules and dashboards are not backed up; every change must be re-read after writing.

## Review Focus

1. **`readarr` and `kavita` have zero available replicas on purpose** (unrepaired, Pending claims). A workload-down rule that ignores this fires two false warnings every 12 h. Exclude them by name until their claims are rebuilt.
2. **qBittorrent's `dnsPolicy: None` is deliberate** to keep DNS inside the VPN. Adding the cluster DNS would resolve `cross-seed` but send tracker lookups through the node resolver. Prefer a `hostAliases` entry over changing the resolver.
3. **Bazarr's path mapping must be fixed in the app, not the chart.** The chart mount `/media` is correct; the mapping that turns `/movies/...` into `/media/media/...` is not.
4. **Maintainerr's warnings may describe a real library mismatch, not a log-level bug.** Do not lower the level to hide them; confirm whether the unmatched titles are meant to be outside Sonarr/Radarr first.
5. **Memory headroom is measured, not guessed.** `bazarr-anime` peaked at 88 % and `lidarr` at 90 % of their limits in 24 h. Raise only after re-measuring, and by the smallest step.

---

### Task 1: Identify the `streaming` temperature sensor and settle `NodeTemperatureHigh`

**Files:**
- No repo file unless the rule changes; then only Grafana state and `docs/observability-conventions.md`.

**Interfaces:**
- Consumes: `node_hwmon_temp_celsius{node="streaming"}` and `node_thermal_zone_temp{node="streaming"}`.
- Produces: a decision recorded in `docs/observability-conventions.md`; either the existing rule stays or its expression changes.

- [ ] **Step 1: Re-read the current evidence**

```bash
curl -sG http://prometheus.kub.local/api/v1/query \
  --data-urlencode 'query=node_hwmon_temp_celsius{node="streaming"}'
curl -sG http://prometheus.kub.local/api/v1/query \
  --data-urlencode 'query=node_thermal_zone_temp{node="streaming"}'
curl -sG http://prometheus.kub.local/api/v1/query \
  --data-urlencode 'query=100-(avg(rate(node_cpu_seconds_total{node="streaming",mode="idle"}[10m]))*100)'
```

Expected: three hwmon zones at 86–91 °C (`pci0000:00_0000:00:18_3` temp1/temp3, `wmi_bus…` temp3), thermal zones at 16.8 °C and 0, CPU around 20 %.

- [ ] **Step 2: Identify the real sensors on the host (operator)**

Run on `streaming`:

```bash
sensors
for z in /sys/class/thermal/thermal_zone*; do echo "$z $(cat $z/type) $(cat $z/temp)"; done
```

Expected: `coretemp-isa-0000` or `k10temp-pci-00c3` with a package temperature, or no CPU sensor at all. Write down which chip and sensor the package value comes from.

- [ ] **Step 3: Decide, then record it**

- If the package sensor reads 80 °C or more at idle-ish load: the ACPI zones are telling the truth. Keep the rule, open a hardware task (fans, airflow, paste), and add one line to "Current health facts" saying the alert is real.
- If the package sensor is normal (under ~60 °C) and only the ACPI/WMI zones read 90: the rule is watching a non-CPU zone. Change the expression to the same shape with an exclusion, for example
  `max by (host) (label_replace(node_hwmon_temp_celsius{chip!~".*nct6775.*|pci0000:00_0000:00:18_3|wmi_bus_.*"}, ...)) > 80`,
  or move to `node_thermal_zone_temp` if that is the sensor the doc says to use.

- [ ] **Step 4: Verify the rule state**

```bash
curl -s -H "Authorization: Bearer $GRAFANA_TOKEN" \
  'http://grafana.kub.local/api/alertmanager/grafana/api/v2/alerts?active=true&silenced=true' \
  | python3 -c 'import sys,json;[print(a["labels"]["alertname"],a["status"]["state"]) for a in json.load(sys.stdin)]'
```

Expected after the fix: `NodeTemperatureHigh` is `inactive` when the real sensor is under 80 °C, or still firing with a message naming the real sensor. No silence.

- [ ] **Step 5: Commit the documentation line**

```bash
git add docs/observability-conventions.md
git commit -m "📝 docs(monitoring): record the streaming temperature sensor decision"
```

---

### Task 2: Add a workload-down alert for `media-stack`

**Files:**
- No repo file; Grafana rule group `homelab/homelab-core`.
- Modify: `docs/observability-conventions.md` (rule table and gap wording).

**Interfaces:**
- Consumes: `kube_deployment_status_replicas_available`.
- Produces: rule `MediaDeploymentDown`, warning, `for: 10m`, excluding `readarr` and `kavita`.

- [ ] **Step 1: Prove the expression on an unrepaired deployment**

```bash
curl -sG http://prometheus.kub.local/api/v1/query \
  --data-urlencode 'query=1 - kube_deployment_status_replicas_available{namespace="media-stack",deployment="readarr"}'
```

Expected: `1` (zero available). This is the value that must fire.

- [ ] **Step 2: Create a temporary firing rule**

Read the group, append a rule cloned from `RestartStorm` (`uid dg43j29hq835sd`) with:

- title `MediaDeploymentDown`
- expression `1 - kube_deployment_status_replicas_available{namespace="media-stack",deployment="readarr"}`
- `for: 1m`, severity `warning`, `noDataState: OK`, `execErrState: OK`
- summary `{{ $labels.namespace }}/{{ $labels.deployment }} has no available replicas`

Write the whole group back:

```bash
curl -s -H "Authorization: Bearer $GRAFANA_TOKEN" \
  'http://grafana.kub.local/api/v1/provisioning/folder/homelab/rule-groups/homelab-core'
# append the rule, then PUT the group back to the same URL
```

Expected after ~2 minutes: one firing instance naming `media-stack/readarr`.

- [ ] **Step 3: Widen the expression and confirm the false positive goes away**

Set the expression to:

```
1 - kube_deployment_status_replicas_available{namespace="media-stack",deployment!~"readarr|kavita"}
```

Expected: the readarr instance resolves; the rule state is `inactive` with every repaired app at `1` available.

- [ ] **Step 4: Record the rule**

Add the row to the alert table in `docs/observability-conventions.md`:

`| `MediaDeploymentDown` | a media-stack Deployment at 0 available (readarr/kavita excluded until repaired) | 10m | warning | inactive |`

and remove the sentence in Known gaps that says a broken app in another namespace never gets a replica check.

- [ ] **Step 5: Commit**

```bash
git add docs/observability-conventions.md
git commit -m "📝 docs(monitoring): add the media deployment-down rule"
```

---

### Task 3: Let qBittorrent resolve `cross-seed` without changing its DNS policy

**Files:**
- Modify: `cluster/helm/media-stack/qbittorrent/values.yaml` (also where the
  ClusterIP maintenance note lives; this chart has no README)
- Modify: `cluster/helm/media-stack/qbittorrent/templates/deployment.yaml`

**Interfaces:**
- Consumes: the `cross-seed` Service ClusterIP (currently `10.43.104.9`).
- Produces: `hostAliases` on the qBittorrent pod; no change to `dnsPolicy` or `dnsConfig`.

- [ ] **Step 1: Confirm the hook and the service IP**

```bash
curl -sG http://prometheus.kub.local/api/v1/query \
  --data-urlencode 'query=kube_service_info{namespace="media-stack",service="cross-seed"}'
```

Expected: `10.43.104.9`. The qBittorrent completion hook is configured in the app under Tools → Options → Downloads and currently targets `http://cross-seed:2468/...`.

- [ ] **Step 2: Add the alias to the chart**

In `values.yaml`:

```yaml
# The completion hook calls cross-seed by name. The pod runs with
# dnsPolicy: None and public resolvers on purpose, so cluster names do not
# resolve; this alias maps the one name the hook needs without changing DNS.
crossSeedServiceIP: "10.43.104.9"
```

In `deployment.yaml`, under `spec:` next to `dnsConfig:`:

```yaml
hostAliases:
  - ip: {{ .Values.crossSeedServiceIP | quote }}
    hostnames:
      - cross-seed
```

- [ ] **Step 3: Render and lint**

```bash
helm template qbittorrent cluster/helm/media-stack/qbittorrent -f cluster/helm/media-stack/qbittorrent/values.yaml | grep -A3 hostAliases
helm lint cluster/helm/media-stack/qbittorrent
```

Expected: the alias renders once, with the IP, and lint passes.

- [ ] **Step 4: After the ArgoCD sync, confirm the log line is gone**

```bash
curl -sG http://loki.kub.local/loki/api/v1/query \
  --data-urlencode 'query=sum(count_over_time({job="fluent-bit",namespace="media-stack",container="qbittorrent"} |= "Could not resolve host: cross-seed" [1h]))'
```

Expected: no result an hour after a completed download. If the Service is ever recreated with a new ClusterIP, update `crossSeedServiceIP` — the note sits next to the value in `values.yaml`.

**Post-merge gate:** after the ArgoCD sync, confirm the qBittorrent pod was recreated (a `hostAliases` change only applies to a new pod) and run the Loki query above after the next completed download. Do not close the follow-up PR before this passes.

- [ ] **Step 5: Commit**

```bash
git add cluster/helm/media-stack/qbittorrent
git commit -m "🐛 fix(qbittorrent): let the completion hook resolve cross-seed"
```

---

### Task 4: Correct the repaired apps' own configuration (operator)

**Files:** none. These are app settings on the live cluster; an agent cannot do them.

- [ ] **Step 1: Bazarr path mappings**

In Bazarr → Settings → Sonarr and Radarr → Path Mappings, map the library root to the container path the same claim is mounted at: `/movies` → `/media` (the chart mounts `pvc-plex-media` at `/media`; Radarr sees the movies at `/movies/movies`, Bazarr must see `/media/movies`).

Acceptance: after a manual subtitle search on one movie, no new `Path does not exist ... /media/media` line appears in Loki for 1 h. Repeat the check for `bazarr-anime` if it carries the same mapping.

- [ ] **Step 2: Lidarr's download client**

In Lidarr → Settings → Download Clients, point the qBittorrent client at `qbittorrent:8080` with the current WebUI credentials (`service.yaml` exposes 8080; the `service.port: 32400` value in `values.yaml` is dead config).

Acceptance: no new `DownloadClientException: Failed to connect to qBittorrent` in Loki for 1 h, and a manual import of one track succeeds.

- [ ] **Step 3: Maintainerr matching**

First decide whether the unmatched titles are meant to be outside Sonarr/Radarr. If they should match, fix the Radarr/Sonarr library mapping in Maintainerr; the warning is `None of the resolved external IDs ... matched a series in Sonarr` and repeats once per item per scan.

Acceptance: the warning count per scan drops from ~7 900 to the number of genuinely unmanaged titles, or the scan stops warning about them. Do not lower `LOG_LEVEL` to hide it.

- [ ] **Step 4: Bazarr-anime AniDB credentials**

Either enter the AniDB credentials in Bazarr's settings or disable the AniDB provider. Acceptance: the `AniDB API credentials are not fully set up` warning disappears from the next scan.

- [ ] **Step 5: Re-measure the two memory peaks**

```bash
curl -sG http://prometheus.kub.local/api/v1/query \
  --data-urlencode 'query=max_over_time(container_memory_working_set_bytes{namespace="media-stack",container=~"bazarr-anime|lidarr"}[24h])'
```

`bazarr-anime` peaked at 679 MiB / 768 MiB and `lidarr` at 923 MiB / 1 GiB. If they stay above 85 % for a week, raise each limit by one step (e.g. 768 → 896 MiB, 1 GiB → 1.25 GiB) and record the new numbers in the chart README.

---

### Task 5: Final documentation pass

- [ ] **Step 1: Check the conventions file against the live state**

```bash
curl -s -H "Authorization: Bearer $GRAFANA_TOKEN" \
  'http://grafana.kub.local/api/v1/provisioning/alert-rules' | python3 -c 'import sys,json;print(len(json.load(sys.stdin)))'
curl -s -H "Authorization: Bearer $GRAFANA_TOKEN" \
  'http://grafana.kub.local/api/alertmanager/grafana/api/v2/silences' | python3 -c 'import sys,json;[print(s["id"],s["comment"][:70]) for s in json.load(sys.stdin)]'
```

Expected: 28 rules (27 + `MediaDeploymentDown`), two active silences (the
ghostfolio/wiki/monitorss apps and the `nas /dev/sdf` drive), no silence on
`NodeTemperatureHigh`. The media-stack silence expired with the
`qbittorrent-exporter` recovery on 2026-11-13.

- [ ] **Step 2: Commit**

```bash
git add docs/observability-conventions.md
git commit -m "📝 docs(monitoring): close the post-repair observability follow-up"
```
