# Cloud services observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Probe the public cloud names and their DNS records from inside the cluster, alert on failures through the existing Grafana route, and set OVHcloud billing alerts at €20 and €50.

**Architecture:** A pinned wrapper chart runs `blackbox_exporter` on `nas` with its own public resolvers. Two static Prometheus jobs feed it targets: HTTP probes for the three public names, DNS probes against every authoritative server of both zones. Three Grafana rules and one dashboard read those series. Billing alerts are created in OVHcloud through its API; nothing about the money side runs in the cluster.

**Tech Stack:** Helm (wrapper chart, pattern D), Prometheus 3.16, Grafana 13 unified alerting, blackbox exporter v0.29.1, OVHcloud API (`ovhcloud` CLI).

**Spec:** `docs/superpowers/specs/2026-11-20-cloud-services-observability-design.md`

## Global Constraints

- Upstream chart `prometheus-community/prometheus-blackbox-exporter` pinned at **11.21.3**; image `quay.io/prometheus/blackbox-exporter:v0.29.1`.
- Every file under `cluster/argocd/**` uses **CRLF**; strip/restore `\r` when scripting.
- **Never** add `syncPolicy.automated` to an Application.
- Dashboards and alert rules are **not** committed: they live in Grafana and are written through its API. Dashboard JSON is staged under `/tmp/opencode/`.
- YAML: 2 spaces, lowercase keys. Commits: Gitmoji prefix.
- No `kubectl` on this workstation: reads go through the Kubernetes MCP, writes through ArgoCD/APIs. `hack/check-exposure.sh` has no new route to check but must still pass.
- Placement: `nodeSelector: kubernetes.io/hostname: nas`. Resources: request `10m` CPU / `32Mi` memory, limit `64Mi` memory, **no CPU limit**.
- Never commit a secret. `~/ovh.conf` and `~/.config/secrets/*.env` are read, never copied.
- Grafana endpoint `https://grafana.kub.local`, token `$GRAFANA_TOKEN` from `~/.config/secrets/homelab.env`; Prometheus `http://prometheus.kub.local`; folder UID `homelab`, datasource UID `PBFA97CFB590B2093`.

## Review Focus

1. **The prober resolves through the cluster resolver.** Then `ashudev.com` → `192.168.1.46` → Traefik → TLS name mismatch, and every probe passes a lie. Pinned by Task 1 (rendered `dnsPolicy: None` + public nameservers) and Task 4 (live probe returns 200).
2. **A DNS regex that never matches because TXT answers are quoted on the wire.** Pinned by Task 4: each DNS module must return `probe_success 1` live, not only a rendered config.
3. **`__param_module` ignored because the job also sets `params.module`.** All DNS probes would then run one module and pass. Pinned by Task 2 (no `params.module` in the DNS job) and Task 4 (`probe_success` per `check` label, and `probe_dns_answer_rrs > 0` for MX).
4. **A rule expression that returns 0 when true** (the repo's `up == 0` bug). Pinned by Task 5 (expressions return a positive number) and the silenced clone test.
5. **The NAT hairpin does not work from a pod**, so `ashuvidz.com` alerts from day one. Pinned by Task 4's live probe of that endpoint; if it fails while `curl` from the LAN succeeds, the target is dropped and the reason recorded, not silenced.

---

### Task 1: Blackbox exporter chart and Application

**Files:**
- Create: `cluster/helm/monitoring/blackbox-exporter/Chart.yaml`
- Create: `cluster/helm/monitoring/blackbox-exporter/values.yaml`
- Create: `cluster/argocd/projects/monitoring/blackbox-exporter.yaml`

**Interfaces:**
- Consumes: nothing.
- Produces: Service `blackbox-exporter.monitoring:9115`; pods annotated for the `kubernetes-pods` scrape job; module names `http_2xx`, `dns_soa_ashudev`, `dns_a_ashudev`, `dns_www_ashudev`, `dns_mx_ashudev`, `dns_spf_ashudev`, `dns_dkim_ashudev`, `dns_dmarc_ashudev`, `dns_ns_ashudev`, `dns_soa_ashuvidz`, `dns_a_ashuvidz`, `dns_ns_ashuvidz`.

- [ ] **Step 1: Write the chart metadata**

`Chart.yaml`: `apiVersion: v2`, `name: blackbox-exporter`, `description: Blackbox probes for the public cloud services`, `type: application`, `version: 0.1.0`, `appVersion: v0.29.1`, and one dependency:

```yaml
dependencies:
  - name: prometheus-blackbox-exporter
    alias: blackbox
    version: 11.21.3
    repository: https://prometheus-community.github.io/helm-charts
```

The alias matters: it puts the dependency values under `blackbox:` instead of `prometheus-blackbox-exporter:`.

- [ ] **Step 2: Build the dependency and confirm the values path**

Run: `helm dependency build cluster/helm/monitoring/blackbox-exporter`
Expected: `Saving 1 charts` and `Deploying "blackbox"`. `charts/` and `Chart.lock` are already gitignored for `monitoring/*`.

- [ ] **Step 3: Write `values.yaml`**

Top level: `fullnameOverride: blackbox-exporter`, `kind: Deployment`, `replicaCount: 1`, `nodeSelector: {kubernetes.io/hostname: nas}`, resources as in Global Constraints, `dnsPolicy: None`, `dnsConfig` with `nameservers: ["1.1.1.1", "9.9.9.9"]`, `searches: []`, `options: [{name: ndots, value: "1"}]`, `podAnnotations` with `prometheus.io/scrape: "true"` and `prometheus.io/port: "9115"`, `service: {port: 9115}`, `image: {registry: quay.io, repository: prometheus/blackbox-exporter, tag: v0.29.1}`, and all `blackbox.serviceMonitor.*` left at their disabled defaults.

Under `blackbox.config.modules`:

- `http_2xx`: `prober: http`, `timeout: 10s`, `http.follow_redirects: true`, `http.valid_status_codes: [200]`, `http.preferred_ip_protocol: ip4`, `http.ip_protocol_fallback: false`.
- DNS modules: `prober: dns`, `timeout: 10s`, `dns.preferred_ip_protocol: ip4`, and per module the query and assertion below (`fail_if_none_matches_regexp` under `dns.validate_answer_rrs`):

| Module | `query_name` | `query_type` | regex |
|---|---|---|---|
| `dns_soa_ashudev` | `ashudev.com` | `SOA` | none (liveness) |
| `dns_a_ashudev` | `ashudev.com` | `A` | `185\.199\.1` |
| `dns_www_ashudev` | `www.ashudev.com` | `CNAME` | `vyrtualsynthese\.github\.io` |
| `dns_mx_ashudev` | `ashudev.com` | `MX` | `(aspmx\|alt[0-9]\.aspmx)\.l\.google\.com` |
| `dns_spf_ashudev` | `ashudev.com` | `TXT` | `_spf\.google\.com` |
| `dns_dkim_ashudev` | `google._domainkey.ashudev.com` | `TXT` | `v=DKIM1; k=rsa` |
| `dns_dmarc_ashudev` | `_dmarc.ashudev.com` | `TXT` | `v=DMARC1` |
| `dns_ns_ashudev` | `ashudev.com` | `NS` | `ovh\.net` |
| `dns_soa_ashuvidz` | `ashuvidz.com` | `SOA` | none |
| `dns_a_ashuvidz` | `ashuvidz.com` | `A` | `80\.14\.57\.209` |
| `dns_ns_ashuvidz` | `ashuvidz.com` | `NS` | none (liveness only, deliberately) |

- [ ] **Step 4: Render and assert the two decisions that can silently break**

```bash
helm template blackbox cluster/helm/monitoring/blackbox-exporter \
  -f cluster/helm/monitoring/blackbox-exporter/values.yaml \
  > /tmp/opencode/blackbox-rendered.yaml
grep -A6 'dnsPolicy' /tmp/opencode/blackbox-rendered.yaml
grep -E 'name: blackbox-exporter$|image: .*v0.29.1|memory: 64Mi' /tmp/opencode/blackbox-rendered.yaml
```

Expected: `dnsPolicy: None`, both nameserver addresses, Service `blackbox-exporter` on port 9115, image tag `v0.29.1`, memory limit `64Mi`. A `dnsPolicy: ClusterFirst` here is a failed task: it reintroduces the Pi-hole rewrite.

- [ ] **Step 5: Write the Application**

Copy `cluster/argocd/projects/monitoring/grafana.yaml` and change `metadata.name` to `blackbox-exporter` and `path` to `cluster/helm/monitoring/blackbox-exporter`. Keep CRLF, keep the namespace `monitoring`, keep `CreateNamespace=true`, add no `automated`.

- [ ] **Step 6: Run the repo checks**

Run the chart sweep from `AGENTS.md` §6 (skip charts needing `helm dependency build`, which now includes this one only when `charts/` was removed) and both path checks. Expected: no `FAIL`, no `BROKEN PATH`, no `BROKEN VALUES`.

- [ ] **Step 7: Commit**

```bash
git add cluster/helm/monitoring/blackbox-exporter cluster/argocd/projects/monitoring/blackbox-exporter.yaml
git commit -m "🚀 feat(monitoring): add a blackbox prober for the public cloud names"
```

---

### Task 2: Prometheus scrape jobs

**Files:**
- Modify: `cluster/helm/monitoring/prometheus/values.yaml` (inside `prometheus.scrapeConfigs`)

**Interfaces:**
- Consumes: Service `blackbox-exporter.monitoring:9115` and the module names from Task 1.
- Produces: series `probe_success{job="blackbox-http"|"blackbox-dns"}` with labels `instance`, `check`, `zone`; `probe_ssl_earliest_cert_expiry{job="blackbox-http"}`; `probe_duration_seconds{job="blackbox-http"}`; `probe_dns_answer_rrs{job="blackbox-dns"}`.

- [ ] **Step 1: Add the HTTP job**

Under `prometheus.scrapeConfigs`, add `blackbox-http` with `enabled: true`, `metrics_path: /probe`, `scrape_interval: 1m`, `static_configs` for the three targets (`https://ashudev.com`, `https://www.ashudev.com`, `https://ashuvidz.com`, each with `labels: {check: http}`), and these relabel rules:

```yaml
relabel_configs:
  - source_labels: [__address__]
    target_label: __param_target
  - source_labels: [__param_target]
    target_label: instance
  - target_label: __address__
    replacement: blackbox-exporter.monitoring.svc:9115
params:
  module: [http_2xx]
```

- [ ] **Step 2: Add the DNS job**

Add `blackbox-dns` with `metrics_path: /probe`, `scrape_interval: 5m`, the same three relabel rules, and **no** `params:` block. Each target carries `labels: {module: <module>, check: <short name>, zone: <zone>}` and one extra relabel rule copies the label to the URL parameter:

```yaml
  - source_labels: [module]
    target_label: __param_module
```

Targets, every authoritative server of each zone:

| Server | Modules | `check` |
|---|---|---|
| `dns13.ovh.net:53`, `ns13.ovh.net:53` | the eight `dns_*_ashudev` | `soa`, `a`, `www`, `mx`, `spf`, `dkim`, `dmarc`, `ns` |
| `ns-1029.awsdns-00.org:53`, `ns-1714.awsdns-22.co.uk:53`, `ns-339.awsdns-42.com:53`, `ns-901.awsdns-48.net:53` | the three `dns_*_ashuvidz` | `soa`, `a`, `ns` |

That is 16 + 12 = 28 targets. Write them explicitly, not with a Helm range: the target list is the inventory and must be greppable.

- [ ] **Step 3: Render and prove the module parameter is not duplicated**

```bash
helm dependency build cluster/helm/monitoring/prometheus
helm template prometheus cluster/helm/monitoring/prometheus \
  -f cluster/helm/monitoring/prometheus/values.yaml \
  > /tmp/opencode/prometheus-rendered.yaml
grep -A80 'job_name: blackbox-dns' /tmp/opencode/prometheus-rendered.yaml | head -60
```

Expected: `job_name: blackbox-dns` contains the four relabel rules, contains `__param_module`, and contains **no** `params:` block. A `params: {module: ...}` in this job is a failed task: it would override the label and run one query against every server. Also confirm `job_name: blackbox-http` has `params: {module: [http_2xx]}` and no `__param_module`.

- [ ] **Step 4: Run the chart sweep again**

Same loop as Task 1, Step 6. Expected: no `FAIL`.

- [ ] **Step 5: Commit**

```bash
git add cluster/helm/monitoring/prometheus/values.yaml
git commit -m "🚀 feat(monitoring): scrape the blackbox prober for cloud names and DNS"
```

---

### Task 3: OVHcloud billing alerts

**Files:** none (account configuration; record the IDs in the commit message body of Task 7, not in a file).

**Interfaces:**
- Consumes: `~/ovh.conf` (or the same trio in `~/.config/secrets/keys.env`) and project `a93a624a514243a6a557863c90a5379e`.
- Produces: two alert configurations, `all-20` and `all-50`.

- [ ] **Step 1: Confirm nothing exists yet**

Run: `ovhcloud cloud alerting list --cloud-project a93a624a514243a6a557863c90a5379e -o json`
Expected: `null`. If a configuration exists, stop and report its content instead of creating a duplicate.

- [ ] **Step 2: Create the two configurations**

```bash
ovhcloud cloud alerting create --cloud-project a93a624a514243a6a557863c90a5379e \
  --name all-20 --service all --monthly-threshold 20 \
  --emails valentin.ronteix@ashudev.com --delay 3600
ovhcloud cloud alerting create --cloud-project a93a624a514243a6a557863c90a5379e \
  --name all-50 --service all --monthly-threshold 50 \
  --emails valentin.ronteix@ashudev.com --delay 3600
```

Expected: two JSON objects returned. A `--delay` below 3600 is refused by the API; keep 3600.

- [ ] **Step 3: Read them back**

Run: `ovhcloud cloud alerting list --cloud-project a93a624a514243a6a557863c90a5379e -o json`
Expected: two entries, thresholds 20 and 50, service `all`, the email above, delay 3600. A wrong email is the failure this step exists for: the alert cannot be corrected by the cluster, only here.

---

### Task 4: Handover and live probe verification

**Files:** none.

**Interfaces:**
- Consumes: Tasks 1 and 2 merged to `develop`.
- Produces: a running prober; the series every later task reads.

- [ ] **Step 1: Hand over the branch**

Rebase on `origin/develop`, run the chart sweep, push, and open the pull request against `develop` with `github_create_pull_request`. The operator merges; nothing in Tasks 1–2 applies before that.

- [ ] **Step 2: Ask the operator for the one click ArgoCD needs**

A new Application is not auto-synced: sync policy lives in the ArgoCD UI (`AGENTS.md` §7.1). Ask the operator to sync `blackbox-exporter` (or enable auto-sync for it) once merged. Do not add `syncPolicy.automated` to make this step go away.

- [ ] **Step 3: Confirm the pod and the scrape jobs**

Through the Kubernetes MCP, confirm one `blackbox-exporter` pod `Running` on `nas`. Then:

```bash
curl -s http://prometheus.kub.local/api/v1/targets |
  jq -r '.data.activeTargets[] | select(.labels.job|startswith("blackbox")) | [.labels.job,.labels.instance,.health] | @tsv' |
  sort | head -40
```

Expected: 3 `blackbox-http` targets and 28 `blackbox-dns` targets, all `up`.

- [ ] **Step 4: Prove the public view and the module routing**

```bash
curl -sG http://prometheus.kub.local/api/v1/query --data-urlencode 'query=probe_success{job="blackbox-http"}' |
  jq -r '.data.result[] | "\(.metric.instance) \(.value[1])"'
curl -sG http://prometheus.kub.local/api/v1/query --data-urlencode 'query=count by (check)(probe_success{job="blackbox-dns"}==1)' |
  jq -r '.data.result[] | "\(.metric.check) \(.value[1])"'
curl -sG http://prometheus.kub.local/api/v1/query --data-urlencode 'query=probe_dns_answer_rrs{job="blackbox-dns",check="mx"}' |
  jq -r '.data.result[] | "\(.metric.instance) \(.value[1])"'
```

Expected: all three HTTP probes `1`; eleven `check` values each with the expected count (8 checks × 2 servers for `ashudev.com`, 3 × 4 for `ashuvidz.com`); MX answer counts of 5 or more. A `check` value with zero targets means the label-to-parameter relabel failed. If `ashuvidz.com` is `0` while `curl -I https://ashuvidz.com` from the workstation returns 200, record the hairpin limitation and take that target out of the values in a follow-up commit rather than silencing the rule.

- [ ] **Step 5: Confirm the certificate series and its issuer**

```bash
curl -sG http://prometheus.kub.local/api/v1/query --data-urlencode 'query=(probe_ssl_earliest_cert_expiry{job="blackbox-http"}-time())/86400'
```

Expected: three values well above 21 (GitHub's and Let's Encrypt's renew ahead of expiry). A negative value means the probe followed a redirect to a host whose name is not in the certificate; read the target, do not widen the assertion.

---

### Task 5: Alert rules

**Files:** none (Grafana's database).

**Interfaces:**
- Consumes: the series from Task 4; folder `homelab`; datasource `PBFA97CFB590B2093`.
- Produces: rules `CloudEndpointDown`, `CloudTlsCertificateExpiring`, `CloudDnsCheckFailing`.

- [ ] **Step 1: Copy the house rule shape**

```bash
curl -s -H "Authorization: Bearer $GRAFANA_TOKEN" \
  https://grafana.kub.local/api/v1/provisioning/alert-rules |
  jq '.[] | select(.title=="CertificateExpiring")'
```

Every new rule reuses that object's `data` array (refIds `A` and `C`, `__expr__` threshold with `gt 0`), `orgID`, `execErrState: OK`, `noDataState: OK`, `isPaused: false`. Only `title`, `uid`, `folderUID`, `ruleGroup`, `for`, `labels`, `annotations` and the `expr` in refId `A` change.

- [ ] **Step 2: Create the three rules**

| Title | Group | `for` | Labels | Expression A |
|---|---|---|---|---|
| `CloudEndpointDown` | `homelab-core-fast` | `5m` | `severity=critical` | `1 - probe_success{job="blackbox-http"} > 0` |
| `CloudDnsCheckFailing` | `homelab-core-fast` | `10m` | `severity=critical` | `1 - probe_success{job="blackbox-dns"} > 0` |
| `CloudTlsCertificateExpiring` | `homelab-core` | `1h` | `severity=warning` | `(21 * 86400 - (probe_ssl_earliest_cert_expiry{job="blackbox-http"} - time())) / 86400 > 0` |

Each expression returns a positive number when firing; the threshold stays `gt 0`. Annotations:

- `CloudEndpointDown` — summary `{{ $labels.instance }} did not answer over HTTPS`, description `The public endpoint failed to answer from inside the cluster, which sees the public internet. For ashudev.com this is GitHub Pages: check githubstatus.com, then the A records in infra-main. For ashuvidz.com it is the homelab router: check Traefik and the public TLS certificate.`
- `CloudDnsCheckFailing` — summary `{{ $labels.check }} check on {{ $labels.zone }} failed at {{ $labels.instance }}`, description `The authoritative server answered with nothing, an error, or a record that does not match. Read the zone with 'ovhcloud domain-zone record list {{ $labels.zone }}', then compare with infra-main/dns. A missing MX, SPF, DKIM or DMARC record breaks mail silently.`
- `CloudTlsCertificateExpiring` — summary `{{ $labels.instance }} certificate expires in {{ $value }} days`, description `Renewal happens well before 21 days for both GitHub Pages and Let's Encrypt. If this fires, the issuer is failing, not the site.`

POST each to `/api/v1/provisioning/alert-rules`.

- [ ] **Step 3: Confirm they evaluate and stay inactive**

```bash
curl -s -H "Authorization: Bearer $GRAFANA_TOKEN" \
  https://grafana.kub.local/api/v1/provisioning/alert-rules |
  jq -r '.[] | select(.title|startswith("Cloud")) | [.title,.ruleGroup,.for,.state] | @tsv'
```

Expected: three rules, the groups and durations above, state `inactive` (or `normal`). A rule in `no_data` state is a broken expression: fix it before going on.

- [ ] **Step 4: Prove the firing path without paging anyone**

Create a silence matching `alertname="ZZ CloudRulePathTest"`, then create a throwaway rule in `homelab-core-fast` whose expression A is `vector(1)` with the same `data` shape and `labels: {severity: test}`. Wait two evaluation intervals (about 2 minutes) and confirm via the rules API that it is `firing`, with `alerts[].annotations` rendered. Then delete the rule and the silence.

This is the only way to see pending → firing here: the cluster has no write path to inject a failing series. Read one existing silence's JSON first and copy its shape; a silence suppresses delivery, not state, which is exactly what the skill asks for.

- [ ] **Step 5: Record the rule UIDs**

Keep the three UIDs for Task 7's documentation.

---

### Task 6: Cloud dashboard

**Files:**
- Stage: `/tmp/opencode/cloud-dashboard.json` (never committed)

**Interfaces:**
- Consumes: the series from Task 4.
- Produces: dashboard `Cloud` in folder `homelab`.

- [ ] **Step 1: Validate every query before building a panel**

Run each through the datasource API and keep the ones that return rows:

```bash
source ~/.config/secrets/homelab.env
for q in 'sum(probe_success{job="blackbox-http"})' \
         'sum(probe_success{job="blackbox-dns"})' \
         'min((probe_ssl_earliest_cert_expiry{job="blackbox-http"}-time())/86400)' \
         'probe_success{job=~"blackbox-.*"}' \
         'probe_duration_seconds{job="blackbox-http"}'; do
  curl -s -H "Authorization: Bearer $GRAFANA_TOKEN" -H 'Content-Type: application/json' \
    https://grafana.kub.local/api/ds/query -d "$(jq -nc --arg q "$q" '{queries:[{refId:"A",datasource:{type:"prometheus",uid:"PBFA97CFB590B2093"},expr:$q,instant:true}],from:"now-24h",to:"now"}')" |
    jq -c --arg q "$q" '{q:$q,rows:([.results.A.frames[]?.data.values[0]|length]|add//0)}'
done
```

Expected: every query returns at least one row. A query with zero rows is dropped, not shipped.

- [ ] **Step 2: Build the six panels**

| Row | Panel | Type | Query |
|---|---|---|---|
| Needs attention | Endpoints answering | stat, `w=4 h=5`, unit `none`, display `x / 3` via a value mapping | `sum(probe_success{job="blackbox-http"})` |
| Needs attention | DNS checks passing | stat, `w=4 h=5`, `x / 28` | `sum(probe_success{job="blackbox-dns"})` |
| Needs attention | Days to earliest certificate | stat, `w=4 h=5`, unit `d`, thresholds 21 amber / 7 red | `min((probe_ssl_earliest_cert_expiry{job="blackbox-http"}-time())/86400)` |
| Endpoints | Endpoint success, 24 h | timeseries `w=8 h=8`, zero line, legend bottom | `probe_success{job="blackbox-http"}` |
| Endpoints | Probe latency | timeseries `w=8 h=8`, unit `s`, legend bottom | `probe_duration_seconds{job="blackbox-http"}` |
| Detail | Every check | table `w=24 h=8`, columns `instance`, `check`, `zone`, `Value` | `probe_success{job=~"blackbox-.*"}` with a labels-to-fields transformation |

Thresholds and units are on the panels; no colour without a threshold. The table is the panel a person reads when an alert names a check.

- [ ] **Step 3: Create it through the API**

POST `/api/dashboards/db` with `{dashboard: <the staged JSON>, folderUid: "homelab", overwrite: true}`. Then GET `/api/dashboards/uid/<uid>` and confirm the six panels and the folder.

- [ ] **Step 4: Re-run each panel's query as saved**

Take each `target.expr` out of the saved dashboard and run it through `/api/ds/query` once more. Expected: rows. A panel that is empty after saving is a panel that lies; fix the query or remove the panel.

---

### Task 7: Documentation

**Files:**
- Create: `docs/runbooks/cloud-monitoring.md`
- Modify: `docs/reference/alerts.md`, `docs/reference/dashboards.md`, `docs/observability-conventions.md`, `mkdocs.yml`

**Interfaces:**
- Consumes: Task 1's module table and service name, Task 2's jobs, Task 3's configuration IDs, Task 4's live numbers, Task 5's rule UIDs, Task 6's dashboard UID.
- Produces: the page a person opens when one of these alerts fires.

- [ ] **Step 1: Write the runbook**

Sections, each answering one question: **What is watched** (table: target, module, assertion, where it lives); **Why the prober has its own resolvers** (the `192.168.1.46` rewrite); **Reading each alert** (one subsection per rule: what it means, what to check, the command to run); **Adding a target** (values file, module, scrape job, and that two files change); **The ashuvidz.com delegation flip** (target list four AWS → two OVH, NS module stays liveness-only, no rule changes); **The money alerts** (id `07f35b7f-f559-472b-8852-93b140034720`, threshold €20, email, how to change or delete with `ovhcloud cloud alerting`; state that the €50 configuration was refused by the provider with HTTP 500 and is tracked by a Backlog card, so the page does not promise an alert that does not exist); **Rollback** (delete the Application and the two scrape jobs; delete the three rules, the dashboard and the silences through the Grafana API; delete the OVH configuration with `ovhcloud cloud alerting delete`; nothing holds state); **Known limits** (no mail delivery test, no zone-versus-git drift, no spend dashboard).

Plain language: "authoritative server (the server that holds the zone's real records)", "delegation (which servers the registry points the domain at)", "CNAME (one name that points at another)".

- [ ] **Step 2: Add it to the nav**

Add `Cloud monitoring: runbooks/cloud-monitoring.md` under the runbooks section of `mkdocs.yml`. A page that is not in the nav is a page nothing links to.

- [ ] **Step 3: Update the three reference pages**

- `docs/reference/alerts.md`: add the three rows to the rule table with group and `for`; add the three to the fast/core split narrative; delete the "No blackbox probe, no certificate check outside Traefik" gap; note the `dns_ns_ashuvidz` liveness-only choice in the `CloudDnsCheckFailing` paragraph.
- `docs/reference/dashboards.md`: add the `Cloud` dashboard with its six panels and the question it answers.
- `docs/observability-conventions.md`: add the exporter to "What runs where", the two static jobs with intervals and target counts (3 + 28), the public-resolver decision, and the prober's exclusion from `ScrapeTargetDown` reasoning if the annotation scrape makes it a normal pod target.

- [ ] **Step 4: Check the links and the nav**

Run `mkdocs build --strict` if the tool is available; otherwise grep every new relative link target and confirm the file exists, and confirm the nav string appears exactly once in `mkdocs.yml`. Expected: no broken relative link, nav entry present.

- [ ] **Step 5: Commit**

```bash
git add docs/runbooks/cloud-monitoring.md docs/reference/alerts.md docs/reference/dashboards.md docs/observability-conventions.md mkdocs.yml
git commit -m "📝 docs: runbook and references for the cloud probes"
```

Put the OVH configuration IDs and the Grafana rule/dashboard UIDs in the commit body.

---

## Verification Gate

After Task 7, on the merged tree:

1. Chart sweep from `AGENTS.md` §6: zero `FAIL`; both path checks: no `BROKEN`.
2. `hack/check-exposure.sh` passes (it should be unaffected; run it anyway).
3. Prometheus `/api/v1/targets`: every blackbox target `up`.
4. Grafana rules API: three rules, correct groups, `inactive`; the silenced clone test proved pending → firing → resolved and was removed.
5. Dashboard: every panel query returned rows when re-run from the saved dashboard.
6. `ovhcloud cloud alerting list`: one configuration (`all-20`, €20, right email); the €50 one is refused by the provider and tracked by a Backlog card.
7. `docs/runbooks/cloud-monitoring.md` is in the nav and every relative link resolves.
