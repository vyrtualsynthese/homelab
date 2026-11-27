# Security dashboard: an export, not a source

Grafana's database is the authority for `Homelab · Security`. This file is a **snapshot taken on
2026-11-25 and corrected on 2026-11-27**, committed so the panels and their expressions can be read in a pull request and restored
by hand if the database is lost. Nothing in this repository applies it: there is no configmap, no
sidecar and no provisioning entry, and Grafana never reads this directory — the same rule the Traefik
export beside it states, for the same reason (two sources of truth in one folder).

| File | What it holds |
|---|---|
| `dashboard.json` | A direct `POST /api/dashboards/db` body: `{dashboard, folderUid: "homelab", overwrite: true}`. Nine panels, uid `homelab-security`, version 11. |
| `rules.json` | The eight security rules, as `GET /api/v1/provisioning/alert-rules` returns them, in that order. Seven are in `homelab-core-fast`, `SecurityExposureDrift` in `homelab-core`. `SecurityAttackSustained` was deleted from the database on 2026-11-26 and is no longer in this copy; `AuthentikSignInSuccessAfterBurst` was added the same day. |

## What the nine panels read

| Panel | Datasource | Reads |
|---|---|---|
| Danger state | Prometheus | `fw_bouncer_banned_ips`, the three `homelab_security_*` posture gauges, `homelab_security_exporter_last_success_timestamp_seconds`, `cs_active_decisions`, `cs_parser_hits_ok_total`, `traefik_router_requests_total`, `homelab_authentik_signins_15m` |
| Bans active | Prometheus | `fw_bouncer_banned_ips`, scraped from the host bouncer on `streaming:60601` |
| Posture | Prometheus | `homelab_security_{ingress_routes,ungated_routes,public_hosts,privileged_workloads}`, `homelab_security_exporter_last_success_timestamp_seconds` |
| Requests per host, 24 h | Loki | `{job="fluent-bit", source="accesslog"}` with `\| json`, grouped by `RequestHost` |
| Rejected share | Prometheus | `traefik_router_requests_total`, 4xx over all, 15-minute rate |
| Top addresses refused | Loki | the same access stream, `DownstreamStatus` 401–499, grouped by `ClientHost` |
| Sign-in outcomes | Prometheus | `homelab_authentik_signins_15m` and `_24h`, by `outcome` |
| Requests by host and status, 15 m | Loki | the access stream, grouped by `RequestHost` and `DownstreamStatus` |
| Host firewall blocks | Loki | `{source="journal", unit="kernel"}` filtered to `UFW BLOCK` |

Every access-log query names `source="accesslog"`. Without it the selector also matches the router's
own stdout — 28,883 lines against 3,148 access lines in the same 24 hours on 2026-11-25 — and `| json`
answers a parse error for the mixed set.

## The danger state

`2 = alerting, 1 = watch, 0 = calm`, and each of the three was read on the screen once when the panel
was created:

- **2** — more than 20 decisions created here in an hour, the posture counts moved, the security
  pipeline is blind, or refusals above 85 % of everything. Proved by stopping the detector for real:
  `kubectl -n security scale deploy/crowdsec-agent --replicas=0` took the tile red within a minute of
  the agent's series going stale, and scaling it back turned it orange again.
- **1** — any ban active, or more than 20 failed sign-ins in 15 minutes. Live at creation: three bans
  were in force.
- **0** — none of the above; the ban term was neutralised and the tile read green.

The first version's top term counted `ALERTS{alertname=~"Security.*"}` and was dead on arrival:
Grafana evaluates every rule in this cluster, so Prometheus's `ALERTS` family does not exist and that
term could never be true. It was replaced by the three signals the rules are written on, which is why
the red state above was proved by a real cause rather than by a probe.

85 % is the top of the measured hourly band (0.33–81.13 % over 92 samples, 2026-11-21 to 2026-11-25)
plus a margin. A 20 % threshold was measured against and dropped: 42 of those 92 hours sat above it,
because routine bot bursts against the public names push the share to 60–80 % without anything being
wrong.

## What the panels do not say

- **Top addresses refused is who was *answered* 401–499, not who the bouncer dropped.** A dropped
  request never reaches Traefik, so it has no line in the access log. The exact ban list is
  `cscli decisions list`, and the count is `Bans active`.
- **Sign-in outcomes carry no address.** The exporter reads authentik's event API and publishes a
  count per outcome; it never publishes who. `homelab_authentik_signins_15m_by_source`, added for the
  success-after-a-burst rule, is not the exception it looks like: its `source` is a salted hash of the
  address, the salt is a Secret, and the panel still shows no address — reading one back is a lookup,
  not a field.
- **The posture tiles are expected constants, not a judgement.** 73 routes, 0 ungated, 9 public hosts
  and 9 privileged controllers were read from the cluster on 2026-11-25; the privileged count was read
  again on 2026-11-26 at 8, and 7 once mktxp's flag is dropped. A tile that moves is a change
  to the cluster, and `SecurityExposureDrift` is the rule that says so.

## Restoring after a wipe

```bash
set -a; . ~/.config/secrets/homelab.env; set +a
B=https://grafana.kub.local
CA=~/.config/secrets/kub-local/local-ca.pem

curl -s --cacert "$CA" -X POST -H "Authorization: Bearer $GRAFANA_TOKEN" \
  -H 'Content-Type: application/json' --data @dashboard.json "$B/api/dashboards/db"
```

The `folderUid` in the body is the folder the screen belongs to; the folder itself has to exist first.
`overwrite: true` makes the call idempotent, so restoring over an edited copy is the same command.
