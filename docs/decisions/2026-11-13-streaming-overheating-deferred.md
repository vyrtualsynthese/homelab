# `streaming` runs at its thermal ceiling; the hardware fix is deferred

Date: 2026-11-13
Decided by: operator
Status: active

## The decision

`streaming` (the control-plane node that also holds the NVIDIA GPU and runs most media and
transcoding work) has a cooling problem that no configuration change fixes. Replacing the cooler or
the platform costs money the operator does not have for the next few months, so the fault is
**accepted and tracked** rather than repaired now. The node stays in service with its normal
workload.

Tracking ends when one of these is true:

- a load-bearing soak (CPU at 50 % for an hour) keeps the CPU package under 80 °C;
- or the node is retired or rebuilt with different cooling.

## Why

Measured from Prometheus on 2026-11-13, over the 39 h of history it holds. The reading is
`node_hwmon_temp_celsius` for the host's `k10temp` device — `Tctl`, the CPU package temperature.
The hwmon **chip label is a device path** (`pci0000:00_0000:00:18_3`), which is what made an earlier
reading mistake it for a PCIe sensor: `node_hwmon_chip_names` reports `k10temp` for that chip.

| Signal | Idle | Under 10–28 % CPU |
|---|---|---|
| CPU package (`Tctl`) | 52–55 °C | 85–91 °C, pinned at **91.2–91.4 °C for hours** |
| Peak over the window | — | **93.75 °C** |
| GPU (`DCGM_FI_DEV_GPU_TEMP`) | 41 °C peak | — |
| `acpitz` zone (different chip, excluded from the rule) | 16.8 °C, unchanging | 16.8 °C — meaningless |

What makes this a machine problem rather than a threshold problem:

1. **The relationship between load and temperature is wrong.** A quarter of sixteen cores reaching
   the low nineties is what a starved or degraded cooler looks like (dried paste under a cooler that
   was never specced for this CPU, or a heatsink packed with dust). A healthy cooler holds that load
   30–40 °C lower.
2. **The plateau.** For four hours the value sat inside a 0.2 °C band — 91.2 to 91.4 °C — while load
   moved between 10 % and 32 %. A limit is being enforced, not a coincidence of sampling.
3. **Nothing else is in the data.** GPU at 41 °C, node memory never below 75 % available, NFS client
   RPC retransmissions zero, no ECC or power events to point at.

## How it is tracked

`NodeTemperatureHigh` (Grafana, group `homelab-core`) selects the CPU package by hwmon driver name
and covers this host again as of 2026-11-13, so the alert **fires correctly** when the node is hot.
That is the tracking mechanism, and it needs no new rule.

What keeps it from becoming noise is a **time-boxed silence**, not a weakened rule: the rule state
stays `firing` and stays visible on the Health dashboard, while Discord is spared a message every
hour for a condition that already has an owner and a plan. The intended silence is recorded here so
it can be recreated exactly:

```
matchers: host="streaming", severity="warning", __alert_rule_uid__=<NodeTemperatureHigh>
comment:  streaming runs at its thermal ceiling; hardware deferred
          (docs/decisions/2026-11-13-streaming-overheating-deferred.md)
endsAt:   2027-03-01
```

**Not applied by this change** — the operator owns the alert configuration, and the earlier
three-minute silence created from the UI has expired. Until a long silence is in place, the rule
notifies normally, which is the correct failure mode: noisier, not blind.

## What it costs if it is wrong

If the reading were meaningless after all, the cost is a warning that fires on `streaming` whenever
the node does real work, plus a rule that has to be narrowed again. If the reading is right and
nothing is done, the cost is thermal shutdowns under sustained load, accelerated wear on the NVMe and
the VRM, and Plex or transcode jobs that stall with no obvious cause — plus a CPU that spends its
burst capacity throttling instead of transcoding.

## Next steps, in order of cost

1. **Free:** physical check — fan spin, heatsink dust, and whether the paste is original. The
   operator reports nothing done yet on the cooling.
2. **Free:** keep the rule firing and put the time-boxed silence above in place.
3. **Costs money:** cooler or platform replacement. Deferred by this decision.

## A second machine, tracked separately

`nuc0` also throttles, by a large margin: `node_cpu_package_throttles_total` grew by **2 005 package
events in 39 h** with per-core counters at 12.7 M and 18.0 M since boot, and its CPU package peaked
at **95 °C**. That machine has already been repasted and cleaned, is too small for its CPU, and is
accepted without an alert:
[`2026-11-13-nuc0-throttling-accepted.md`](2026-11-13-nuc0-throttling-accepted.md).
