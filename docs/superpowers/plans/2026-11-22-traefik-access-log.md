# Traefik redacted access log — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Traefik a per-request record with the URL and client address removed, so a 502 or a slow route can be found from Loki without copying credentials into an unauthenticated store.

**Architecture:** One `accessLog` block in `cluster/infrastructure/traefik/values-v3.yaml` (chart `42.4.1`, Traefik `3.7.19`) enables a JSON access log with the URL and client address dropped; the release is moved with `helm` because Traefik is outside GitOps. Two Grafana panels on Homelab · Traefik read the lines from Loki, and four living docs pages record the decision, the volume and the panels.

**Tech Stack:** Traefik 3.7.19 (chart `42.4.1`), Helm 3, Fluent Bit → Loki 3.7, Grafana HTTP API.

**Spec:** `docs/superpowers/specs/2026-11-22-traefik-access-log-design.md`

## Global Constraints

- Traefik 3.7.19, chart `42.4.1`, live values `cluster/infrastructure/traefik/values-v3.yaml`. Keys come from that chart's `values.yaml`, which validates with `additionalProperties: false`: the block is `accessLog`, and `defaultMode` is camel case.
- No URL, query string, header, client address or client username may reach Loki. `RequestPath` is dropped because its path carries FileBrowser's `/share/<hash>` capability links; `queryParameters` is dropped as a second lock.
- The values file is the release's only input: `helm upgrade --reset-values`. Never apply a manifest by hand.
- Loki carries no authentication (`auth_enabled: false`, route `lan-only`). Do not widen what it stores.
- Commit prefixes follow the repo rule (`🚀` feature, `📝` docs, `🐛` fix). PR base is `develop`; never push `develop` or `main`.
- Verification gate before the PR: `python3 hack/check_docs_links.py` and `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests`, both clean.

## Review Focus

1. A request whose URL carries a credential (`?X-Plex-Token=…`, `/share/<hash>`, `?code=…`): no part of the URL may appear in the stored line. Task 3 asserts it.
2. A failure Traefik answers itself (502/504, `OriginStatus` 0): the failed-request query must still select it. Tasks 3 and 4.
3. A long-lived Plex stream (duration over a second): it lands in the slow panel and is not a fault; the panel description says so. Task 4.
4. A non-JSON line from the same container (Traefik's own error log): the `| json` queries must skip it, not break. Task 3.
5. A fresh request that is not in Loki yet: lines reach it through Fluent Bit's flush interval, a few seconds, so an empty panel immediately after a request is not a bug. `bufferingSize: 100` is a writer channel in Traefik, not a batch: it does not add a timed delay. Task 4's description.

---

### Task 1: Enable the redacted access log in the values file

**Files:**
- Modify: `cluster/infrastructure/traefik/values-v3.yaml` (insert after the `resources:` block, before the `additionalArguments:` comment)

**Interfaces:**
- Produces: the `accessLog` block every later task verifies against; the rendered args pinned in Task 1's check.

- [ ] **Step 1: Write the failing render check**

Fetch the chart the release runs, once:

```bash
mkdir -p /tmp/opencode && cd /tmp/opencode
helm pull traefik/traefik --version 42.4.1 --untar --untardir /tmp/opencode/tv3
```

Then run the check, which must fail on the missing `--accesslog=true`:

```bash
cd "$(git rev-parse --show-toplevel)"
ARGS=$(helm template test /tmp/opencode/tv3/traefik -n kube-system \
  -f cluster/infrastructure/traefik/values-v3.yaml | grep -o -- '--accesslog[^" ]*')
grep -q -- '--accesslog=true' <<<"$ARGS" || echo "FAIL: access log not enabled"
```

Expected: `FAIL: access log not enabled`.

- [ ] **Step 2: Add the block**

```yaml
# A per-request record, with the request URL and the client address removed.
# Traefik's `RequestPath` is the URI, and v3.7.3+ can drop its query string
# (`queryParameters` below) but not its path. The path alone still carries
# FileBrowser's `/share/<hash>` capability links, and Loki has no authentication
# and a 30-day retention, so the whole field is dropped. Plex's `?X-Plex-Token=`
# and OIDC `?code=` never reach the log, and the query-parameter drop is a second
# lock in case a field is ever re-added.
#
# `defaultMode: keep` keeps everything else, so a line is wider than the six
# names below suggest: the requested hostname, the method, the entry point, both
# statuses and durations, sizes, the retry count, the TLS version and cipher, and
# the pod address Traefik forwarded to. No path, no query, no header, no client
# address. `docs/observability-conventions.md` lists the fields in full.
accessLog:
  enabled: true
  format: json
  # Hand each line to a writer goroutine through a 100-slot channel instead of
  # writing on the request path. There is no timed flush in Traefik: the delay
  # between a request and its line in Loki is Fluent Bit's, a few seconds.
  bufferingSize: 100
  fields:
    # The chart always renders this key, so it must be set; `keep` is the
    # default and the five names below are the exceptions.
    defaultMode: keep
    names:
      RequestPath: drop
      ClientAddr: drop
      ClientHost: drop
      ClientPort: drop
      ClientUsername: drop
    headers:
      # Already the default; written out so a future reader sees that no
      # Authorization or Cookie header is logged.
      defaultMode: drop
    queryParameters:
      defaultMode: drop
```

- [ ] **Step 3: Re-run the check and assert every argument**

```bash
cd "$(git rev-parse --show-toplevel)"
ARGS=$(helm template test /tmp/opencode/tv3/traefik -n kube-system \
  -f cluster/infrastructure/traefik/values-v3.yaml | grep -o -- '--accesslog[^" ]*')
grep -q -- '--accesslog=true' <<<"$ARGS"
grep -q -- '--accesslog.format=json' <<<"$ARGS"
grep -q -- '--accesslog.bufferingsize=100' <<<"$ARGS"
grep -q -- '--accesslog.fields.defaultmode=keep' <<<"$ARGS"
grep -q -- '--accesslog.fields.headers.defaultmode=drop' <<<"$ARGS"
grep -q -- '--accesslog.fields.queryparameters.defaultmode=drop' <<<"$ARGS"
for f in RequestPath ClientAddr ClientHost ClientPort ClientUsername; do
  grep -q -- "--accesslog.fields.names.$f=drop" <<<"$ARGS" || echo "FAIL: $f not dropped"
done
helm lint /tmp/opencode/tv3/traefik -f cluster/infrastructure/traefik/values-v3.yaml | tail -1
```

Expected: no `FAIL` line, `1 chart(s) linted, 0 chart(s) failed`, and the list shows all eleven flags.

- [ ] **Step 4: Commit**

```bash
git add cluster/infrastructure/traefik/values-v3.yaml
git commit -m "🚀 feat(traefik): log requests without the URL or the client address"
```

---

### Task 2: Move the live release

**Files:** none — the release is applied from the file Task 1 committed.

**Interfaces:**
- Consumes: the `accessLog` block from Task 1.
- Produces: a live release whose args Task 3 can query.

- [ ] **Step 1: Record the before state**

```bash
helm -n kube-system history traefik | tail -2
kubectl -n kube-system get deploy traefik -o jsonpath='{.spec.template.spec.containers[0].image}{"\n"}'
POD=$(kubectl -n kube-system get pod -l app.kubernetes.io/name=traefik -o jsonpath='{.items[0].metadata.name}')
kubectl -n kube-system exec "$POD" -- sha256sum /data/acme.json
kubectl get ingressroute.traefik.io -A --no-headers | wc -l
```

Expected: revision 12 or later, image `docker.io/traefik:v3.7.19`, an `acme.json` hash, and the route count. Write the hash and the count down — Task 2 Step 4 compares against them.

- [ ] **Step 2: Dry-run and diff the Deployment**

```bash
helm upgrade traefik /tmp/opencode/tv3/traefik -n kube-system \
  --reset-values -f cluster/infrastructure/traefik/values-v3.yaml \
  --dry-run=server > /tmp/opencode/traefik-dryrun.yaml
grep -n -- '--accesslog' /tmp/opencode/traefik-dryrun.yaml
```

Expected: only the eleven `--accesslog*` flags are new; no other argument, port or volume changes.

- [ ] **Step 3: Apply**

```bash
helm upgrade traefik /tmp/opencode/tv3/traefik -n kube-system \
  --reset-values -f cluster/infrastructure/traefik/values-v3.yaml --wait --timeout 5m
```

Expected: `STATUS: deployed`, one pod ready. Ingress blips for the few seconds the `Recreate` takes.

- [ ] **Step 4: Verify the release holds the file, the args are live, the store is untouched**

```bash
helm -n kube-system get values traefik > /tmp/opencode/traefik-after.yaml
python3 - <<'EOF'
import subprocess, yaml
raw = subprocess.check_output(['helm', '-n', 'kube-system', 'get', 'values', 'traefik'], text=True)
release = yaml.safe_load(raw.split('USER-SUPPLIED VALUES:', 1)[1])
file = yaml.safe_load(open('cluster/infrastructure/traefik/values-v3.yaml'))
print("release == file (semantic):", release == file)
EOF
kubectl -n kube-system get deploy traefik -o jsonpath='{.spec.template.spec.containers[0].args}' \
  | tr ',' '\n' | grep accesslog
POD=$(kubectl -n kube-system get pod -l app.kubernetes.io/name=traefik -o jsonpath='{.items[0].metadata.name}')
kubectl -n kube-system exec "$POD" -- sha256sum /data/acme.json
kubectl get ingressroute.traefik.io -A --no-headers | wc -l
for h in https://plex.kub.home.ashudev.com https://filebrowser.kub.home.ashudev.com https://vikunja.kub.local; do
  printf '%s %s\n' "$(curl -s -o /dev/null -w '%{http_code}' "$h")" "$h"
done
```

Expected: `release == file (semantic): True`; all eleven flags; the same `acme.json` hash; the same
route count; 401 on the Plex host (its route carries `plex-security`), 200 on the other two.

A textual `diff` of the two never matches — Helm re-marshals the values, so comments and key order are
gone — which is why the comparison is parsed.

- [ ] **Step 5: No commit**

The file was committed in Task 1; this task changes the cluster only.

---

### Task 3: Prove the line is safe, then measure it

**Files:** none — the numbers feed Task 5.

**Interfaces:**
- Consumes: live lines from Task 2.
- Produces: the byte/day figure and the four query outputs Task 5 quotes.

- [ ] **Step 1: Read three lines and inspect the fields**

```bash
curl -sG http://loki.kub.local/loki/api/v1/query_range \
  --data-urlencode 'query={job="fluent-bit", container="traefik"}' \
  --data-urlencode 'limit=3' | python3 -c '
import json,sys
d=json.load(sys.stdin)
for s in d["data"]["result"]:
    for ts,line in s["values"]:
        print(line)
'
```

Expected: JSON objects with `RouterName`, `DownstreamStatus`, `Duration` (a number, nanoseconds), `RequestHost`. No `RequestPath`, `ClientAddr`, `ClientHost`, `ClientPort`, `ClientUsername`, no `Request_Header` map. What is expected to be present besides those: `RequestAddr`, `RequestMethod`, `RequestScheme`, `RequestProtocol`, `RouterName`, the `Kubernetes*` names, `OriginStatus`, `OriginDuration`, `Overhead`, both content sizes, `RetryAttempts`, `TLSVersion`, `TLSCipher`, `ServiceAddr` and `ServiceURL`. If a field arrives that carries a path or a query string, stop and add it to Step 2's list and to the drop set.

- [ ] **Step 2: Assert nothing sensitive is present**

```bash
for pat in 'RequestPath' 'RequestLine' 'ClientAddr' 'ClientHost' 'ClientPort' 'ClientUsername' \
           'X-Plex-Token' 'share/' 'code='; do
  n=$(curl -sG http://loki.kub.local/loki/api/v1/query \
    --data-urlencode "query=sum(count_over_time({job=\"fluent-bit\", container=\"traefik\"} |~ \"$pat\" [30m]))" \
    | python3 -c 'import json,sys;r=json.load(sys.stdin)["data"]["result"];print(int(float(r[0]["value"][1])) if r else 0)')
  echo "$pat $n"
done
```

Expected: `0` on every line. A non-zero count stops the task: the field set is wrong, fix Task 1 before going on. `RequestLine` is in the list even though v3 does not emit it: it is the field the v3 adaptation rests on being gone, so it is asserted rather than assumed.

- [ ] **Step 3: Check a Traefik-answered failure is selectable**

```bash
curl -sG http://loki.kub.local/loki/api/v1/query_range \
  --data-urlencode 'query={job="fluent-bit", container="traefik"} | json | DownstreamStatus >= 400' \
  --data-urlencode 'limit=5' | python3 -c '
import json,sys
d=json.load(sys.stdin)
print(sum(len(s["values"]) for s in d["data"]["result"]), "lines")
'
```

Expected: a number, possibly 0 if nothing failed in the window. If 0, generate one:
`curl -s -o /dev/null https://traefik.kub.local/nope` (404/401), wait a few seconds for Fluent Bit's
flush, re-run.

- [ ] **Step 4: Measure lines and bytes over the window that exists**

Never divide by a window longer than the data: a `[1h]` query run ten minutes after the switch-on
returns a figure that looks like a tenth of the rate. Measure the window that exists, and say which.

```bash
START=$(curl -sG http://loki.kub.local/loki/api/v1/query_range \
  --data-urlencode 'query={job="fluent-bit", container="traefik"}' \
  --data-urlencode 'limit=1' --data-urlencode 'direction=forward' \
  | python3 -c 'import json,sys;print(min(x[0] for s in json.load(sys.stdin)["data"]["result"] for x in s["values"])//10**9)')
END=$(date +%s); MINS=$(( (END-START)/60 ))
echo "window ${MINS}m from $(date -d @$START -Iseconds)"
for q in 'sum(count_over_time({job="fluent-bit", container="traefik"}[90m]))' \
         'sum(bytes_over_time({job="fluent-bit", container="traefik"}[90m]))'; do
  curl -sG http://loki.kub.local/loki/api/v1/query \
    --data-urlencode "query=$q" --data-urlencode "time=$END" \
    | python3 -c 'import json,sys;r=json.load(sys.stdin)["data"]["result"];print(float(r[0]["value"][1]) if r else 0)'
done
```

Compute, and keep every input: `line size = bytes / lines`; `MB/day = bytes / (window seconds) × 86400
/ 1e6`. The window is whatever the log has been running for, capped at `90m` so the query covers it.
Quote the window and its UTC bounds with the figure, and note that a night window reads low — an
evening carries more. Once a day of data exists, replace the measurement with the two `[24h]` queries
in [`observability-conventions.md`](../../observability-conventions.md#what-the-router-logs) and quote
that instead.

Do not multiply a request count from Prometheus by a line size: the two do not agree (the log carries
more lines than `traefik_router_requests_total` counts), and the byte rate measured on the stream
needs neither.

- [ ] **Step 5: No commit**

---

### Task 4: Two panels on Homelab · Traefik

**Files:** Grafana database via the HTTP API (no repo file; `docs/reference/dashboards.md` is Task 5).

**Interfaces:**
- Consumes: the Loki datasource `P8E80F9AEF21F6940` and the access-log lines.
- Produces: dashboard `homelab-traefik` with a fifth row, `Request record`.

- [ ] **Step 1: Back up the dashboard**

```bash
set -a; . ~/.config/secrets/homelab.env; set +a
mkdir -p /tmp/opencode/grafana/backup
curl -s -H "Authorization: Bearer $GRAFANA_TOKEN" \
  https://grafana.kub.local/api/dashboards/uid/homelab-traefik \
  -o /tmp/opencode/grafana/backup/homelab-traefik-before.json
python3 -c 'import json;d=json.load(open("/tmp/opencode/grafana/backup/homelab-traefik-before.json"))["dashboard"];print(len(d["panels"]),"panels")'
```

Expected: `15 panels`.

- [ ] **Step 2: Insert the row and the two panels**

Write `/tmp/opencode/grafana/add-request-record.py`:

```python
import json, os, urllib.request

SRC = "/tmp/opencode/grafana/backup/homelab-traefik-before.json"
dash = json.load(open(SRC))["dashboard"]

SHIFT = 9  # one row (1) + two panels (8)
for p in dash["panels"]:
    if p["gridPos"]["y"] >= 18:
        p["gridPos"]["y"] += SHIFT

row = {
    "id": 16, "type": "row", "title": "Request record",
    "collapsed": False, "gridPos": {"h": 1, "w": 24, "x": 0, "y": 18},
    "panels": [],
}
failed = {
    "id": 17, "type": "logs", "title": "Failed requests (4xx/5xx)",
    "datasource": {"type": "loki", "uid": "P8E80F9AEF21F6940"},
    "description": "Access-log lines with a 4xx or 5xx status. The URL is not logged: "
                   "RequestPath is dropped and query parameters with it, because Loki has no "
                   "authentication and URLs carry Plex tokens and FileBrowser share capabilities. "
                   "A failure Traefik answers itself shows OriginStatus 0. A line reaches Loki a "
                   "few seconds after the request, through Fluent Bit's flush interval.",
    "gridPos": {"h": 8, "w": 12, "x": 0, "y": 19},
    "options": {"dedupStrategy": "none", "enableLogDetails": True, "prettifyLogMessage": True,
                "showCommonLabels": False, "showLabels": False, "showTime": True,
                "sortOrder": "Descending", "wrapLogMessage": False},
    "targets": [{"datasource": {"type": "loki", "uid": "P8E80F9AEF21F6940"},
                 "expr": '{job="fluent-bit", container="traefik"} | json | DownstreamStatus >= 400',
                 "queryType": "range", "refId": "A"}],
}
slow = {
    "id": 18, "type": "timeseries", "title": "Requests slower than 1 s, by router",
    "datasource": {"type": "loki", "uid": "P8E80F9AEF21F6940"},
    "description": "Count of access-log lines whose Duration passed one second, per router, in the trailing "
                   "five minutes at each step. Duration is nanoseconds in the JSON format. A Plex "
                   "stream runs for the length of the film and belongs here; read the router name "
                   "before treating a bar as a fault. The p95 panel above is the alerting view, this "
                   "one is per-request. Lines reach Loki a few seconds late, through Fluent Bit.",
    "gridPos": {"h": 8, "w": 12, "x": 12, "y": 19},
    "fieldConfig": {"defaults": {"unit": "short", "decimals": 0}, "overrides": []},
    "options": {"legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
                "tooltip": {"mode": "multi", "sort": "desc"}},
    "targets": [{"datasource": {"type": "loki", "uid": "P8E80F9AEF21F6940"},
                 "expr": 'sum by (RouterName) (count_over_time({job="fluent-bit", container="traefik"}'
                         ' | json | Duration > 1000000000 [5m]))',
                 "queryType": "range", "refId": "A", "legendFormat": "{{RouterName}}"}],
}
dash["panels"].extend([row, failed, slow])
assert max(p["id"] for p in dash["panels"]) == 18
assert max(p["gridPos"]["y"] + p["gridPos"]["h"] for p in dash["panels"]) == 44

body = json.dumps({"dashboard": dash, "overwrite": True,
                   "message": "add the access-log request record"}).encode()
req = urllib.request.Request(
    "https://grafana.kub.local/api/dashboards/db", data=body, method="POST",
    headers={"Authorization": "Bearer " + os.environ["GRAFANA_TOKEN"],
             "Content-Type": "application/json"})
print(urllib.request.urlopen(req, context=__import__("ssl")._create_unverified_context()).read().decode())
```

Run it:

```bash
set -a; . ~/.config/secrets/homelab.env; set +a
python3 /tmp/opencode/grafana/add-request-record.py
```

Expected: `{"id":…,"slug":"homelab-traefik","status":"success","uid":"homelab-traefik","version":N}`.

- [ ] **Step 3: Verify through the API**

```bash
curl -s -H "Authorization: Bearer $GRAFANA_TOKEN" \
  https://grafana.kub.local/api/dashboards/uid/homelab-traefik | python3 -c '
import json,sys
p=json.load(sys.stdin)["dashboard"]["panels"]
print(len(p),"panels")
print([x["title"] for x in p if x.get("type")=="row"])
print([(x["title"],x["gridPos"]) for x in p if x["title"].startswith(("Failed","Requests slower"))])
'
```

Expected: `18 panels`; rows `Traffic, Latency and errors, Request record, Volume, Health`; both new panels listed, the second at `x: 12`.

- [ ] **Step 4: Prove both queries return data**

Run the failed-request query from Task 3 Step 3 again, and:

```bash
curl -sG http://loki.kub.local/loki/api/v1/query_range \
  --data-urlencode 'query=sum by (RouterName) (count_over_time({job="fluent-bit", container="traefik"} | json | Duration > 1000000000 [5m]))' \
  --data-urlencode 'limit=1' | python3 -c '
import json,sys
r=json.load(sys.stdin)["data"]["result"]
print([(x["metric"].get("RouterName"), x["values"][-1][1]) for x in r] or "empty (no 1 s request in 5 min)")
'
```

Expected: a router list, or `empty` — an empty result is a valid pass, because no request over one second occurred; say so rather than inventing failure.

- [ ] **Step 5: No commit**

---

### Task 5: Record the decision, the volume and the panels

**Files:**
- Modify: `docs/observability-conventions.md` (new `## What the router logs` section, after `## How metrics get in`)
- Modify: `docs/reference/metrics.md` (the `kube-system` bullet under `## Storage and retention`)
- Modify: `docs/reference/dashboards.md` (the `Homelab · Traefik` paragraph)
- Modify: `docs/runbooks/traefik-release.md` (the `access log | still off` row in the v3 table)

**Interfaces:**
- Consumes: the measured figures from Task 3 and the panel titles from Task 4.
- Produces: the page the card's second branch asked for.

- [ ] **Step 1: Write the section in `observability-conventions.md`**

```markdown
## What the router logs

Traefik keeps a per-request access log since 2026-11-22: JSON on stdout, buffered 100 lines at a
time, and carried to Loki by the same Fluent Bit pipeline as everything else. It exists for the
question the metrics cannot answer — *which* request returned 502, or took four seconds — and it is
deliberately narrower than a normal access log.

**The URL and the client are not in it.** `RequestPath` is the URI; from 3.7.3 it can be stripped of
its query string (`fields.queryParameters`), but not of its path, and the path alone carries
FileBrowser's `/share/<hash>` capability links. So the whole field is dropped — the query parameters
with it as a second lock — along with `ClientAddr`, `ClientHost`, `ClientPort` and `ClientUsername`;
headers stay dropped by the chart default. The reason is that `loki.kub.local` has no authentication
and a 30-day retention: a full log would put Plex's `?X-Plex-Token=`, FileBrowser's share links and
OIDC `?code=` callbacks in front of every device on the LAN.

**Everything else is kept**, because the file uses `defaultMode: keep` and names only the drops above.
What a line carries, measured on the live stream rather than read off the docs: the time, the entry
point name and scheme, the router name with its Kubernetes route namespace, the service name,
namespace and port, the requested hostname and method, the downstream and origin status, the two
durations in nanoseconds, the response and request sizes, the retry count, the TLS version and cipher,
the compression ratio, and the pod address Traefik forwarded the request to (`ServiceAddr`,
`ServiceURL`). What is left in it is the hostname and cluster-internal addresses, not a credential: a
record without the hostname cannot say which application failed. A failure Traefik answers itself
shows `OriginStatus` 0.

**Measured 2026-11-22** over the twelve minutes after that day's last restart (01:20–01:32Z): 153 lines
and 118 kB, so 769 bytes a line and **about 15 MB/day** at the night-time rate, against a pipeline that
carries 0.09 GB/day. An evening reads higher; the day's own figure is two queries away:

```bash
# bytes carried in the trailing 24 h — this is the day's volume
curl -sG http://loki.kub.local/loki/api/v1/query \
  --data-urlencode 'query=sum(bytes_over_time({job="fluent-bit", container="traefik"}[24h]))'
# lines in the same window; divide the two for the line size
curl -sG http://loki.kub.local/loki/api/v1/query \
  --data-urlencode 'query=sum(count_over_time({job="fluent-bit", container="traefik"}[24h]))'
```

Two panels on **Homelab · Traefik**, row *Request record*, read it: *Failed requests (4xx/5xx)* and
*Requests slower than 1 s, by router*. A long Plex stream belongs in the second one. Lines reach Loki
a few seconds late, because Fluent Bit batches on its own flush interval; Traefik's writer does not
wait.

Gating Loki itself is a separate card: the log store is still readable by every device on the LAN,
and that is true of every application log, not only this one.
```

Replace `N`, `B`, `X` with Task 3's figures, and the "two panels" names with what Task 4 created.

- [ ] **Step 2: Add the volume row in `reference/metrics.md`**

In the `kube-system, 6 MB` bullet of `## Storage and retention`, append one sentence with the same
`X MB/day` figure and the field set that keeps it small, and add the new measurement date beside the
existing one.

- [ ] **Step 3: Note the panels in `reference/dashboards.md`**

In the `**Homelab · Traefik**` paragraph, add two sentences after the `Certificates in the store`
sentence: the fifth row reads the access log, names both panels, and says the URL is absent by
design (link `../observability-conventions.md#what-the-router-logs`).

- [ ] **Step 3b: Replace the `access log | still off` row in the runbook**

In the v3 defaults table of `docs/runbooks/traefik-release.md`, the row reads
`| access log | still off | The decision belongs to card 544, not here. If it is turned on, drop query
strings with ... |`. Replace it with a row naming the live setting: on since 2026-11-22 at revision N,
`accessLog.format: json`, `bufferingSize: 100`, `RequestPath` and the four client fields dropped,
query parameters dropped, and the two panels that read it. Keep the runbook's tone: a fact with a
date, not a plan.

- [ ] **Step 4: Run the documentation gates**

```bash
python3 hack/check_docs_links.py
uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests
```

Expected: `0 broken relative links` and a zero-failure pytest summary.

- [ ] **Step 5: File the Loki-authentication card**

```bash
set -a; . ~/.config/secrets/homelab.env; set +a
curl -s -X POST -H "Authorization: Bearer $VIKUNJA_API_TOKEN" -H "Content-Type: application/json" \
  https://vikunja.kub.local/api/v2/projects/2/tasks \
  -d @- <<'JSON'
{"title": "loki: put authentik-forwardauth in front of loki.kub.local",
 "bucket_id": 13, "position": 2000000000,
 "description": "<p>repo: vyrtualsynthese/homelab<br/>docs: observability-conventions.md<br/>branch: none<br/>pr: none</p><p>Loki runs <code>auth_enabled: false</code> and its route carries <code>lan-only</code> with no gate, so every device on the LAN can read every application log, 30 days of it. Found while deciding what Traefik's access log may contain (task 544).</p><p>Done when the <code>loki.kub.local</code> IngressRoute carries <code>kube-system-authentik-forwardauth@kubernetescrd</code>, Grafana and Fluent Bit still reach it through the Service, and the raw API answers a redirect to authentik from a LAN client without a session.</p>"}
JSON
```

Expected: `201` with the new task. Then attach the `task` label (id 3) with
`POST /tasks/{id}/labels` `{"label_id": 3}`.

- [ ] **Step 6: Commit**

```bash
git add docs/observability-conventions.md docs/reference/metrics.md docs/reference/dashboards.md
git commit -m "📝 docs(traefik): record the access log, its field set and its volume"
```

---

### Task 6: Hand over

**Files:** none.

- [ ] **Step 1: Rebase and run the full gate**

```bash
git fetch origin
git rebase origin/develop
python3 hack/check_docs_links.py
uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests
```

Expected: a clean rebase, `0 broken relative links`, zero failures.

- [ ] **Step 2: Push and open the PR**

```bash
git push -u origin feature/traefik-access-log
```

Then `github_create_pull_request` with base `develop`, title
`Traefik: a request record without the URLs`, body carrying the spec link, the dropped fields, the
measured figure, the note that the live release was moved with `helm` on 2026-11-22, and the
verification output.

- [ ] **Step 3: Move the card to Validation**

`PUT /projects/2/views/13/buckets/14/tasks` `{"task_id": 544}`, then update the description with the
branch, the PR link and the verification output, and comment the two commands that prove the log has
no URL.

- [ ] **Step 4: Report**

State what changed live, what the measurement was, and what the PR carries.
