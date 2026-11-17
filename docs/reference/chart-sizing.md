# Chart sizing: the reviewed set

> **In one line.** The charts in the two tables below have been measured against Prometheus and
> either adjusted or confirmed adequate; everything absent sets a request and a limit nobody has
> checked.
>
> **Use it when** you are setting a memory request or limit, or an audit asks which numbers are
> evidence and which are guesses · **Verified** 2026-11-17 · **Kind:** living page

Requirement 14 is a floor, not a target. This table is the record of which charts have actually been
measured against Prometheus, so a later audit can tell a settled number from an unexamined one
instead of re-deriving every chart and reporting the same findings again.

**Only the charts in the tables below have been sized against real data.** Anything absent is
"sets a request and a limit, never measured" — worth a measurement, not worth an incident. A chart
is re-measured when the app itself changes (new image, new library, new feature) or when this table
is more than six months old; not because someone noticed the number is round.

Source: Prometheus on 2026-11-13, the 39 h of history it holds, `container_memory_working_set_bytes`
(working set — file cache included — not RSS), peak and average per container.

## Adjusted on 2026-11-13

| Chart | Measured peak | Request was → now | Limit was → now | Why |
|---|---|---|---|---|
| `media-stack/lidarr` | 930Mi | 256Mi → **512Mi** | 1Gi → **2Gi** | limit filled to 93 %; the app had grown since it was sized from sonarr's profile |
| `media-stack/sonarr-anime` | 800Mi | 384Mi → **512Mi** | 1Gi → **2Gi** | limit filled to 80 %; the pre-rebuild 30-day sample understated it by half |
| `media-stack/sonarr` | 570Mi | 256Mi → **384Mi** | 768Mi → **1.5Gi** | same sample, same drift |
| `media-stack/bazarr-anime` | 660Mi | 256Mi → **384Mi** | 768Mi → **1.5Gi** | the old pair was a guess made while the app was Pending |
| `media-stack/bazarr` | 480Mi | 256Mi → **384Mi** | 768Mi → **1.5Gi** | kept identical to `bazarr-anime`: one image, two libraries |
| `media-stack/unpackerr` | 460Mi burst | 64Mi (unchanged) | 512Mi → **1Gi** | the peak is extraction children, and 92 % of the limit risks killing a big archive |
| `n8n` | 420Mi | 256Mi (unchanged) | 512Mi → **768Mi** | 85 % of the limit on a workflow run |
| `monitoring/grafana` | 370Mi | 256Mi (unchanged) | 512Mi → **768Mi** | it evaluates and delivers every alert in the cluster; 74 % is not enough headroom |
| `filebrowser` | 20Mi | 512Mi → **64Mi** | 1Gi → **256Mi** | reserved 25× the app |
| `media-stack/maintainerr` | 230Mi | 512Mi → **256Mi** | 1Gi (unchanged) | reserved 2× the app |
| `media-stack/tautulli` | 120Mi | 256Mi → **128Mi** | 1Gi → **512Mi** | both numbers were aspirational |
| `dashy` | 270Mi | 500m CPU → **10m**, 512Mi → **256Mi** | 4Gi → **1Gi** | upstream defaults, never visited |
| `monitoring/mktxp` | 30Mi | 128Mi → **64Mi** | 256Mi (unchanged) | 4× the app |
| `firefly` | 80Mi | 258Mi → **128Mi** | 512Mi (unchanged) | 3× the app |
| `guacamole` | 370Mi flat | none → **384Mi** | none → **768Mi** | had `resources: {}`, so it was unaccounted for by the scheduler |
| `syncthing` | 110Mi | none → **128Mi** | none → **256Mi** | had `resources: {}` |
| `databases/pgadmin` | 220Mi | none → **256Mi** | none → **384Mi** | had `resources: {}` |

Net effect: about 1.25 GiB of reservation released, all eight tight limits given real headroom, and
three workloads moved out of the unaccounted set.

## Verified adequate, unchanged

Measured on the same run and left alone. Peak as a share of the limit is in brackets; the number is
recorded so the next audit does not repeat the exercise.

| Chart | Peak / limit |
|---|---|
| `media-stack/plex` | 2.55Gi / 4Gi (64 %) |
| `media-stack/qbittorrent` | 9.81Gi / 12Gi (82 %) — but 0.24Gi is RSS and the rest is page cache, so the limit is not the number to tune |
| `monitoring/prometheus` | 0.96Gi / 2Gi — GC follows the cgroup limit, see `docs/observability-conventions.md` |
| `monitoring/loki` | 0.24Gi / 1Gi |
| `media-stack/radarr`, `radarr-anime` | 0.35Gi, 0.22Gi / 1Gi |
| `media-stack/prowlarr` | 0.26Gi / 1Gi |
| `media-stack/autobrr` | 0.22Gi / 768Mi |
| `media-stack/seerr` | 0.20Gi / 512Mi |
| `media-stack/cross-seed` | 0.07Gi / 512Mi |
| `media-stack/flaresolverr` | 0.23Gi / 1Gi |
| `media-stack/tautulli` | see above — adjusted |
| `thelounge` | 0.07Gi / 128Mi (60 %) |
| `monitorss` | 0.14Gi web, 0.04Gi bot / 256Mi |
| `linkstack`, `qbittorrent-exporter` | under 0.02Gi |
| `monitoring/prometheus-smartctl`, `prometheus-node-exporter`, `fluent-bit` | under 0.03Gi |

`recyclarr` is a CronJob: it has resources but no steady state to measure, so it is out of scope.
