# Cloud monitoring: the probes outside the cluster

> **In one line.** A `blackbox_exporter` on `nas` makes real HTTPS and DNS requests against the three
> public names and both zones' authoritative servers, so a public face that stops answering, a
> mail-critical record that disappears, or a certificate about to lapse pages Discord like anything
> else in the cluster.
>
> **Use it when** `CloudEndpointDown`, `CloudDnsCheckFailing` or `CloudTlsCertificateExpiring` fires,
> a public name or DNS record changes, or `ashuvidz.com`'s delegation moves to OVHcloud · **Verified**
> 2026-11-20 · **Kind:** living page

A **blackbox probe** is a request made from outside-looking-in — a real HTTPS fetch or DNS query, not
a number read from inside the app — so it answers "does this name answer for anyone?". The probes run
in the cluster, but they read the public internet exactly as a stranger would, which is why they
catch the failures the in-cluster rules cannot.

The component is the wrapper chart `cluster/helm/monitoring/blackbox-exporter` (upstream
`prometheus-community/prometheus-blackbox-exporter` 11.21.3, image
`quay.io/prometheus/blackbox-exporter:v0.29.1`), one replica on `nas`, Service
`blackbox-exporter.monitoring:9115`. Design:
[`2026-11-20-cloud-services-observability-design.md`](../superpowers/specs/2026-11-20-cloud-services-observability-design.md).

## 1. What is watched

Every module lives in `cluster/helm/monitoring/blackbox-exporter/values.yaml`: a module is the
question (probe HTTPS, ask for the MX record) together with what a good answer looks like. Every
target lives in `cluster/helm/monitoring/prometheus/values.yaml`, in the job named in the last
column.

| Target | Module | Assertion | Job |
|---|---|---|---|
| `https://ashudev.com` | `http_2xx` | a final `200` after redirects, IPv4 only | `blackbox-http` |
| `https://www.ashudev.com` | `http_2xx` | the same | `blackbox-http` |
| `https://ashuvidz.com` | `http_2xx` | the same | `blackbox-http` |
| `ashudev.com` SOA | `dns_soa_ashudev` | the server answers at all; no record is asserted | `blackbox-dns` |
| `ashudev.com` A | `dns_a_ashudev` | `80.14.57.209`, the router address | `blackbox-dns` |
| `www.ashudev.com` CNAME | `dns_www_ashudev` | points at `ashudev.com` (a **CNAME** is one name that points at another) | `blackbox-dns` |
| `ashudev.com` MX | `dns_mx_ashudev` | at least one `aspmx.l.google.com` or `altN.aspmx.l.google.com` | `blackbox-dns` |
| `ashudev.com` TXT | `dns_spf_ashudev` | contains `_spf.google.com` | `blackbox-dns` |
| `google._domainkey.ashudev.com` TXT | `dns_dkim_ashudev` | contains `v=DKIM1; k=rsa` | `blackbox-dns` |
| `_dmarc.ashudev.com` TXT | `dns_dmarc_ashudev` | contains `v=DMARC1` | `blackbox-dns` |
| `ashudev.com` NS | `dns_ns_ashudev` | contains `ovh.net` | `blackbox-dns` |
| `ashuvidz.com` SOA | `dns_soa_ashuvidz` | the server answers at all | `blackbox-dns` |
| `ashuvidz.com` A | `dns_a_ashuvidz` | `80.14.57.209` | `blackbox-dns` |
| `ashuvidz.com` NS | `dns_ns_ashuvidz` | the server answers at all; the provider is deliberately not asserted, see [§5](#5-the-ashuvidzcom-delegation-flip) | `blackbox-dns` |

Each DNS module is asked of **every authoritative server that zone publishes** — an authoritative
server being the one that holds the zone's real records. Today that is `dns13.ovh.net` and
`ns13.ovh.net` for `ashudev.com` (8 checks × 2 servers = 16 probes) and the four Route 53 servers
`ns-1029.awsdns-00.org`, `ns-1714.awsdns-22.co.uk`, `ns-339.awsdns-42.com` and
`ns-901.awsdns-48.net` for `ashuvidz.com` (3 × 4 = 12), so **28 DNS probes every five minutes**, plus
3 HTTP probes every minute. Asking all of them is the point: a dead secondary is invisible to a probe
that only asks the primary.

The DNS job carries no `params:` block on purpose. The module comes from each target's `module`
label, copied to `__param_module` by a relabel rule. Adding a job-level `params: module: [...]` would
override that and run every target through one module — 28 green probes that test one question.

## 2. Why the prober has its own resolvers

Pi-hole answers `ashudev.com` with the LAN address `192.168.1.46`, and since 2026-11-20 that name is
served by this homelab's Traefik. A pod resolving through cluster DNS would land on Traefik, whose
certificate does not cover `ashudev.com`, and report a TLS failure that has nothing to do with the
public site — or, worse, report green for a public zone that is broken. The pod therefore runs with
`dnsPolicy: None` and its own resolvers, `1.1.1.1` and `9.9.9.9`:

```bash
grep -A8 'dnsPolicy' cluster/helm/monitoring/blackbox-exporter/values.yaml
```

Two resolvers, not one, so a resolver outage does not raise three false endpoint alerts. The exporter
needs nothing from cluster DNS: Prometheus reaches it by Service name, and the DNS probes are pointed
at authoritative servers by name, which those resolvers resolve. A `dnsPolicy: ClusterFirst` in a
rendered manifest is a regression — render and look before committing a change to that chart
(`charts/` is gitignored, so `helm dependency build` runs first):

```bash
helm dependency build cluster/helm/monitoring/blackbox-exporter
helm template test cluster/helm/monitoring/blackbox-exporter \
  -f cluster/helm/monitoring/blackbox-exporter/values.yaml | grep -A6 dnsPolicy
```

## 3. Reading each alert

All three read series Prometheus writes from the prober, all three reuse the `discord-homelab`
contact point and the existing notification policy, and all three are `noDataState: OK`. That last
part has one consequence worth knowing before you debug: a rule whose selector matches nothing looks
identical to a healthy rule. The proof that the selectors still match is two counts. The Grafana
calls below need the token, so load it first — `-k` is there because `grafana.kub.local` is served
by the internal CA, so a machine without that CA in its trust store still answers:

```bash
set -a; . ~/.config/secrets/homelab.env; set +a
curl -sG http://prometheus.kub.local/api/v1/query \
  --data-urlencode 'query=count(probe_success{job="blackbox-http"})'    # 3
curl -sG http://prometheus.kub.local/api/v1/query \
  --data-urlencode 'query=count(probe_success{job="blackbox-dns"})'     # 28
```

A `0` there means the probes are not arriving and every rule below is quiet for the wrong reason; go
to the `blackbox-exporter` pod and `ScrapeTargetDown` first.

Timing: an HTTP probe runs every minute, so with `for: 5m` and the 30 s `group_wait` the first
Discord message about `CloudEndpointDown` lands about **6½ minutes** after a failure starts. A DNS
probe runs every five minutes, so with `for: 10m` and the same `group_wait` the first message about
`CloudDnsCheckFailing` lands about **15 minutes** after it starts.

### `CloudEndpointDown`

Critical, `for: 5m`, group `homelab-core-fast`, rule uid `ad5e038b-89d5-45e6-b90f-67445151890c`.

**Means:** one of the three names did not return `200` over HTTPS from the cluster — a refusal, a
timeout, a non-200 status, a certificate the client rejects, or a DNS failure. The cluster's view is
the internet's view here because the router forwards 80/443 to Traefik; the endpoint is the same
router for all three names, so "all three at once" is a WAN or Traefik problem, and "one" is that
name's route, certificate or DNS.

**Check:** which `instance` the alert names, then the series and the certificate clock:

```bash
curl -sG http://prometheus.kub.local/api/v1/query \
  --data-urlencode 'query=probe_success{job="blackbox-http"}'
curl -sG http://prometheus.kub.local/api/v1/query \
  --data-urlencode 'query=(probe_ssl_earliest_cert_expiry{job="blackbox-http"} - time()) / 86400'
curl -sG http://prometheus.kub.local/api/v1/query \
  --data-urlencode 'query=probe_duration_seconds{job="blackbox-http"}'
```

`probe_success` at `0` is the failure; a duration near the 10 s module timeout means the request hung
rather than was refused. Confirm from outside the LAN with `curl -sSI https://ashudev.com | head -1`
and, for a resolution problem, `dig +short ashudev.com @1.1.1.1`. Then follow
[`routes.md`](../reference/routes.md) for the Traefik router and
[`internal-ca.md`](internal-ca.md) only if the failure is a `*.kub.local` name, which this rule does
not cover.

### `CloudDnsCheckFailing`

Critical, `for: 10m`, group `homelab-core-fast`, rule uid `161a17b1-d862-4d0d-9410-7c102d1c15de`.

**Means:** one check on one authoritative server answered nothing, answered with an error, or
answered with a record that does not match the assertion. The alert names three labels: `zone`, the
`check` (`a`, `mx`, `spf`, `dkim`, `dmarc`, `ns`, `soa`, `www`), and `instance`, the server.

**Check:** the per-check series first, then the zone itself. `ashudev.com` is served by OVHcloud,
whose `ovhcloud` CLI credentials are set out in [§6](#6-the-money-alerts):

```bash
curl -sG http://prometheus.kub.local/api/v1/query \
  --data-urlencode 'query=probe_success{job="blackbox-dns"} == 0'
ovhcloud domain-zone record list ashudev.com
dig +short MX ashudev.com @ns13.ovh.net
```

One server failing and its sibling passing is a provider problem on that server. Both failing for
one check is a record problem. `ashuvidz.com` is still delegated to Route 53, so `ovhcloud domain-zone`
will not answer for it until the zone moves: read it through `dig` at one of the four AWS servers, or
in the Route 53 console.

`dns_ns_ashuvidz` asserts liveness only, on purpose: the zone is mid-transfer, so asserting AWS would
be throwaway work and asserting OVH would fail until the flip. It catches a delegation that stops
answering, not one that answers from the wrong provider — the flip in [§5](#5-the-ashuvidzcom-delegation-flip)
is what changes that, if you decide it should.

A missing `MX`, `SPF`, `DKIM` or `DMARC` record breaks mail silently — `MX` says where a domain's
mail goes, and `SPF`, `DKIM` and `DMARC` say what is allowed to send as that domain. That is the
failure this rule exists for: the site can look fine for days while mail bounces.

### `CloudTlsCertificateExpiring`

Warning, `for: 1h`, group `homelab-core` (the five-minute group), rule uid
`b8e24b82-fbe6-4dbb-a2da-897c61673cbb`.

**Means:** the earliest certificate a probe saw on an endpoint has **fewer than 21 days left**. This
rule differs from its neighbours in one way that trips readers: its value is the number of days
remaining, and the threshold is **below 21**, not "above zero". `CertificateExpiring` watches the
cluster's own Traefik certificates; this one watches what a stranger is served for the public names.

**Check:** which `instance`, then the day count per endpoint:

```bash
curl -sG http://prometheus.kub.local/api/v1/query \
  --data-urlencode 'query=(probe_ssl_earliest_cert_expiry{job="blackbox-http"} - time()) / 86400'
```

Measured 2026-11-20: about 90 days for `ashudev.com` and `www.ashudev.com`, about 69 for
`ashuvidz.com` — all three on the roughly 90-day renewal cycle of a Let's Encrypt certificate, which
Traefik renews long before 21 days remain (the rule's own description says so). A value below 21
therefore means the issuer is failing, not the site: check `CertificateExpiring` and Traefik's ACME
certificate store. The read-only rule object:

```bash
curl -sk -H "Authorization: Bearer $GRAFANA_TOKEN" \
  https://grafana.kub.local/api/v1/provisioning/alert-rules/b8e24b82-fbe6-4dbb-a2da-897c61673cbb
```

## 4. Adding a target

Two files change for a new DNS assertion, one for a new HTTP name. No Grafana rule changes either
way: the rules select by job, so a new target is covered the moment its first sample lands.

1. **The module**, in `cluster/helm/monitoring/blackbox-exporter/values.yaml`, only for a new DNS
   question. One module per assertion, because `query_name` is per module. Put the expected answer
   under `dns.validate_answer_rrs.fail_if_none_matches_regexp`; matching is on the answer's wire form,
   so TXT answers carry their quotes.
2. **The target**, in `cluster/helm/monitoring/prometheus/values.yaml`. An HTTP name is one
   `static_configs` entry in `blackbox-http` with `labels: {check: http}`; a DNS assertion is one entry
   per authoritative server in `blackbox-dns`, each with `labels: {module: <module>, check: <short
   name>, zone: <zone>}`. Keep the DNS job free of `params:`.
3. **Two numbers in Grafana**, which are literals in panel titles, not queries: `Endpoints answering
   (of 3)` and `DNS checks passing (of 28)` on the `Cloud` dashboard. Edit the titles through
   `POST /api/dashboards/db`, or the panel will claim the wrong denominator.

Then commit, let ArgoCD sync, and confirm within one interval (a minute for HTTP, five for DNS) that
the new series exists at all:

```bash
curl -sG http://prometheus.kub.local/api/v1/query \
  --data-urlencode 'query=probe_success{job="blackbox-http",instance="https://example.com"}'
```

No series is a different fault from `probe_success 0`: it means the target is missing from the job,
the module name does not match a module in the chart, or the pod is not being scraped. The `Every
check` table on the `Cloud` dashboard shows what Prometheus actually holds, failures first.

## 5. The `ashuvidz.com` delegation flip

**Delegation** is which servers the registry points the domain at, published as the zone's `NS`
records. `ashuvidz.com` is delegated to four AWS Route 53 servers and is moving to OVHcloud
(`ashudev.com`'s zone is already there, on `dns13.ovh.net` and `ns13.ovh.net`). Twelve of the 28 DNS
probes are `ashuvidz.com` checks run against those four AWS servers.

When the delegation moves:

1. Read the new servers from the answer, not from memory: `dig +short NS ashuvidz.com @1.1.1.1`.
2. In `cluster/helm/monitoring/prometheus/values.yaml`, replace the four `ashuvidz.com` server blocks
   (12 entries) with the two OVH servers (6 entries), keeping `module`, `check` and `zone` labels
   unchanged. The job goes from 28 targets to **22**.
3. Edit the `DNS checks passing (of 28)` title to `(of 22)`.
4. The same count appears in three other pages, edit them: the `28` in the "How metrics get in"
   section of [`observability-conventions.md`](../observability-conventions.md#how-metrics-get-in),
   the `count(probe_success{job="blackbox-dns"})` = 28 in
   [`reference/alerts.md`](../reference/alerts.md#alerting), and the "DNS checks passing of 28" in
   [`reference/dashboards.md`](../reference/dashboards.md).
5. Nothing beyond that: no module changes, no rule changes, no dashboard query changes, because the
   rules select `job="blackbox-dns"` and name the target from its labels.

`dns_ns_ashuvidz` stays liveness-only. Its assertion is "this delegation answers", not "OVH answers
it", which is what makes the flip a target-list edit rather than a module edit. Asserting the new
provider afterwards is a separate decision, taken deliberately, not as a side effect of the move.

Expect one page if the steps are not close together: between the delegation change and the target
commit, the twelve AWS probes fail **if the old servers stop answering the zone**, and
`CloudDnsCheckFailing` fires after ten minutes. Check that the new servers answer the zone *before*
committing (`dig +short SOA ashuvidz.com @<new server>`), then commit — the remaining window is the
commit-to-sync delay, inside one scrape interval if the sync is quick.

## 6. The money alerts

The spend side is configured at OVHcloud, not in the cluster. It fires whether or not the homelab is
up, needs no credential anywhere near Prometheus, and sends email rather than Discord.

**One configuration exists**, in the cloud project `a93a624a514243a6a557863c90a5379e`:

| Field | Value |
|---|---|
| Name | `all-20` |
| Id | `07f35b7f-f559-472b-8852-93b140034720` |
| Service | `all` (every service in the project) |
| Monthly threshold | €20 |
| Email | `valentin.ronteix@ashudev.com` |
| Delay between alerts | 3600 s, the minimum: at most one mail an hour |

```bash
ovhcloud cloud alerting list --cloud-project a93a624a514243a6a557863c90a5379e -o json
```

Change the threshold or the address with `edit`, remove it with `delete`, and create a new one only
for a service that has none (see the limit below):

```bash
ovhcloud cloud alerting edit 07f35b7f-f559-472b-8852-93b140034720 \
  --cloud-project a93a624a514243a6a557863c90a5379e --monthly-threshold 30
ovhcloud cloud alerting delete 07f35b7f-f559-472b-8852-93b140034720 \
  --cloud-project a93a624a514243a6a557863c90a5379e
```

Credentials are the legacy application-key trio in `~/ovh.conf` (mode 600, section `[ovh-eu]`),
mirrored as `OVH_APPLICATION_KEY`, `OVH_APPLICATION_SECRET` and `OVH_CONSUMER_KEY` in
`~/.config/secrets/keys.env`.

**The API keeps one configuration per service, so a second global threshold cannot exist.** This was
retried on 2026-11-22 and the earlier reading is now explained, not merely repeated. With `all-20` in
place, `create --service all` fails at any threshold — €50 on 2026-11-20 and again on 2026-11-22,
€9999 the same day — always HTTP 500 `Server::InternalServerError`, with a fresh `X-OVH-Query-Id` each
time (`EU.ext-4.6b0339ee.1014459.1c89b147…`, `EU.ext-4.6b033a02.1014459.b8f34e7a…`) and in under
100 ms, so the request is rejected, not dropped. The same call for a service that has no
configuration succeeds: `--service instances` at €9999 returned 200, and `block_storage` did too while
two configurations already existed. A second `instances` configuration then failed the same way,
which is what makes the rule "one per service" rather than "one per project": the request shape and
the threshold never mattered, only whether that service already had a configuration. Every probe was
deleted afterwards with `alerting delete`, and `alerting list` reads back the one `all-20`.

The consequence is small but real: the €50 "something is running that should not be" line cannot sit
beside a €20 line for the same project-wide scope. One global threshold is what the provider allows,
and €20 is the one kept. Nothing is unmonitored — crossing €50 crosses €20 and sends the mail — only
the second signal is missing. A second signal would need OVHcloud to allow two configurations on one
service scope; no support ticket is open, and the query IDs above are what a ticket would quote.

What stays with OVHcloud, unchanged: domain renewal and expiry notices, unpaid-bill reminders, and
invoice mail. The AWS budget is not part of this and was destroyed with the migration.

## 7. Rollback

Nothing in this change holds state, so rollback is removal, in this order — rules first, so you never
leave a rule that can no longer fire behind:

1. **The three rules**, through the Grafana API:

   ```bash
   for uid in ad5e038b-89d5-45e6-b90f-67445151890c \
              161a17b1-d862-4d0d-9410-7c102d1c15de \
              b8e24b82-fbe6-4dbb-a2da-897c61673cbb; do
     curl -sk -o /dev/null -w '%{http_code}\n' -X DELETE \
       -H "Authorization: Bearer $GRAFANA_TOKEN" \
       "https://grafana.kub.local/api/v1/provisioning/alert-rules/$uid"
   done
   ```

2. **The dashboard**: `DELETE /api/dashboards/uid/cloud-services`, same token. Any silence created
   for these rules goes the same way, through `/api/alertmanager/grafana/api/v2/silence/<id>`; none
   exists today.
3. **The two scrape jobs**: delete `blackbox-http` and `blackbox-dns` from
   `cluster/helm/monitoring/prometheus/values.yaml` and commit. Prometheus reloads on the sync.
4. **The Application**: delete `blackbox-exporter` in ArgoCD, the way the seven retired applications
   went, and remove `cluster/argocd/projects/monitoring/blackbox-exporter.yaml` from git in the same
   change. The chart directory under `cluster/helm/` can stay; nothing renders it once the Application
   is gone.
5. **The OVH configuration**, if the spend alert is going too:
   `ovhcloud cloud alerting delete 07f35b7f-f559-472b-8852-93b140034720 --cloud-project a93a624a514243a6a557863c90a5379e`.

The DNS zones, the AWS budget and the websites are not touched by this change, so there is nothing to
restore there.

## 8. Known limits

- **No mail delivery test.** The records are asserted; whether a message reaches a mailbox is not.
  Google Workspace can accept mail with a broken `DMARC` for a while, and this page will not see it.
- **No zone-versus-git drift.** The probe asserts what the zone serves *now*. A record edited in the
  console and never committed looks green here; `infra-main` owns the comparison with git.
- **No spend dashboard or history.** The €20 email is the only spend signal, and a second threshold
  cannot exist: the provider keeps one configuration per service scope (see
  [§6](#6-the-money-alerts)). Spend over time would need the exporter that was considered and
  rejected.
- **A dead prober is quieter than a dead site, by design.** If the `blackbox-exporter` pod dies,
  `probe_success` stops being produced and `CloudEndpointDown` goes quiet rather than firing, because
  every rule is `noDataState: OK`. `ScrapeTargetDown` reports the pod instead — the pod carries
  `prometheus.io/scrape`, so it is an ordinary annotation-scraped target. The asymmetry is intended:
  one message about the prober, not three about websites that are probably fine.
- **One place watches, and it is the cluster.** If Prometheus or Grafana is down, the probes still
  run but nothing notifies. That is the alerting single point recorded in
  [`reference/alerts.md`](../reference/alerts.md#known-gaps).
- **The `Cloud` dashboard is the only place these series are drawn**, and it lives in Grafana's
  database like every other dashboard: not in git, and reachable only through a restore of the
  `nodes-nas` stream ([how-it-works/backups.md](../how-it-works/backups.md)).
- **Three names and these records only.** No other port, no other hostname, no internal `*.kub.local`
  name — Traefik's own `CertificateExpiring` covers those.
