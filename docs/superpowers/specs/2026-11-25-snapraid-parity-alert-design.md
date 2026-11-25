# SnapRAID parity stale: design

Card #499 (`tasks/713`) · branch `feature/snapraid-parity-alert` · 2026-11-25

## Intent

`nas` keeps a redundant copy of its six data disks — the **parity** — and a nightly **sync** brings
that copy up to date (SnapRAID). If a data disk dies, only the changes covered by the last finished
sync can be rebuilt.

Nothing watched that. The parity on the array carried 2024-02-10 as its date — 1,018 days of writes
with no protection — while a runner existed that could not start, because `python2` had left the host.
The runner is replaced (`ansible/roles/snapraid`, card #497) but still reports to nobody: a sync that
never runs again, or one that starts and dies, leaves the array exactly as exposed as before and looks
the same from outside.

The alert has to make "the redundant copy is behind the data" a message, and it has to keep making it
when the thing that would update it stops.

## Goals

- One metric, `homelab_snapraid_last_sync_timestamp_seconds`: the Unix time of the last sync that
  finished without an error. It ages by itself, so a dead runner turns into a growing number.
- One rule, `SnapraidParityStale`, warning, group `homelab-core`, firing when that timestamp is more
  than **7 days** old, and **Alerting when there is no metric at all** — no marker means no sync has
  ever finished, which is today's state and not a healthy one.
- The message names the host and how far behind it is, and says what to check and what to run.
- Nothing that works today changes: the restore-drill textfile, the node-exporter DaemonSet and its
  `/restore-drills` mount stay exactly as they are.

## Non-goals

- **Shipping the host journal to Loki.** The card's alternative — a Loki rule on a missing `snapraid`
  line — is not cheaper than the metric: Fluent Bit tails `/var/log/containers/*.log` only
  (`cluster/helm/monitoring/fluent-bit/values.yaml`, `nodeAccess: false`, no host mount), so no
  journal line from any host reaches Loki. The comments in `ansible/roles/snapraid` that claim
  otherwise are corrected as text in this change; wiring the journal in is a stack-wide decision of
  its own (host mounts, log volume, retention) and gets a card.
- **The scrub.** Scrubbing checks that the parity is *readable* as well as current; it runs one slice
  a month by design, so its own age says nothing for weeks at a time. Different signal, different
  card if wanted.
- **Reading `snapraid status` from anything.** It refuses to run while a sync holds the content lock,
  which is exactly when a check would want to run. The metric is written by the runner, not measured
  on demand.

## Constraints found while designing

- `node-exporter`'s textfile collector reads **one** directory (`--collector.textfile.directory`), and
  on the live DaemonSet that directory is `/restore-drills`, the restore-drill ConfigMap. A host
  directory cannot be added "beside" it: one of the two has to move. The restore-drill rule depends
  on the ConfigMap being re-read when a drill is recorded, which a `subPath` mount would break, so
  the ConfigMap stays where it is.
- There is no in-cluster view of `nas`'s files. Anything that measures the array is a host write or a
  `hostPath` mount.
- The first full sync reads 13 TiB and resumes across nights, so a sync that has not finished after
  two nights is normal for now; the threshold has to be longer than that.

## Approaches

- **A — a second, textfile-only exporter on `nas` (chosen).** The runner writes a marker file on the
  host after a sync exits cleanly; a small `node-exporter` Deployment pinned to `nas`, with its
  default collectors disabled, serves that directory and nothing else. One pod (~25 MiB), its own
  chart and Application. No existing path changes, and the metric arrives the way every other number
  in this stack arrives: a scrape.
- **B — free the textfile directory.** Move the restore-drill dates from the ConfigMap to a
  Prometheus recording rule and point the DaemonSet's textfile collector at a host directory. No new
  pod, but it rewires a rule that works, and Prometheus holds no rule files today: a bad file there
  stops the scraper rather than one rule.
- **C — ship the host journal and alert on a missing line.** The biggest change and the one that
  fixes the false claim at the root, but it is a monitoring-stack decision (host mounts, volume,
  retention, privacy) rather than this card's.

Chosen: **A**, for changing nothing that works and carrying the age in the message. The cost is a
second exporter on one host, and it is a textfile collector with every default collector off, so it
cannot publish a second, wrong copy of `nas`'s node metrics.

## The metric

Written by `ansible/roles/snapraid/files/snapraid-sync.sh` immediately after `/usr/bin/snapraid sync`
returns 0, into `/var/lib/node-exporter/textfile/snapraid.prom`:

```
# HELP homelab_snapraid_last_sync_timestamp_seconds Unix time of the last snapraid sync that finished without an error.
# TYPE homelab_snapraid_last_sync_timestamp_seconds gauge
homelab_snapraid_last_sync_timestamp_seconds 1764100000
```

- **Timestamp, not an age.** An age written once a night is frozen by the dead runner it is meant to
  expose; a timestamp ages on its own and the rule subtracts.
- **Written atomically** (temp file in the same directory, then `mv`), because node-exporter rejects
  a whole file it cannot parse and reads it on every scrape. `rename(2)` inside one directory is
  atomic, so a scrape sees the old body or the new one, never half of either.
- **Written only on success.** A sync that fails exits non-zero under `set -e` before the write, so a
  failing loop leaves the number behind and the alert fires. A marker that cannot be written is
  logged as an error line and does not abort the run: the alert is already the loud part, and a
  monthly scrub should not be lost to it.
- **Mode `0644`, directory `0755`**, because the exporter runs as user `65534`.

## Parts

1. **`ansible/roles/snapraid`**
   - `tasks/main.yml`: create `/var/lib/node-exporter/textfile` (root, `0755`); correct the header
     comment that says the journal reaches Loki.
   - `files/snapraid-sync.sh`: write the marker through a `write_marker` function after the sync;
     correct the same false claim in its header.
2. **`cluster/helm/monitoring/snapraid-exporter/`** — new chart, pattern A, one Deployment:
   - image `quay.io/prometheus/node-exporter:v1.13.1` (the version the DaemonSet already runs),
     `appVersion: "1.13.1"`;
   - args `--collector.disable-defaults --collector.textfile
     --collector.textfile.directory=/textfile`;
   - `hostPath: /var/lib/node-exporter/textfile`, type `Directory` (not `DirectoryOrCreate`: a
     missing directory is the role not having run, and a pod that will not start says so);
   - a pod annotation `prometheus.io/scrape: "true"` and `prometheus.io/port: "9100"` — annotation
     discovery found it the same way it finds `mktxp` and `blackbox-exporter`, so no Service and no
     scrape config in the `prometheus` chart;
   - pinned to `nas`; `runAsNonRoot` 65534, `readOnlyRootFilesystem`, all capabilities dropped, no
     `hostNetwork`/`hostPID` (the DaemonSet holds port 9100 on the host, this one binds inside the
     pod);
   - requests `5m`/`16Mi`, limit `32Mi`.
3. **`cluster/argocd/projects/monitoring/snapraid-exporter.yaml`** — the Application, namespace
   `monitoring`.
4. **The rule**, created through Grafana's provisioning API
   (`POST /api/v1/provisioning/alert-rules`), never by `kubectl`:
   - query A, instant, Prometheus: `time() - homelab_snapraid_last_sync_timestamp_seconds`
   - threshold: `gt 604800` (7 days), the comparison in the Grafana threshold rather than in PromQL
     — the shape `RestoreDrillOverdue` uses, because the no-data state is Alerting and a false
     PromQL comparison is an empty vector, which would page on every healthy night;
   - `for: 10m`, `noDataState: Alerting`, `execErrState: OK`, severity `warning`, group
     `homelab-core` (5-minute interval: a nightly input gains nothing from 60 s);
   - summary: `snapraid parity is behind on {{ $labels.node }}`; description: the age, the two causes
     (no completed sync, or the marker unreadable), the command to run and the card this came from.
5. **Docs**: `docs/reference/alerts.md` (row, counts, the paragraph, the no-data exception list),
   `docs/observability-conventions.md` (what runs where, and the exporters paragraph),
   `docs/how-it-works/applications.md` (the catalogue row), `docs/reference/metrics.md` if it lists
   targets, and `ansible/README.MD` if it describes the role.

## Why 7 days

The runner is nightly; a single failed night is noise and not worth a page. Seven days is one full
week of missed or failed syncs — the point where the redundant copy stops being a copy of anything
recent — and it clears the multi-night first sync, which two nights would not. A warning rather than
critical: the disks are still readable, the protection is not, and the repo's critical tier is for
things that are down.

## Verification

- `hack/check-charts.sh` (lint and template the new chart), `hack/check-app-version.sh`,
  `hack/check-docs.sh`, `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q
  hack/tests`, and the ArgoCD path loop from `AGENTS.md` §6.
- `ansible-playbook playbooks/site.yml --limit storage --check` reports the directory task, then the
  real run creates it on `nas`.
- After the next completed sync, Prometheus serves the metric:
  `homelab_snapraid_last_sync_timestamp_seconds{node="nas"}` returns one sample whose `time() - value`
  matches the run.
- Plant a timestamp ten days back on the host, then: the rule goes `pending` → `firing` within its
  `for` and the message names `nas` and the age; write the true timestamp back and it returns to
  Normal. Proved on a muted copy through `/api/v1/eval` first, then on the saved rule.
- Delete the marker file: the rule reports Alerting through its no-data state, which is the same
  message a runner that never wrote it produces — the "stopping the runner does not pass silently"
  case.
- Read back `GET /api/v1/provisioning/folder/homelab/rule-groups/homelab-core` and confirm the rule's
  group, interval, `for` and no-data state, then make `docs/reference/alerts.md` agree with it.

## Open, decided by the builder

- The chart name and the Application name (`snapraid-exporter`) — one name for both, as every other
  monitoring chart does.
- Whether the metric keeps the `homelab_` prefix: it does, following
  `homelab_restore_drill_timestamp_seconds`, which is the same kind of hand-made metric.
