# Traefik dashboard and rules: an export, not a source

Grafana's database is the authority for the `Homelab · Traefik` dashboard and for its three alert
rules. These two files are a **snapshot taken on 2026-11-21**, committed so the panels and expressions
can be read in a pull request and recovered by hand if the database is lost. Nothing in this
repository applies them: there is no configmap, no sidecar and no provisioning entry, and Grafana
never reads this directory.

Why not provision them: the other ten dashboards and all 42 rules in the `Homelab` folder live in the
same database. Provisioning two objects from git would give that folder two sources of truth, and the
next person to edit a panel would have to know which one wins. The exception is recorded in
[`docs/reference/dashboards.md`](../../../../../../docs/reference/dashboards.md).

| File | What it holds |
|---|---|
| `dashboard.json` | A direct `POST /api/dashboards/db` body: `{dashboard, folderUid: "homelab", overwrite: true}`. Fourteen panels, uid `homelab-traefik`. |
| `rules.json` | The three rules as `GET /api/v1/provisioning/alert-rules` returns them, in group `homelab-core` (300 s), severity `warning`. |

## Re-applying after a wipe

```bash
set -a; . ~/.config/secrets/homelab.env; set +a
B=https://grafana.kub.local

curl -sk -X POST -H "Authorization: Bearer $GRAFANA_TOKEN" \
  -H 'Content-Type: application/json' --data @dashboard.json "$B/api/dashboards/db"

python3 - <<'EOF'
import json, os, urllib.request
B = "https://grafana.kub.local"
T = os.environ["GRAFANA_TOKEN"]
for r in json.load(open("rules.json")):
    for k in ("id", "updated", "provenance"):
        r.pop(k, None)
    req = urllib.request.Request(B + "/api/v1/provisioning/alert-rules",
        data=json.dumps(r).encode(), method="POST",
        headers={"Authorization": "Bearer " + T, "Content-Type": "application/json"})
    print(r["title"], urllib.request.urlopen(req).status)
EOF
```

The rules land in `homelab-core` with the interval the group already carries; if the group itself is
gone, create it with `POST /api/v1/provisioning/folder/homelab/rule-groups/homelab-core` and
`{"interval": 300}` first. The `uid` values in `rules.json` are the ones the live rules answer to, so
a restore keeps the same identity the Discord messages and silences name.

## What the panels and rules read

The metrics are Traefik's own Prometheus endpoint on the pod's `:9100`, scraped through the pod
annotation — the Service no longer publishes that port ([`values.yaml`](../../../../../infrastructure/traefik/values.yaml)).
The p95 and p99 panels only exist because Prometheus stopped dropping `traefik_.*_bucket` on
2026-11-21 and the release widened the buckets to `0.1,0.25,0.5,1,2.5,5,10`. The thresholds, the
reasoning and the known holes are in [`docs/reference/alerts.md`](../../../../../../docs/reference/alerts.md).

The certificate panel reads the whole store, which includes the eighteen names no route serves any
more; they renew like the rest until board task 409 removes them.
