# Log budget

> **In one line.** What the cluster writes to Loki in a day, which services write it, and the
> selector that reproduces each number — so the next pass can tell a regression from an old event.
>
> **Use it when** you are about to reduce log volume, or a week of logs looks larger than the last
> measurement · **Verified** 2026-11-22 · **Kind:** reference
>
> **Sources** `cluster/helm/monitoring/fluent-bit/values.yaml` (the inputs, filters and outputs that
> decide what is shipped), `cluster/helm/monitoring/loki/values.yaml` (retention and the limits), and
> the metrics page's reader counts. Read through Loki's own HTTP API on 2026-11-22.

The numbers below were taken from Loki's own HTTP API against the live cluster. Nothing here is
estimated: every figure has the query that produced it, and a change is only "fixed" if the number
moved.

## The day

**2026-11-22, 24 hours, `{job="fluent-bit"}`: 533,803 lines, 117.0 MB** uncompressed as Loki counts
them. The pipeline took ~2,269 B/s in and stored ~13.5 MB, because a line is compressed and indexed
by its stream labels rather than kept whole.

About three quarters of the volume is not a signal anyone acts on: health checks logged as requests,
a log level left at `info`, and three objects applied by hand that nothing in Git owns.

### The host journal, pending

Fluent Bit also ships each node's own journal in the change that adds the security detection (sshd
auth failures as `unit="ssh"`, the kernel's ufw block lines as `unit="kernel"`, `source="journal"`).
**Its 24-hour line count has not been measured:** the pipeline has never run, so the query answers
nothing and no number on this page covers it. Record it **on or after 2026-11-26**, once the sync has
landed and a day has passed, with both halves — lines and bytes, because the decision below is about
the store rather than the line count:

```bash
curl -skG -H "Authorization: Bearer $GRAFANA_TOKEN" \
  "https://grafana.kub.local/api/datasources/proxy/uid/P8E80F9AEF21F6940/loki/api/v1/query" \
  --data-urlencode 'query=sum by (unit) (count_over_time({source="journal"}[24h]))'
curl -skG -H "Authorization: Bearer $GRAFANA_TOKEN" \
  "https://grafana.kub.local/api/datasources/proxy/uid/P8E80F9AEF21F6940/loki/api/v1/query" \
  --data-urlencode 'query=sum by (unit) (bytes_over_time({source="journal"}[24h]))'
```

Read through the same proxy on 2026-11-25, `{job="fluent-bit"}` answered 176,940 lines over 24 hours
while `{source="journal"}` and `{source="accesslog"}` both answered nothing — the two streams this
change adds have never run, so their absence is a measurement rather than a failed query. That
176,940 is well below the 533,803 of the day above; this page does not yet write up what moved it, so
the headline stays the baseline and that number is only proof the selector answers.

The decision rule, one comparator and one unit: the store for the day above is **13.5 MB** compressed,
so the journal may add **1.35 MB a day** (10 %), and **13.5 MB a day** would double it. Past the
second figure the `ssh` input gains a line filter on `Failed` — an iteration, not a redesign.

A `unit="ssh"` count of zero is the case to act on, not the calm one: it means the filter matched no
daemon on that node. The input selects `_COMM=sshd` rather than `_SYSTEMD_UNIT=ssh.service` because
Ubuntu socket-activates sshd since 22.10, where each connection is a transient `ssh@<n>-<addr>.service`
and a unit filter ships nothing — but a container that cannot read the journal, or a node whose
journal holds no `sshd` line, fails just as quietly, and the count is what says so.

## How to measure it

Loki is behind `authentik-forwardauth` since 2026-11-22, so a script asks through Grafana's
datasource proxy: prefix every path in the table below with
`https://grafana.kub.local/api/datasources/proxy/uid/P8E80F9AEF21F6940`, and send the token.

```bash
curl -skG -H "Authorization: Bearer $GRAFANA_TOKEN" \
  "https://grafana.kub.local/api/datasources/proxy/uid/P8E80F9AEF21F6940/loki/api/v1/query" \
  --data-urlencode 'query=sum(count_over_time({job="fluent-bit"}[24h]))'
```

| Question | Call |
|---|---|
| Lines per service in a window | `/loki/api/v1/query` with `sum by (namespace, container) (count_over_time({job="fluent-bit"}[24h]))` |
| Bytes per service | same, `bytes_over_time` |
| One pattern's count | `sum(count_over_time({job="fluent-bit"} \|= "the text" [24h]))` |
| Which labels exist | `/loki/api/v1/labels`, then `/loki/api/v1/label/<name>/values` |
| Totals by label, without a full scan | `/loki/api/v1/index/volume_range` (per-label totals only; `limit` above 5000 answers 400) |

`| detected_level="error"` works without a parser: Fluent Bit's Kubernetes filter fills the level
when the app logs JSON or a level word. Use `query_range` when the shape over time matters —
`query` returns one number per series for the whole window.

## The top ten

24 hours to 2026-11-22. `% lines` and `% bytes` are of the 533,803 and 117.0 MB above.

| Service | Lines | % lines | MB | % bytes | What it is |
|---|---:|---:|---:|---:|---|
| `news/freshrss` | 112,283 | 21.0 | 22.6 | 19.3 | 103,617 are `oidc_check_x_forwarded`, three per readiness probe |
| `authentik/server` | 28,386 | 5.3 | 16.3 | 13.9 | one request line per probe, at the default `info` |
| `monitoring/prometheus-smartctl` | 1,729 | 0.3 | 13.2 | 11.3 | the whole smartctl JSON echoed on exit code 32 |
| `authentik/worker` | 23,508 | 4.4 | 12.4 | 10.6 | health checks at `info` |
| `kube-system/traefik` | 12,494 | 2.3 | 9.2 | 7.8 | access log, deliberate (card 544) |
| `vikunja/vikunja` | 33,184 | 6.2 | 6.4 | 5.5 | 52 % is the `/api/v1/info` probe at `info` |
| `default/loop-device-cleaner` | 82,000 | 15.4 | 4.6 | 4.0 | a hand-applied shell loop, one line per device per minute |
| `gpu-operator/gpu-operator` | 23,655 | 4.4 | 3.5 | 3.0 | `--zap-log-level=info`, six lines per 30-second reconcile |
| `media-stack/maintainerr` | 10,598 | 2.0 | 3.1 | 2.7 | the nightly scan, ~12k warnings at 00:00 |
| `kube-system/intel-gpu-plugin` | 17,278 | 3.2 | 2.6 | 2.2 | a warning every 5 s: no Intel GPU exists |

Two more sit just outside the ten and matter as much:

- `node-feature-discovery/nfd-worker` — 17,078 lines, the second NFD in the cluster. Its
  `failed to get DMI entry` on `rbp2` alone was 2,878 a day.
- `docs/builder` — 44,717 lines, 8.4 % of all lines. That one is by design: the one-minute CronJob
  lists every repository and ref it publishes.
- The nginx pair plus pgAdmin — 50,000 lines between them, of which 61–99 % are `kube-probe` access
  lines. Across the whole cluster, **84,903 lines (15.9 %) contain `kube-probe`**.

## Three classes, three fixes

**A level left at `info`.** The chart contract (`docs/charts/README.md`, Logs) says warnings and
errors. authentik, vikunja and the GPU operator were at the default, and their probes are what fills
the log. The lever is each app's own level: `AUTHENTIK_LOG_LEVEL=warning`,
`VIKUNJA_LOG_LEVEL=WARNING`, `operator.logging.level: error`. What an operator acts on — sign-ins,
admin actions, workflow outcomes — lives in the application's own record (authentik's Events table,
n8n's executions), not in the request log.

**A probe that trips an application check.** FreshRSS was the largest single source, and it was not
a level choice: mod_auth_openidc warns when `X-Forwarded-Host/Port/Proto` are missing, and a kubelet
probe does not carry them while Traefik does. The probe now sends the three headers. The same class
has a second shape: an nginx access log that records the prober. A `map $http_user_agent` plus
`access_log … if=` keeps every real request and drops the prober's.

**An object nobody owns.** `intel-gpu-plugin`, the second `nfd-worker` and `loop-device-cleaner`
were applied by hand, carry only the `last-applied-configuration` annotation, and appear in no
Application. They are the reason a log pass is also an inventory pass: deleting them is the fix, and
the cluster stops carrying a `devel` image that no Renovate rule can see.

## Accepted exceptions

These are recorded so a later pass does not re-open them:

- **`argocd/repo-server`: `found in Chart.yaml, but missing in charts/ directory`, ~668 a day.**
  ArgoCD's first `helm template` runs before `helm dependency build`, so every wrapper chart fails
  once and then succeeds. The Applications stay Synced. Not a defect; the mechanism, the 24-hour
  counts and how to tell a stalled render from an old sync operation are in
  [`runbooks/argocd.md`](../runbooks/argocd.md#wrapper-charts-and-their-dependency-build).
- **Grafana's search manifest watcher.** Grafana 13 warns twice per poll when no app plugin
  publishes a manifest, which is the normal state here. There is no setting to stop the poll; the
  logger is filtered to errors in `cluster/helm/monitoring/grafana/values.yaml`. Upstream issue
  `grafana/grafana#137058`.
- **`prometheus-smartctl` echoing its JSON.** The v2.5.0 exporter prints the whole smartctl output
  whenever the exit code is non-zero, and one disk returns 32 (SMART failing). The volume follows
  the failing disk tracked by card 468; the exporter has no level knob.
- **`docs/builder`.** One line per repository and ref per minute is what the publisher does.
- **Traefik's access log.** It is what proves what reached a public host.

## Bursts, and why the week view lies

A seven-day total is dominated by events that are already over. Measured on 2026-11-22:

| Source | 7 days | Today | What happened |
|---|---:|---:|---|
| `argocd` | 2.9 GB | 0.2 % of today | 27–59 MB/h until 2026-11-19 01:10, then quiet |
| `n8n` | 729 MB | ~184 lines | 8 MB/h for twelve hours on 2026-11-18: an expired licence-SDK certificate and a pruner retried every second, ended by the 01:10 restart |

Read the 24-hour column first. A week-scale alarm that is not in today's top ten is a past event;
the next burst of that shape is what the per-service selectors above are for.

## What changed after this pass

The 2026-11-22 pass filed cards 627–636. Their fixes, in one line each:

- authentik, vikunja and the GPU operator log at warning/error, not `info`.
- The FreshRSS readiness probe carries the three `X-Forwarded-*` headers.
- The three hand-applied DaemonSets are gone.
- The nginx charts and pgAdmin stop logging probes.
- Fluent Bit's own metrics are scraped, so a dropped line is visible.
- CoreDNS's two glob warnings are gone: `cluster/infrastructure/coredns/custom.yaml` gives the
  `import` globs a file to match.

Re-measure a week after these land. The expected shape is under 300,000 lines a day, with
`docs/builder`, Traefik and maintainerr as the largest honest sources.
