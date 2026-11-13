# `nuc0` overheats and throttles; accepted, no alert

Date: 2026-11-13
Decided by: operator
Status: active

## The decision

`nuc0` throttles under load and will keep doing so. The cooling has already been serviced — repasted
and cleaned — and the chassis is too small for the CPU it holds, so there is no remaining
configuration or maintenance action. The operator accepts occasional overheating, and the real fix is
to build a properly sized replacement node when money allows. Until then the condition is **tracked
in this record only**: it gets no alert and no silence, because an alert would fire regularly and
carry no action.

## Why

Measured from Prometheus on 2026-11-13 over the 39 h of history it holds:

| Signal | Value |
|---|---|
| `node_cpu_package_throttles_total` | **+2 005 package events in 39 h**, 65 013 682 since boot |
| Per-core counters | 12.7 M and 18.0 M on the two busiest cores since boot |
| `platform_coretemp_0` peak | **95 °C** (`temp1` 94 °C, `temp10` 95 °C) |
| `node_thermal_zone_temp` peak | 92 °C (zone 1), 89 °C (zone 0) |
| Load | 0.68 average, 2.37 peak — the throttling is not caused by running out of CPU |

Unlike `streaming`, this host does report hardware throttle counters and a real CPU temperature, so
there is nothing to measure and nothing to discover: the machine is at its thermal limit by design of
the chassis.

## Why no alert

The cluster's alerting is one Grafana rule group delivering to one Discord channel. A rule on this
condition would fire for hours at a time on a node that is doing nothing wrong, and the only response
it could ask for is "buy a new computer" — which is already the plan. Alert fatigue is the real cost
of a rule nobody can act on.

The state is still visible without alerting: the Platform dashboard shows node temperature and
frequency, and this record is the dated evidence. What must not happen is the opposite mistake —
someone re-reading the dashboard in three months and filing the 95 °C as a new incident.

## What it costs if it is wrong

If the throttling is worse than "occasional", the cost is unexplained slowness and long transcode or
indexing jobs on `nuc0`, with no alert to say so. The mitigation is the node replacement, not a
threshold. If the machine fails outright, its workloads are stateless apart from `local-path` config
claims, which is the same exposure every other node has.

## Related

`streaming` has a similar thermal condition with a different tracking method, because its alert can
be silenced without removing it from the dashboard:
[`2026-11-13-streaming-overheating-deferred.md`](2026-11-13-streaming-overheating-deferred.md).
