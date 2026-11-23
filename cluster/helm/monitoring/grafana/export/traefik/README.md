# Traefik dashboard and rules: an export, not a source

Grafana's database is the authority for the `Homelab · Traefik` dashboard and for its three alert
rules. These two files are a **snapshot taken on 2026-11-21**, with the dashboard re-exported on
2026-11-22, committed so the panels and expressions can be read in a pull request and recovered by
hand if the database is lost. Nothing in this repository applies them: there is no configmap, no
sidecar and no provisioning entry, and Grafana never reads this directory.

Why not provision them: the other ten dashboards and the other 39 rules live in the same database.
Provisioning two objects from git would give that folder two sources of truth, and the next person to
edit a panel would have to know which one wins. The exception is recorded in
[`docs/reference/dashboards.md`](../../../../../../docs/reference/dashboards.md).

| File | What it holds |
|---|---|
| `dashboard.json` | A direct `POST /api/dashboards/db` body: `{dashboard, folderUid: "homelab", overwrite: true}`. Eleven panels in four rows, uid `homelab-traefik`, version 7. |
| `rules.json` | The three rules as `GET /api/v1/provisioning/alert-rules` returns them, in group `homelab-core` (300 s), severity `warning`. |

Both files were re-exported the same day, after the v3 move, because the v3 router stops exporting two
names they used: `traefik_router_open_connections` is now `traefik_open_connections` (labelled by
entry point) and `traefik_config_reloads_failure_total` does not exist at all. The connections panel
follows the rename; the failure panel became "Reloads, 1 h", and `TraefikConfigReloadFailing` now reads
`traefik_config_last_reload_success == 0`, which is what a refused configuration leaves behind.

The dashboard was re-exported again on 2026-11-22, version 7, when the configuration audit turned on
`metrics.prometheus.addEntryPointsLabels` and added `Requests per second by entry point`
([the audit](../../../../../../docs/runbooks/traefik-release.md#the-37-configuration-audit-2026-11-22-revision-12)). The rules did
not change.

## Re-applying after a wipe

```bash
set -a; . ~/.config/secrets/homelab.env; set +a
B=https://grafana.kub.local
CA=~/.config/secrets/kub-local/local-ca.pem

curl -s --cacert "$CA" -X POST -H "Authorization: Bearer $GRAFANA_TOKEN" \
  -H 'Content-Type: application/json' --data @dashboard.json "$B/api/dashboards/db"

python3 - <<'EOF'
import json, os, ssl, urllib.request
B = "https://grafana.kub.local"
T = os.environ["GRAFANA_TOKEN"]
CA = os.path.expanduser("~/.config/secrets/kub-local/local-ca.pem")
ctx = ssl.create_default_context(cafile=CA)

def call(method, path, payload=None):
    req = urllib.request.Request(B + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        method=method, headers={"Authorization": "Bearer " + T, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, context=ctx) as r:
        return r.status, json.loads(r.read() or b"{}")

for r in json.load(open("rules.json")):
    for k in ("id", "updated", "provenance"):
        r.pop(k, None)
    print(r["title"], call("POST", "/api/v1/provisioning/alert-rules", r)[0])

# The rule POSTs recreate the group at Grafana's default interval; put 300 s back.
_, g = call("GET", "/api/v1/provisioning/folder/homelab/rule-groups/homelab-core")
print("group", call("PUT", "/api/v1/provisioning/folder/homelab/rule-groups/homelab-core",
                   {"title": g["title"], "folderUid": g["folderUid"],
                    "interval": 300, "rules": g["rules"]})[0])
EOF
```

The `uid` values in `rules.json` are the ones the live rules answer to, so a restore keeps the same
identity the Discord messages and silences name. **There is no `POST` on the rule-group path** (it
answers 404): creating a rule in a folder and group that does not exist creates the group at Grafana's
default interval, and the `PUT` above is what sets 300 s. That `PUT` replaces the whole group, which is
why it carries the rules it just read back. Both calls were run against Grafana 13.3.1 on 2026-11-21.

## What the panels and rules read

The metrics are Traefik's own Prometheus endpoint on the pod's `:9100`, scraped through the pod
annotation — the Service no longer publishes that port ([`values.yaml`](../../../../../helm/platform/traefik/values.yaml), nested under `traefik:`).
The p95 and p99 panels only exist because Prometheus stopped dropping `traefik_.*_bucket` on
2026-11-21 and the release widened the buckets to `0.1,0.25,0.5,1,2.5,5,10`. The thresholds, the
reasoning and the known holes are in [`docs/reference/alerts.md`](../../../../../../docs/reference/alerts.md).

The certificate panel reads the whole store, which includes the eighteen names no route serves any
more; they renew like the rest until task 543 removes them.
