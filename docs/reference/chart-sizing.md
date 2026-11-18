# Chart sizing: the reviewed set

> **In one line.** Every chart below has been measured against Prometheus and either adjusted or
> confirmed adequate; anything absent sets a request and a limit nobody has checked.
>
> **Use it when** you are setting a memory request or limit, or an audit asks which numbers are
> evidence and which are guesses · **Verified** 2026-11-18 · **Kind:** living page

Requirement 14 is a floor, not a target. This table is the record of which charts have actually been
measured against Prometheus, so a later audit can tell a settled number from an unexamined one
instead of re-deriving every chart and reporting the same findings again.

**Only the charts in the tables below have been sized against real data.** Anything absent is
"sets a request and a limit, never measured" — worth a measurement, not worth an incident. A chart
is re-measured when the app itself changes (new image, new library, new feature) or when this table
is more than six months old; not because someone noticed the number is round.

Source for the 2026-11-18 pass: the **6 days of history Prometheus retains** (5 days queried, to
leave margin), `container_memory_working_set_bytes` (working set — file cache included — not RSS),
p95 and max per container. The card that asked for this pass said 14 days; the retention is the
limit, and the window is written into every chart's `values.yaml` so the next reader knows what the
number covers. Method: **request = p95**, **limit = peak × 1.5**, CPU request 10–100m and no CPU
limit.

## Adjusted on 2026-11-18

| Chart | p95 / peak | Request was → now | Limit was → now | Why |
|---|---|---|---|---|
| `ashuvidz-website` nginx | 24Mi / 24Mi | 128Mi → **32Mi** | 192Mi → **128Mi** | The site serves files from disk: memory does not track page size. The limit covers the `git clone` init container too, which runs for seconds and has no sample to size from |
| `ashuvidz-website` git-syncer | 21Mi / 25Mi | 128Mi → **32Mi** | 192Mi → **64Mi** | One `git pull` a minute; the limit is 2.5× the peak |
| `media-stack/autobrr` | 417Mi / 681Mi | 192Mi → **448Mi** | 768Mi → **1Gi** | Grew well past the reference chart's first-day sample |
| `media-stack/bazarr` | 398Mi / 691Mi | 384Mi → **416Mi** | 1.5Gi → **1.25Gi** | Request was below p95; limit reserved 2.2× the peak |
| `media-stack/bazarr-anime` | 467Mi / 1300Mi | 384Mi → **480Mi** | 1.5Gi → **2Gi** | The anime library's subtitle scan is the peak |
| `media-stack/calibre-web-automated` | 465Mi / 789Mi | 512Mi → **480Mi** | 2Gi → **1.25Gi** | The 2Gi limit reserved 2.5× the peak |
| `media-stack/cross-seed` | 130Mi / 323Mi | 128Mi → **160Mi** | 512Mi (unchanged) | Request was a hair below p95 |
| `media-stack/flaresolverr` | 382Mi / 912Mi | 256Mi → **416Mi** | 1Gi → **1.5Gi** | The Chromium peak, not the idle footprint |
| `media-stack/kavita` | 550Mi / 954Mi | 256Mi → **576Mi** | 1Gi → **1.5Gi** | Chapter page cache holds the memory |
| `media-stack/lidarr` | 1104Mi / 1836Mi | 512Mi → **1.125Gi** | 2Gi → **2.75Gi** | The 2026-11-13 sample understated the scan by half — again |
| `media-stack/maintainerr` | 325Mi / 702Mi | 256Mi → **352Mi** | 1Gi → **1.25Gi** | Request was below p95 |
| `media-stack/prowlarr` | 339Mi / 678Mi | 256Mi → **352Mi** | 1Gi (unchanged) | Request was below p95 |
| `media-stack/radarr` | 276Mi / 477Mi | 256Mi → **288Mi** | 1Gi → **768Mi** | Limit reserved 2.1× the peak |
| `media-stack/radarr-anime` | 299Mi / 634Mi | 256Mi → **320Mi** | 1Gi (unchanged) | Request was below p95 |
| `media-stack/seerr` | 286Mi / 733Mi | 320Mi (unchanged) | 512Mi → **1.25Gi** | Peak is 1.4× the old limit, not 1.5× |
| `media-stack/sonarr` | 308Mi / 1075Mi | 384Mi (unchanged) | 1.5Gi → **1.75Gi** | Peak filled the old limit to 70 % |
| `media-stack/sonarr-anime` | 1109Mi / 1911Mi | 512Mi → **1.125Gi** | 2Gi → **3Gi** | This library's scan is the heaviest in the stack |
| `media-stack/tautulli` | 135Mi / 334Mi | 128Mi → **160Mi** | 512Mi (unchanged) | Request was a hair below p95 |
| `media-stack/unpackerr` | 34Mi / 472Mi | 64Mi (unchanged) | 1Gi → **768Mi** | 768Mi is still 1.6× the extraction peak |
| `exportarr` sidecars (8) | 26Mi / 38Mi | 56Mi → **32Mi** | 112Mi → **64Mi** | Reserved twice what the sidecar holds |
| `media-stack/plex` exporter sidecar | 19Mi / 37Mi | 64Mi → **32Mi** | 128Mi → **64Mi** | Same |
| `nfs-*` provisioners (9) | 49Mi / 99Mi | none → **64Mi** | none → **192Mi** | `resources: {}` since they were vendored |
| `media-stack/qbittorrent` vpn sidecar | 15Mi / 22Mi | 64Mi → **16Mi** | 128Mi → **64Mi** | Kernel tunnel buffers are not charged to the container |

Two init containers in `qbittorrent` (`wait-for-downloads`, `init-qbittorrent`) run for seconds,
shorter than the scrape interval, so there is no sample to size them from. They now carry a
protection ceiling only, with the reason next to the values.

## Kept, with the reason

| Chart | Request / limit | Why it did not move |
|---|---|---|
| `media-stack/plex` | 2Gi / 4Gi | A 30-day sample on the same workload is better evidence than this 5-day window: p95 2.19GiB, peak 2.37GiB. The recent window reads lower because the current pod's file cache is still filling |
| `media-stack/qbittorrent` | 6Gi / 12Gi | 9.5Gi p95 is download page cache; RSS was 0.24Gi on 2026-11-13. The request stays at RSS, the limit at 12Gi |
| `media-stack/qbittorrent-exporter` | 32Mi / 128Mi | 86Mi peak sits inside the 128Mi limit; re-measure after the next image bump |
| `media-stack/recyclarr` | 128Mi / 1Gi | A CronJob run is shorter than the scrape interval, so it has no steady state to measure |

## Enforced by the method, not by these numbers

No CPU limit anywhere, and CPU requests stay at 10–100m. CPU is compressible: a limit throttles the
burst the app needs and protects nothing, while a truthful small request is all the scheduler needs
to place the pod.

## History

The 2026-11-13 pass (commit `5a5432a0`) measured 18 charts against 39 h of history, raised eight
limits that sat within 6–20 % of their peak, released about 1.25 GiB of reservations and gave
`guacamole`, `syncthing` and `pgadmin` their first `resources` block. Its table is superseded by the
one above; `git show 5a5432a0` has it, with the reasoning, if a number needs explaining.
