# Grafana alert links — design

2026-11-12. Status: implemented the same day; live `appUrl` and the Discord test
confirmed by the operator.

## Why

Discord alerts carry a **Source** and a **Silence** link. Both are built from
Grafana's `root_url`, which is unset, so Grafana falls back to
`http://localhost:3000/`. The operator opens the message on a phone and the
link points at their own device.

Confirmed against the live API on 2026-11-12:

```
GET http://grafana.kub.local/api/frontend/settings
{"appUrl": "http://localhost:3000/", "appSubUrl": ""}
```

The Discord contact point `discord-homelab` is unchanged: it uses the default
message template, which takes the links from that base URL. Fixing the base
URL fixes every alert without touching 25 rules or the contact point.

## Goal

A Discord alert link opens Grafana from any device on the LAN or the
WireGuard link, with no port-forward.

## Decision

Point `root_url` at the existing internal route, `http://grafana.kub.local/`.
The route already exists in the chart (`IngressRoute`, entrypoint `web`,
`Host(\`grafana.kub.local\`)`) and answered HTTP 200 on 2026-11-12.

## Approaches considered

1. **Internal `root_url`** (chosen). Three lines in the wrapper chart's
   `values.yaml`. No new exposure, no certificate, no DNS change.
2. **Public TLS host** (`grafana.kub.home.ashudev.com`, `websecure` +
   `myresolver` + a TLSOption, same as syncthing/n8n). Works from anywhere,
   but publishes Grafana's login page to the internet. Rejected by the
   operator in favour of LAN-only.
3. **Custom Discord message template with hand-built links.** Duplicates the
   contact point's logic, has to be maintained per channel, and does not fix
   the UI's own links. Rejected.

## Change

In `cluster/helm/monitoring/grafana/values.yaml`, under `grafana:`:

```yaml
  grafana.ini:
    server:
      root_url: http://grafana.kub.local/
      domain: grafana.kub.local
```

The upstream chart renders this into the `grafana` ConfigMap; the Deployment
already carries a `checksum/config` annotation, so the pod rolls once.

`docs/observability-conventions.md` gains one line under Grafana: the base URL
and the fact that it drives alert links.

## Verification

1. `helm template` the wrapper chart and confirm both keys land in the
   ConfigMap.
2. After sync: `GET /api/frontend/settings` returns
   `appUrl: http://grafana.kub.local/`.
3. Deliver one test notification from the **Test** button on the contact point
   (Alerting → Contact points → `discord-homelab`). Grafana 13 has no working
   HTTP test route for a receiver created through the legacy provisioning API:
   `/api/v1/provisioning/contact-points/<uid>/test` and
   `/api/alertmanager/grafana/config/api/v1/receivers/<name>/test` return 404,
   and `/apis/notifications.alerting.grafana.app/.../receivers/<name>/test`
   answers "Receiver not found" for it. The operator reads the Discord message
   and checks that Source and Silence open `grafana.kub.local`, not localhost.

## Risks and limits

- Plain HTTP: fine on the LAN, and the link is useless outside it anyway.
- A phone on WireGuard needs Pi-hole (`192.168.1.x`) as its DNS resolver for
  `kub.local` to resolve. That configuration lives on the `rbp0`/`rbp1` hosts,
  not in this repo, and is not changed here.
- Alerting remains a single point of failure: Grafana builds and sends the
  message. Accepted previously, unaffected by this change.
