# Cloud services observability — design

2026-11-20. Status: awaiting operator approval.

## 1. What this is, and why

Nothing in the homelab watches the services that are not in the cluster. The
public site, the DNS zones that make mail work, and the certificates the
outside world sees are all unmonitored; `docs/reference/alerts.md` lists the
hole as "No blackbox probe, no certificate check outside Traefik."

This design closes it with the standard tool: a `blackbox_exporter` that
probes the public names from inside the cluster using a **public resolver**,
asserts the DNS records that matter for mail and delegation, and feeds three
Grafana rules into the existing Discord route. The money side is configured at
the provider: OVHcloud's own cloud-project billing alerts, which need no
service to run and no credentials in the cluster.

Scope agreed with the operator on 2026-11-20: probes plus OVH-native billing
alerts (approach A). A billing exporter with Grafana panels (approach B) and a
single custom exporter doing everything (approach C) were considered and
rejected; see §4.

## 2. What exists today

| Cloud piece | Live state, verified 2026-11-20 | Watched by |
|---|---|---|
| `ashudev.com` zone | OVHcloud, nameservers `dns13.ovh.net` / `ns13.ovh.net` | nothing |
| `ashudev.com` website | the homelab: apex A `80.14.57.209`, `www` CNAME to the apex, served by Traefik | nothing |
| Mail records | Google Workspace: five MX, SPF `_spf.google.com`, DKIM at `google._domainkey`, DMARC `p=none` | nothing |
| `ashuvidz.com` zone | still AWS Route 53 (`ns-1029.awsdns-00.org` and three others); apex and wildcard A `80.14.57.209` | nothing |
| `ashuvidz.com` website | homelab public IP; from the LAN the NAT hairpin works (HTTP 200) | nothing |
| OVH cloud project `AshuLab` | created 2026-11-20; usage €0.00; one 2026 bill of €16.19 (March) | nothing |
| AWS | only the $15 budget, destroyed in migration Task 16 | AWS email |

Two facts decide the shape of the prober:

- **The local resolver rewrites `ashudev.com` to `192.168.1.46`.** A pod using
  cluster DNS would land on Traefik, whose certificate does not cover the
  name, and report a TLS failure that has nothing to do with the site.
- **The props must be answered by the authoritative servers**, not the local
  resolver, or a DNS assertion tests the Pi-hole rather than the zone.

## 3. Goals, non-goals, anti-goals

**Goals**

1. A public name that stops answering, or serves a broken or expiring
   certificate, notifies Discord within roughly five minutes (§6 states the
   measured worst case).
2. A mail-critical record that disappears (MX, SPF, DKIM, DMARC) notifies.
3. A zone whose authoritative servers stop answering, or whose delegation
   changes, notifies.
4. The spend on the OVH cloud project emails the operator before it becomes a
   surprise.
5. Every probe target is visible in git and in one dashboard panel; adding a
   target is a values-file edit.

**Non-goals**

- A spend dashboard or spend history. That is approach B, rejected.
- Mail *delivery* testing. DNS records are asserted; whether a message arrives
  is not.
- Zone-versus-git drift. `infra-main/scripts/zone-diff.py` and CI own that.
- AWS. The budget is deleted with the migration.
- Probing internal `*.kub.local` names. Traefik's own certificate metric
  already covers them (`CertificateExpiring`).
- Any change to the AWS budget, the DNS zones, or the website itself.

**Anti-goals**

- A probe that resolves through the cluster resolver and therefore tests the
  homelab instead of the internet.
- A second alert channel for uptime, or rules that notify outside Grafana.
- Dashboards or rules in git: they live in Grafana, and this change edits them
  through its API like every other rule.
- A floating image tag or an unpinned upstream chart.

## 4. Approaches considered

| | What it is | Trade-off | Verdict |
|---|---|---|---|
| **A** | Blackbox probes plus OVH-native billing alerts | Two channels (Discord for uptime, email for spend), no spend history | **chosen** |
| **B** | A + a small OVH billing exporter into Prometheus | One channel and a spend panel; one more process, one more Secret, OVH API credentials in the cluster | rejected: cost outweighs the panel |
| **C** | One custom exporter for HTTP, DNS and billing | Fewest processes; re-implements blackbox and owns the most code | rejected |

A is chosen because the money question is answered by the provider itself:
OVHcloud sells the alert, runs it when the cluster is down, and needs no
credential anywhere near the homelab. The operator confirmed A on 2026-11-20.

## 5. The prober

### 5.1 Chart

New wrapper chart at `cluster/helm/monitoring/blackbox-exporter`, pattern D:

- `Chart.yaml` pins `prometheus-community/prometheus-blackbox-exporter`
  **11.21.3** (app `v0.29.1`).
- `values.yaml` pins the image repository and tag
  `quay.io/prometheus/blackbox-exporter:v0.29.1`, sets the module
  configuration, one replica on `nas`, and the service on 9115. It sets
  `fullnameOverride: blackbox-exporter`, so the Service really is
  `blackbox-exporter.monitoring.svc:9115` as used below; without it the
  upstream chart names it `<release>-prometheus-blackbox-exporter`.
- `charts/` is already gitignored for wrapper charts; ArgoCD runs
  `helm dependency build` at sync time.
- Resources: request `10m` CPU / `32Mi` memory, limit `64Mi` memory, no CPU
  limit (house rule).
- Pod annotation `prometheus.io/scrape: "true"` on port 9115, so the
  exporter's own `/metrics` arrives through the normal annotation job and
  `ScrapeTargetDown` covers it.

The pod runs with:

```yaml
dnsPolicy: None
dnsConfig:
  nameservers: ["1.1.1.1", "9.9.9.9"]
  searches: []
  options:
    - name: ndots
      value: "1"
```

This is the public-resolution fix. It does not need cluster DNS for anything:
Prometheus reaches the Service by IP, and the probes only need public names.

### 5.2 Modules

`http_2xx`: follow redirects, `valid_status_codes: [200]`, `preferred_ip_protocol: ip4`,
10 s timeout. One module serves all HTTP targets.

DNS modules, one per assertion (`query_name` is per-module, so one module is
the granularity):

| Module | Query | Assertion |
|---|---|---|
| `dns_soa_ashudev` | `ashudev.com` SOA | answers (liveness of the server) |
| `dns_a_ashudev` | `ashudev.com` A | `80\.14\.57\.209` |
| `dns_www_ashudev` | `www.ashudev.com` CNAME | `ashudev\.com` |
| `dns_mx_ashudev` | `ashudev.com` MX | at least one `(aspmx\|alt\d\.aspmx)\.l\.google\.com` |
| `dns_spf_ashudev` | `ashudev.com` TXT | `_spf\.google\.com` |
| `dns_dkim_ashudev` | `google._domainkey.ashudev.com` TXT | `v=DKIM1; k=rsa` |
| `dns_dmarc_ashudev` | `_dmarc.ashudev.com` TXT | `v=DMARC1` |
| `dns_ns_ashudev` | `ashudev.com` NS | `ovh\.net` |
| `dns_soa_ashuvidz` | `ashuvidz.com` SOA | answers |
| `dns_a_ashuvidz` | `ashuvidz.com` A | `80\.14\.57\.209` |
| `dns_ns_ashuvidz` | `ashuvidz.com` NS | answers (provider not asserted; see below) |

Assertions use `validate_answer_rrs.fail_if_none_matches_regexp`, which the
exporter applies to each answer in its wire form.

**Corrected 2026-11-20, during live verification.** The site moved off GitHub
Pages to the homelab that evening: the authoritative servers and all three
public resolvers return `80.14.57.209`, and `www` is a CNAME to the apex. The
first version of this table was read from `infra-main`, which still declares
the GitHub addresses; the live zone is the authority. The apex A is asserted
exactly at the router address, and `www` at the apex it points to.

`dns_ns_ashuvidz` deliberately asserts only that the delegation answers, not
who answers it. The zone is mid-transfer from AWS to OVHcloud, and asserting
the provider would either fail on the day of the planned flip or need a
throwaway assertion built today. Liveness is the property that matters; the
runbook says the target list changes when the delegation moves, and why that
is the only edit.

### 5.3 Targets and scrape jobs

Two static jobs added under `prometheus.scrapeConfigs` in the Prometheus
wrapper values. Static is deliberate: the convention already allows static
jobs for the edge Pis and for Loki/Grafana, and a multi-target exporter cannot
be discovered by the pod annotation.

| Job | Interval | Targets | Module |
|---|---|---|---|
| `blackbox-http` | 1 m | `https://ashudev.com`, `https://www.ashudev.com`, `https://ashuvidz.com` | `http_2xx` |
| `blackbox-dns` | 5 m | each module against **every** authoritative server of its zone | one per target, carried as a `module` label |

Relabelling is the standard multi-target shape: `__address__` becomes
`__param_target`, `instance` is set from the target plus the check, the real
`__address__` becomes `blackbox-exporter.monitoring.svc:9115`, and a `module`
label is copied to `__param_module`. Each target carries `zone` and `check`
labels so an alert message can name what failed without a lookup.

DNS target list today, which is every server the delegations publish:

- `dns13.ovh.net:53` and `ns13.ovh.net:53` — the eight `ashudev.com` modules;
- `ns-1029.awsdns-00.org:53`, `ns-1714.awsdns-22.co.uk:53`,
  `ns-339.awsdns-42.com:53` and `ns-901.awsdns-48.net:53` — the three
  `ashuvidz.com` modules.

Every authoritative server is probed because one dead server is invisible to a
probe that asks its siblings. That is 28 probes per five minutes (16 for
`ashudev.com`, 12 for `ashuvidz.com`); at the delegation flip the four AWS
addresses become the two OVH ones and the count becomes 22.

## 6. Alerts

Three rules in Grafana's `Homelab` folder, through the provisioning API. Every
expression returns a positive number when firing, per the house pattern; the
notification policy, grouping and Discord contact point are reused unchanged.

| Rule | Expression | for | Group | Severity |
|---|---|---|---|---|
| `CloudEndpointDown` | `1 - probe_success{job="blackbox-http"}` | 5 m | `homelab-core-fast` | critical |
| `CloudTlsCertificateExpiring` | `(probe_ssl_earliest_cert_expiry{job="blackbox-http"} - time()) / 86400 < 21` | 1 h | `homelab-core` | warning |
| `CloudDnsCheckFailing` | `1 - probe_success{job="blackbox-dns"}` | 10 m | `homelab-core-fast` | critical |

- `noDataState: OK` and `execErrState: OK` on all three: no series is the
  healthy case, and a dead Prometheus is already `MonitoringDown`'s message.
- **Timing.** A 1 m probe interval, `for: 5m` and the 30 s `group_wait` put
  the first Discord message at about 6½ minutes after a failure starts. That
  is accepted: it keeps the house 5 m `for` and filters a single failed probe.
  Goal 1's "roughly five minutes" means this number.
- Summaries name the target, the check and the server, and say what to do:
  `CloudEndpointDown` — "https://ashudev.com did not answer from the public
  internet (homelab router and Traefik). Check the route, the public
  certificate and the WAN address." `CloudDnsCheckFailing` — "ashudev.com MX answered by
  ns13.ovh.net does not match Google Workspace's hosts. Read the zone with
  `ovhcloud domain-zone record list ashudev.com`, then compare with
  infra-main."
- `CloudTlsCertificateExpiring` is the outside view of a certificate the
  cluster does not issue for `ashudev.com` (GitHub pages it) and does issue
  for `ashuvidz.com` (Let's Encrypt through Traefik). It stays a warning at 21
  days, matching `CertificateExpiring`.

## 7. Dashboard

One dashboard, `Cloud`, six panels, the screen answers "is the internet-facing
side healthy, and what needs attention?":

| Row | Panels |
|---|---|
| Needs attention | KPI: endpoints answering (x/3) · KPI: DNS checks passing (x/28) · KPI: days to earliest certificate |
| Endpoints | timeseries `probe_success` per endpoint, 24 h · timeseries `probe_duration_seconds` per endpoint |
| Detail | table: endpoint or check, target, server, result, certificate days, duration |

Colour is used for thresholds only (red on `probe_success == 0`, amber under
21 days). The table is the panel a person reads when an alert names one check.

## 8. The money side (OVHcloud)

Configured through the OVH API, not a dashboard:

| Alert configuration | Service | Monthly threshold | Email | Delay |
|---|---|---|---|---|
| `all-20` | `all` | €20 | `valentin.ronteix@ashudev.com` | 1 h |
| `all-50` | `all` | €50 | the same | 1 h |

Evidence for the numbers: the one 2026 bill on the account is €16.19 (March);
the new project's usage is €0.00. €20 is a small margin above the historical
monthly, €50 is the "something is running that should not be" line. The API
repeats no more than once an hour.

**Built 2026-11-20, with one deviation.** `all-20` was created and reads back
correct (id `07f35b7f-f559-472b-8852-93b140034720`). `all-50` was refused by
the API on five attempts, each an HTTP 500 `Server::InternalServerError` from a
different backend, with a request body identical in shape to the one that
succeeded apart from the name and the threshold. The provider, not the
request, is at fault. The €20 threshold still fires on higher spend, so no
condition is unmonitored; only the second signal is missing. A Backlog card
tracks the retry. The earlier claim that the API allows one configuration per
service and threshold is not supported by this evidence and is withdrawn.

The credentials are the legacy application-key trio in `~/ovh.conf` (mode
600; section `[ovh-eu]`): `application_key`, `application_secret`,
`consumer_key`. They are mirrored into `~/.config/secrets/keys.env` as
`OVH_APPLICATION_KEY`, `OVH_APPLICATION_SECRET`, `OVH_CONSUMER_KEY`, which is
what the shell and the OpenCode wrapper load; the two files were compared on
2026-11-20 and hold the same three values. With them loaded, `ovhcloud` and
signed API calls authenticate against `ovh-eu`: `cloud project list`,
`GET /cloud/project/<id>/usage/current` and `GET /me/bill` all answered. The
builder ran the `alerting create` calls and verified with `alerting
list`; no credential value is copied into the repository. This is the
repository's normal agent path (`AGENTS.md` §9, "Configure through the API,
not the operator's mouse"), and the only OVH writes were the configurations
named in the table.

What stays with OVH, unchanged, and is written down rather than replaced:
domain renewal and expiry notices, unpaid-bill dunning, and the invoice
emails. The AWS budget is not touched; migration Task 16 destroys it.

## 9. Documentation

- New `docs/runbooks/cloud-monitoring.md`: what is probed and why, how to add
  a target or a DNS assertion, how to read each of the three alerts, the
  `dns_ns_ashuvidz` flip step, and how to change the OVH thresholds. Added to
  `mkdocs.yml`'s nav in the same commit.
- `docs/reference/alerts.md`: three rules added to the tables, the "no
  blackbox probe" gap removed, the `dns_ns_ashuvidz` trap recorded.
- `docs/reference/dashboards.md`: the `Cloud` dashboard and its panels.
- `docs/observability-conventions.md`: the new exporter, its placement, the
  public-resolver decision, and the two static jobs with their intervals.

## 10. Verification

1. `helm template` renders the new chart, and the existing chart sweep still
   passes (the loop in `AGENTS.md` §6).
2. ArgoCD sync leaves the pod Running on `nas`, and Prometheus `/api/v1/targets`
   lists the new jobs with no down target.
3. Each panel query is executed through the Grafana datasource API and returns
   data; no panel ships on a query that has never returned a row.
4. Each rule is made to fire once against a deliberately broken target (a
   non-existent name added to the values, then removed), showing the target,
   check and server in the message, then resolves.
5. The missing-data case is checked: a module that returns no answer renders
   blank, not zero, in the table and never fires `CloudEndpointDown`.
6. `ovhcloud cloud alerting list --cloud-project <id>` shows both
   configurations.

## 11. Rollback

Delete the Application and the two scrape jobs; the chart directory can stay.
The rules and the dashboard are deleted through the Grafana API. The OVH
configurations are deleted with `ovhcloud cloud alerting delete`. Nothing this
change touches holds state, so rollback is removal, and nothing else in the
cluster depends on it.

## 12. Decisions made for the builder

- **Placement `nas`, with the rest of observability.** The probes leave the
  house, so the node does not change what they see; keeping them beside
  Prometheus keeps the namespace story true.
- **Two resolvers in the pod, not one.** `1.1.1.1` answered from the
  workstation on 2026-11-20; `9.9.9.9` is the second so one resolver outage
  does not raise three false alerts.
- **`www` is probed as its own target**, not assumed to follow the apex: it is
  a CNAME to GitHub, and a broken `www` is a real failure for anyone typing
  it.
- **Every authoritative server is probed for every DNS check.** All four
  `ashuvidz.com` servers are in the list while the delegation is on Route 53;
  the extra probes are cheaper than a silent dead server, and the list shrinks
  to two at the flip.
- **`ashuvidz.com`'s NS record is asserted for liveness, not for provider.**
  The zone is mid-transfer; asserting AWS would be throwaway work and
  asserting OVH would fail until the flip.
- **`for: 5m` stays.** A blip should not page; the 6½-minute worst case is
  recorded in §6 rather than traded for a tighter `for`.
- **The apex A assertion is loose.** Exact-IP assertions on GitHub's addresses
  turn a provider change into a false page.
