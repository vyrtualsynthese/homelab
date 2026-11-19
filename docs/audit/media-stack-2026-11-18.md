# Media stack audit — 2026-11-18

> **In one line.** The twenty-one `media-stack` charts were read against the chart contract, the
> exposure checker and the live cluster on one day: the stack is coherent, the drift is a handful
> of stale documents and one chart that never got the contract pass.
>
> **Use it when** a media-stack change needs the evidence behind it, or card 225 needs its findings ·
> **Verified** 2026-11-18 · **Kind:** snapshot (2026-11-18)

This is the report card 225 asked for. It describes the repository at `2b8b9b29` on `develop` and
the cluster as read on the same day. The findings marked **fixed here** were corrected on
`feature/media-stack-audit`; the rest became Backlog cards, numbered below.

## 1. How this read was taken

- Render and lint every chart: `helm template test <chart> -f <chart>/values.yaml -n media-stack`
  and `helm lint`, for all 21 — no errors.
- `hack/check-exposure.sh`, full repository: 69 routes, `result: OK`.
- `grep` for tabs, `networking.k8s.io`, floating `latest` tags, and copy-paste leftovers — clean.
- Application references, with the two checks in `AGENTS.md` §6.
- Live cluster, read-only through the `opencode-mcp` ServiceAccount: pods, PVCs, Deployments,
  IngressRoutes, container statuses and one container's log.
- Prometheus, `http://prometheus.kub.local`, for exporter target health.
- The per-chart READMEs and `docs/charts/README.md`, checked line by line against the values files.

No write was made to the cluster.

## 2. The contract, chart by chart

`Req.` is contract requirement 1 (tag and `appVersion` agree), 3 (`Recreate` with a config claim),
14 (memory request and limit) and 15 (hardening). `Gate` is the routing decision recorded in the
chart.

| Chart | Req. | Probes | CPU / memory request / memory limit | Route and gate |
|---|---|---|---|---|
| `autobrr` | ok | startup, liveness and readiness, separate endpoints | 10m / 192Mi / 768Mi | TLS, `lan-only`, native OIDC |
| `bazarr` | ok | `/` for all three, recorded exception | 10m / 384Mi / 1.5Gi | TLS, forward-auth, `/api` on `lan-only` |
| `bazarr-anime` | ok | as `bazarr` | 10m / 384Mi / 1.5Gi | TLS, forward-auth, `/api` on `lan-only` |
| `calibre-web-automated` | ok | startup and liveness on `/`, readiness off | 25m / 512Mi / 2Gi | TLS, `lan-only`, native OIDC |
| `cross-seed` | ok | liveness only | 50m / 128Mi / 512Mi | none |
| `flaresolverr` | ok | liveness and readiness on `/health` | 10m / 256Mi / 1Gi | none |
| `kavita` | ok | startup and liveness on `/api/health`, readiness off | 15m / 256Mi / 1Gi | public TLS, `lan-only`, native OIDC |
| `lidarr` | ok | `/ping` for all three | 10m / 512Mi / 2Gi | TLS, forward-auth, `/api` on `lan-only` |
| `maintainerr` | ok | liveness and readiness, separate endpoints | 25m / 256Mi / 1Gi | TLS, forward-auth, `/api` on `lan-only` |
| `plex` | ok | `/identity` for all three | 100m / 2Gi / 4Gi | public, no gate — Plex login |
| `prowlarr` | ok | `/ping` for all three, with `Host` header | 10m / 256Mi / 1Gi | TLS, forward-auth, `/api` on `lan-only` |
| `qbittorrent` | **no** — `appVersion` 1.0.0 vs 5.3.0 | none | 100m / 6Gi / 12Gi | TLS, forward-auth, no `/api` exemption, documented |
| `qbittorrent-exporter` | ok | none (exporter) | 10m / 32Mi / 128Mi | none |
| `radarr` | ok | `/ping` for all three, with `Host` header | 10m / 256Mi / 1Gi | TLS, native OIDC, `/api` on `lan-only` |
| `radarr-anime` | ok | as `radarr` | 10m / 256Mi / 1Gi | TLS, native OIDC, `/api` on `lan-only` |
| `recyclarr` | ok | n/a (CronJob) | 50m / 128Mi / 1Gi | none |
| `seerr` | ok | same endpoint for all three, recorded exception | 10m / 320Mi / 512Mi | public, no gate — Seerr login |
| `sonarr` | ok | `/ping` for all three | 10m / 384Mi / 1.5Gi | TLS, forward-auth, `/api` on `lan-only` |
| `sonarr-anime` | ok | `/ping` for all three | 10m / 512Mi / 2Gi | TLS, forward-auth, `/api` on `lan-only` |
| `tautulli` | ok | startup and liveness on `/status`, readiness off | 10m / 128Mi / 512Mi | TLS, `lan-only`, own login |
| `unpackerr` | ok | liveness only | 10m / 64Mi / 1Gi | none |

All 21 pin a node (`streaming`, except `recyclarr` on `nuc0`), pin an image tag, set a memory
request and a limit, and keep CPU requests in the 10m–100m band with no CPU limit. The LinuxServer
images set `PUID`/`PGID` 1100 and `TZ=Europe/Paris`.

**Gate coverage is complete.** Every non-public route carries `kube-system-lan-only`, and each
chart records its own decision: forward-auth for `bazarr`, `bazarr-anime`, `lidarr`, `maintainerr`,
`prowlarr`, `sonarr`, `sonarr-anime`; native OIDC for `autobrr`, `calibre-web-automated`, `kavita`,
`radarr`, `radarr-anime`; the app's own login for `tautulli`; Plex or Seerr login on the two public
hosts. The `/api` exemption is the same two-rule shape on the seven forward-auth and native-OIDC
charts that need it, and each says in its own comment why the app checks the key itself.

**The tag/`appVersion` pair is the only contract break** (requirement 1). Every other row above was
already true, which is worth recording: the rebuild work landed.

## 3. What the live cluster said

- 26 pods `Running` and Ready in `media-stack`, plus three `Completed` `recyclarr` Jobs.
- 24 PVCs, all `Bound`: 16 `local-path` config claims and 8 NFS claims.
- Every media-stack Prometheus target in the `kubernetes-pods` job reads `up`: `autobrr`, `sonarr`,
  `sonarr-anime`, `radarr`, `radarr-anime`, `lidarr`, `prowlarr`, `qbittorrent-exporter`,
  `flaresolverr`, `plex`. `bazarr` and `bazarr-anime` are absent by design — their `exportarr`
  block is off, with the reason in both READMEs.
- `tautulli`, `kavita` and `cross-seed` carry no exporter because their images ship none.

## 4. Defects fixed here

| # | Where | Finding |
|---|---|---|
| 1 | `cluster/helm/media-stack/qbittorrent/Chart.yaml:3,24` | Boilerplate description (`A Helm chart for Kubernetes`) and `appVersion: "1.0.0"` against `image.tag: 5.3.0` — the one requirement-1 break. Description written, `appVersion` set, chart version to `0.2.3`. |
| 2 | `cluster/helm/media-stack/lidarr/README.md:113-118` | "Resources are unmeasured … `256Mi` request, `1Gi` limit" — Lidarr was measured on 2026-11-13 (930Mi peak) and runs `512Mi`/`2Gi`. Section rewritten. |
| 3 | `cluster/helm/media-stack/tautulli/README.md:42` | Values table said `256Mi`/`1Gi`; the chart has `128Mi`/`512Mi` since the same sizing pass. |
| 4 | `cluster/helm/media-stack/unpackerr/README.md:47,186-188` | Values table and Resources section said a `512Mi` limit; the chart has `1Gi`, raised because the extraction burst reached 460Mi. |
| 5 | `docs/charts/README.md:211` | The hardening table ended with "everything else \| no \| no \| no", which was false for `plex`, `seerr` and `qbittorrent-exporter`, and said nothing about `qbittorrent`'s privileged VPN sidecar. Four rows added; the catch-all row removed. |
| 6 | `docs/status.md:158` | `qbittorrent` — "`alpine:latest` in the completion hook": pinned to `3.22` on 2026-11-18. |
| 7 | `docs/secrets.md:24` | The Secret inventory had no row for `autobrr-oidc` (`client-secret` → `AUTOBRR__OIDC_CLIENT_SECRET`), and the arr row did not mention unpackerr as a second consumer. |
| 8 | `docs/reference/chart-notes.md:94` | "Both exporter sidecars still answer 401 until the operator patches the stale keys" — both `radarr` targets read `up` on 2026-11-18. |

## 5. Filed as follow-up cards

| Card | Finding |
|---|---|
| 353 | `qbittorrent` is pre-contract: unprefixed helpers, hardcoded IngressRoute API group and Service name, no README, privileged WireGuard sidecar never recorded as an exception. |
| 354 | Two Applications point at a directory that does not exist (`nfs/plex-media.yaml`, `nfs/nfs-audio.yaml`) and one at a values-only directory (`nfs/nfs-qbittorrent-config.yaml`). Orphan charts `nfs/plexmedia` and `nfs/minio-nfs`. Each needs a decision, not a one-line edit. |
| 355 | NFS provisioners carry 93–224 restarts (last one about a day earlier), exit 255. Stable now, cause never recorded. |
| 356 | `filebrowser-c9949b4cf-kpdxj` is still `Terminating` in `media-stack` with no Deployment behind it. |

Also noted, not carded: 8 of the 21 charts leave the app's log level at its default and only
`bazarr` records why (its logger has no warning level). A comment with the list went on card 227,
which owns logs.

## 6. Findings left alone, on purpose

- **Resource numbers.** Card 226 measures and rewrites them; this pass only corrected the READMEs
  that quoted numbers the 2026-11-13 sizing had already changed.
- **`plex-media-public` IngressRoutes in the live cluster.** Card 316 already owns the deletion.
- **`nfs-qbittorrent-config` reporting Synced/Healthy while deploying nothing.** Folded into card
  354, because the same decision — deploy or delete — covers all three broken references.
- **The card's premise that `sonarr-anime` and `radarr-anime` have no gate.** Both changed on
  2026-11-18: `sonarr-anime` carries forward-auth and `radarr-anime` uses Radarr 7's native OIDC.
  The charts are right; the card text was written before those changes.

## 7. Verdicts

1. The media stack is **coherent**: contract, exposure and live state agree, and the drift is
   documentary, not structural.
2. **`qbittorrent` is the outlier** and the only chart that needs a rebuild, not a patch.
3. The NFS layer is the fragile part: broken Applications, orphan charts and a restart history
   nobody has explained. That is worth a session of its own (cards 354 and 355).
4. Nothing here blocks the next layer — identity for other namespaces, storage, backups — on
   correctness. The one thing that would bite a later reader is a document that states the old
   truth, which is what section 4 removes.
