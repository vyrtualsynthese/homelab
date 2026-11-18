# Longhorn leftovers: what is left and how to clear it

Longhorn was the cluster's replicated storage layer. It was abandoned after the 2026 disk failure and
its replica data is gone; no volume below holds anything worth rescuing, by operator decision
(`docs/superpowers/specs/2026-11-10-homelab-stabilisation-design.md` §9 W0).

The storage driver and its custom resources are already uninstalled, so nothing removes the last
objects on its own: their finalizers name controllers that no longer exist.

> **Where this stands.** The operator ran phases 1, 3 and 4 on 2026-11-18. Twenty volumes and three
> claims were cleared, and `longhorn-system` is gone. **What remains is the three claims of phase 2**
> — `n8n-2`, `syncthing-conf`, `thelounge-volume` — which stay until those three applications are
> rebuilt off Longhorn. The tables below are the inventory taken before that run, kept as the record
> of what was removed.

> **Agents cannot run these steps.** The agent's cluster access is read-only and shell `kubectl` is
> denied, so every cluster-side command here is for the operator. Repo-side items are already merged.

## What is left

| Object | Count | State |
|---|---|---|
| PersistentVolume | 26 | 20 `Released`, 6 `Bound`; all `Terminating` since 2026-08-28 |
| PersistentVolumeClaim | 6 | `Terminating` since 2026-08-28, still `Bound` |
| Namespace `longhorn-system` | 1 | `Active`, empty except the `kube-root-ca.crt` ConfigMap |
| StorageClass `longhorn*` | 0 | already gone with the driver |
| Longhorn custom resources | 0 | the CRDs are uninstalled |

The namespace is empty, not "stuck on a finalizer": it holds no workload, service or secret. Deleting
it is safe once the volumes are gone.

### The six claims, and who is still on them

| Namespace / claim | Size | Pod still mounting it | Safe to clear now |
|---|---|---|---|
| `media-stack/filebrowser-config` | 1Gi | none alive (`filebrowser-c9949b4cf-kpdxj` is `Succeeded`) | yes |
| `media-stack/filebrowser-db-2` | 1Gi | same dead pod | yes |
| `monitoring/prometheus-data` | 150Gi | none alive (`prometheus-server-7cbccb44b5-spn89` is `Failed`) | yes |
| `n8n/n8n-2` | 1Gi | `n8n-5586dbb4d5-7pzbw`, **Running** since 2026-08-13 | **no** |
| `syncthing/syncthing-conf` | 1Gi | `syncthing-75bc646f6b-b8cmq`, **Running** since 2026-08-13 | **no** |
| `thelounge/thelounge-volume` | 20Gi | `thelounge-7ff76784d7-z8fmb`, **Running** since 2026-08-13 | **no** |

The last three are still serving reads and writes from the mount the kubelet made before the failure.
Deleting the claim object does not stop the running process, but the application loses its volume on
its next restart and will not start again. They are rebuilt on `local-path` first (Wave A), then
their claims can go in the same way as the first three.

**This contradicts AGENTS.md §7.4**, which said every application had already been rebuilt. It has
not: n8n, syncthing and thelounge were never migrated, and nothing else mounts a Longhorn claim.

### The twenty-six volumes

Every one carries `external-attacher/driver-longhorn-io`. The six `Bound` ones also carry
`kubernetes.io/pv-protection` until their claim is gone, and `pvc-e705d2ea-…` adds
`external-provisioner.volume.kubernetes.io/finalizer`.

| PV | Claim | Size | Phase |
|---|---|---|---|
| `bazarr-anime-config` | `media-stack/bazarr-anime-config` | 2Gi | Released |
| `bazarr-config` | `media-stack/bazarr-config` | 2Gi | Released |
| `cross-seed` | `media-stack/cross-seed` | 5Gi | Released |
| `cross-seed-config` | `media-stack/cross-seed-config` | 1Gi | Released |
| `discourse2` | `discourse/discourse2` | 20Gi | Released |
| `filebrowser-config` | `media-stack/filebrowser-config` | 1Gi | Bound |
| `filebrowser-db-2` | `media-stack/filebrowser-db-2` | 1Gi | Bound |
| `freshrss-volume` | `news/freshrss-volume` | 1Gi | Released |
| `grafana-2` | `monitoring/grafana-2` | 1Gi | Released |
| `kavita-config2` | `media-stack/kavita-config2` | 5Gi | Released |
| `koillection-conf` | `koillection/koillection-conf` | 1Gi | Released |
| `koillection-uploads` | `koillection/koillection-uploads` | 20Gi | Released |
| `n8n-2` | `n8n/n8n-2` | 1Gi | Bound |
| `overseerr-config` | `media-stack/overseerr-config` | 20Gi | Released |
| `prometheus-data` | `monitoring/prometheus-data` | 150Gi | Bound |
| `prowlarr-config` | `media-stack/prowlarr-config` | 50Gi | Released |
| `pvc-e705d2ea-c817-44b5-888a-985bed767c8c` | `discourse/redis-data-discourse-redis-master-0` | 8Gi | Released |
| `qbittorrent-vpn-config` | `media-stack/qbittorrent-vpn-config` | 1Gi | Released |
| `radarr-anime-config` | `media-stack/radarr-anime-config` | 20Gi | Released |
| `radarr-config2` | `media-stack/radarr-config2` | 50Gi | Released |
| `readarr-config` | `media-stack/readarr-config` | 20Gi | Released |
| `sonarr-anime-config` | `media-stack/sonarr-anime-config` | 20Gi | Released |
| `sonarr-config` | `media-stack/sonarr-config` | 20Gi | Released |
| `syncthing-conf` | `syncthing/syncthing-conf` | 1Gi | Bound |
| `tautulli-config2` | `media-stack/tautulli-config2` | 50Gi | Released |
| `thelounge-volume` | `thelounge/thelounge-volume` | 20Gi | Bound |

A `Released` volume has no claim left: nothing references it, and clearing it changes nothing at
runtime.

## Where the eleven affected applications stand

| Application | Planned disposition | State on 2026-11-18 |
|---|---|---|
| filebrowser | rebuilt in W7.1 | rebuilt, `local-path`, own namespace; old claims orphaned |
| calibre | rebuilt in Wave A | rebuilt as Calibre-Web Automated on `local-path` |
| firefly | rebuilt in Wave A | retired 2026-11-18 |
| linkstack | rebuilt in Wave A | retired 2026-11-18 |
| n8n | rebuilt in Wave A | **not rebuilt**, still on `n8n-2` |
| syncthing | rebuilt in Wave A | **not rebuilt**, still on `syncthing-conf` |
| thelounge | rebuilt in Wave A | **not rebuilt**, still on `thelounge-volume` |
| pgadmin | rebuilt in Wave A | rebuilt on `local-path` |
| prometheus | replaced wholesale in W6 | replaced on `local-path`; old claim orphaned |
| postiz redis | rebuilt with postiz | retired 2026-11-18 |

## Runbook

Run from a shell with the admin kubeconfig. Each phase ends with a check; stop if a check does not
match.

### Phase 0 — re-check the live set

The numbers above are a snapshot. Confirm them before patching, and stop if a claim below is not on
the list.

```bash
kubectl get pvc -A -o json | jq -r '.items[] | select(.metadata.deletionTimestamp != null) | "\(.metadata.namespace)/\(.metadata.name)"'
```

Expected: the six claims of the table above, in the same namespaces. A seventh means the cluster
changed since this page was written; re-derive the list before continuing.

### Phase 1 — clear the three orphaned claims

`filebrowser-config`, `filebrowser-db-2`, `prometheus-data` have no live pod on them.

```bash
for pv in filebrowser-config filebrowser-db-2 prometheus-data; do
  kubectl patch pv "$pv" --type=merge -p '{"metadata":{"finalizers":null}}'
done
for ref in media-stack/filebrowser-config media-stack/filebrowser-db-2 monitoring/prometheus-data; do
  kubectl -n "${ref%/*}" patch pvc "${ref#*/}" --type=merge -p '{"metadata":{"finalizers":null}}'
done
```

If a PV still shows `Terminating` after its claim is gone, it is holding `kubernetes.io/pv-protection`:
run the same `kubectl patch pv` line once more.

Expected: `NotFound` for each volume, and no Longhorn claim left. Match on the storage class, not on
the name — the rebuilt `filebrowser` application has a `filebrowser-config` claim of its own, on
`local-path`, and a name-only grep finds it instead.

```bash
kubectl get pv filebrowser-config filebrowser-db-2 prometheus-data 2>&1
kubectl get pvc -A | grep longhorn
```

The second command prints only the three claims of phase 2.

### Phase 2 — the three claims still in use: deferred

`n8n-2`, `syncthing-conf` and `thelounge-volume` are mounted by running packs. Do not patch them
here. Rebuild each application on `local-path` (Wave A), confirm the new pod runs on the new claim,
then run the Phase 1 loop with those three names. Their PVs stay `Bound` until then, which is correct.

### Phase 3 — clear the twenty released volumes

```bash
kubectl get pv -o json \
  | jq -r '.items[] | select(.spec.csi.driver == "driver.longhorn.io") | select(.status.phase == "Released") | .metadata.name' \
  | while read -r pv; do echo "-> $pv"; kubectl patch pv "$pv" --type=merge -p '{"metadata":{"finalizers":null}}'; done
```

Expected: twenty `->` lines. Any PV printed that is not in the table above means the cluster moved;
stop and re-derive.

### Phase 4 — delete the namespace

```bash
kubectl delete namespace longhorn-system --wait=false
kubectl get ns longhorn-system 2>&1
```

Expected: `namespace "longhorn-system" deleted` at once, then `NotFound` within a few seconds. With
`--wait=false` the delete returns while the namespace is still `Terminating`, so `kubectl get ns
longhorn-system` can answer `Terminating` on the first look — that is the normal race, not a stuck
namespace. The namespace holds only `kube-root-ca.crt`, so it goes without a patch; a `Terminating`
that still stands after a minute means something points into it — report the condition instead of
clearing it blind.

### Phase 5 — verify

```bash
kubectl get pv | grep -ci longhorn
kubectl get pvc -A | grep -ci longhorn
kubectl get ns | grep -ci longhorn
```

Expected: `3`, `3` and `0`. The two threes are the deferred claims of Phase 2 and their volumes —
they stay until those applications are rebuilt. Anything counting six means Phase 3 did not run. A
`1` on the last line is the terminating namespace still in the list; wait and re-run.

## Found on the way, outside this cleanup

Three other leftovers are visible in the same listings and must **not** be cleared with the same
recipe:

- **Four externally-provisioned NFS volumes carry a deletion timestamp while still in use**:
  `pv-traefik-nfs` (mounted by `kube-system/traefik-nfs-c8ff497b8-jdgml` and holding the ACME
  certificate store), `pv-plex-media`, `pv-stash-nfs-nfs-stash` and
  `pv-nfs-qbittorrent-download-nfs-subdir-external-provisioner`. Their claims are `Bound` and pods
  are reading them now. They have no CSI driver finalizer — only `kubernetes.io/pv-protection` — so
  a patch that looks identical would delete a live mount. They need their own investigation.
- **Four `local-path` volumes are `Released`** and hold no claim: `pvc-51a71183…`
  (`calibre-web-automated-config`), `pvc-61023024…` (`pgadmin-config`), `pvc-76b5d66d…`
  (`cross-seed2`), `pvc-801276ae…` (`plex-transcode`). Deleting the objects is safe; the directories
  on disk stay where they are, because `local-path` volumes use the `Retain` reclaim policy.
- **Two pods are stuck `Terminating` on `nuc0`**: `media-stack/filebrowser-c9949b4cf-kpdxj` and
  `monitoring/prometheus-server-7cbccb44b5-spn89`. Both are already dead (`Succeeded` / `Failed`)
  and are waiting on a container runtime teardown. They disappear with a node restart or a
  `kubelet` restart; nothing else depends on them.
