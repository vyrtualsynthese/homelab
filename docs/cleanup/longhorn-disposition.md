# Longhorn leftovers: what was left and how it was cleared

> **In one line.** Every Longhorn object is gone — the driver, its StorageClasses, its custom
> resources, the `longhorn-system` namespace, all twenty-six volumes and all six claims.
>
> **Use it when** a Longhorn volume, claim or CRD turns up in an old note, or you want the order the
> leftovers were cleared in · **Verified** 2026-11-19 · **Kind:** record
>
> **Sources** the live cluster on 2026-11-18 and 2026-11-19, and the inventory table below

Longhorn was the cluster's replicated storage layer. It was abandoned after the 2026 disk failure and
its replica data is gone; no volume below held anything worth rescuing, by operator decision
(`docs/superpowers/specs/2026-11-10-homelab-stabilisation-design.md` §9 W0).

The storage driver and its custom resources were uninstalled first, so nothing could remove the last
objects on its own: their finalizers named controllers that no longer exist.

> **Where this stands: done, 2026-11-19.** Twenty-three volumes and three claims went on 2026-11-18;
> `n8n`, `thelounge` and `syncthing` were rebuilt on `local-path` on 2026-11-19 and their claims
> followed, `syncthing-conf` last. The page is kept as the record of what was removed and how, not as
> a queue.

> **Agents cannot run these steps.** An agent's cluster access is read-only — writes, `exec`, `cp` and
> `port-forward` answer `403` — so every cluster-side command here was the operator's. Repo-side items
> were merged.

## What was left

The table is the inventory taken before the first run, on 2026-11-18.

| Object | Count | State |
|---|---|---|
| PersistentVolume | 26 | 20 `Released`, 6 `Bound`; all `Terminating` since 2026-08-28 |
| PersistentVolumeClaim | 6 | `Terminating` since 2026-08-28, still `Bound` |
| Namespace `longhorn-system` | 1 | `Active`, empty except the `kube-root-ca.crt` ConfigMap |
| StorageClass `longhorn*` | 0 | already gone with the driver |
| Longhorn custom resources | 0 | the CRDs are uninstalled |

The namespace is empty, not "stuck on a finalizer": it holds no workload, service or secret. Deleting
it is safe once the volumes are gone.

### The six claims, and who was on them at the snapshot

| Namespace / claim | Size | Pod mounting it then | Safe to clear then |
|---|---|---|---|
| `media-stack/filebrowser-config` | 1Gi | none alive (`filebrowser-c9949b4cf-kpdxj` is `Succeeded`) | yes |
| `media-stack/filebrowser-db-2` | 1Gi | same dead pod | yes |
| `monitoring/prometheus-data` | 150Gi | none alive (`prometheus-server-7cbccb44b5-spn89` is `Failed`) | yes |
| `n8n/n8n-2` | 1Gi | `n8n-5586dbb4d5-7pzbw`, **Running** since 2026-08-13 | **no** |
| `syncthing/syncthing-conf` | 1Gi | `syncthing-75bc646f6b-b8cmq`, **Running** since 2026-08-13 | **no** |
| `thelounge/thelounge-volume` | 20Gi | `thelounge-7ff76784d7-z8fmb`, **Running** since 2026-08-13 | **no** |

The last three were still serving reads and writes from the mount the kubelet made before the failure.
Deleting the claim object would not have stopped the running process, but the application would have
lost its volume on its next restart. They were rebuilt on `local-path` first (Wave A), then their
claims went in the same way as the first three.

`n8n` was rebuilt that way on 2026-11-19. Its old pod could not finish its own teardown — the
unmount needs the driver that no longer exists — so it was force-deleted, then the volume was
patched and the claim disappeared with it. `thelounge` and `syncthing` followed the same day, and
`syncthing-conf` went last: no Longhorn claim is left.

AGENTS.md §7.4 said until 2026-11-18 that every application had been rebuilt. It had not: n8n,
syncthing and thelounge were never migrated, and nothing else mounted a Longhorn claim. §7.4 records
the resolution now, so the two documents agree.

### The twenty-six volumes

Every one carried `external-attacher/driver-longhorn-io`. The six `Bound` ones also carried
`kubernetes.io/pv-protection` until their claim was gone, and `pvc-e705d2ea-…` added
`external-provisioner.volume.kubernetes.io/finalizer`.

| PV | Claim | Size | Phase at the snapshot | Cleared |
|---|---|---|---|---|
| `bazarr-anime-config` | `media-stack/bazarr-anime-config` | 2Gi | Released | 2026-11-18 |
| `bazarr-config` | `media-stack/bazarr-config` | 2Gi | Released | 2026-11-18 |
| `cross-seed` | `media-stack/cross-seed` | 5Gi | Released | 2026-11-18 |
| `cross-seed-config` | `media-stack/cross-seed-config` | 1Gi | Released | 2026-11-18 |
| `discourse2` | `discourse/discourse2` | 20Gi | Released | 2026-11-18 |
| `filebrowser-config` | `media-stack/filebrowser-config` | 1Gi | Bound | 2026-11-18 |
| `filebrowser-db-2` | `media-stack/filebrowser-db-2` | 1Gi | Bound | 2026-11-18 |
| `freshrss-volume` | `news/freshrss-volume` | 1Gi | Released | 2026-11-18 |
| `grafana-2` | `monitoring/grafana-2` | 1Gi | Released | 2026-11-18 |
| `kavita-config2` | `media-stack/kavita-config2` | 5Gi | Released | 2026-11-18 |
| `koillection-conf` | `koillection/koillection-conf` | 1Gi | Released | 2026-11-18 |
| `koillection-uploads` | `koillection/koillection-uploads` | 20Gi | Released | 2026-11-18 |
| `n8n-2` | `n8n/n8n-2` | 1Gi | Bound | 2026-11-19 |
| `overseerr-config` | `media-stack/overseerr-config` | 20Gi | Released | 2026-11-18 |
| `prometheus-data` | `monitoring/prometheus-data` | 150Gi | Bound | 2026-11-18 |
| `prowlarr-config` | `media-stack/prowlarr-config` | 50Gi | Released | 2026-11-18 |
| `pvc-e705d2ea-c817-44b5-888a-985bed767c8c` | `discourse/redis-data-discourse-redis-master-0` | 8Gi | Released | 2026-11-18 |
| `qbittorrent-vpn-config` | `media-stack/qbittorrent-vpn-config` | 1Gi | Released | 2026-11-18 |
| `radarr-anime-config` | `media-stack/radarr-anime-config` | 20Gi | Released | 2026-11-18 |
| `radarr-config2` | `media-stack/radarr-config2` | 50Gi | Released | 2026-11-18 |
| `readarr-config` | `media-stack/readarr-config` | 20Gi | Released | 2026-11-18 |
| `sonarr-anime-config` | `media-stack/sonarr-anime-config` | 20Gi | Released | 2026-11-18 |
| `sonarr-config` | `media-stack/sonarr-config` | 20Gi | Released | 2026-11-18 |
| `syncthing-conf` | `syncthing/syncthing-conf` | 1Gi | Bound | 2026-11-19 |
| `tautulli-config2` | `media-stack/tautulli-config2` | 50Gi | Released | 2026-11-18 |
| `thelounge-volume` | `thelounge/thelounge-volume` | 20Gi | Bound | 2026-11-19 |

A `Released` volume had no claim left, so clearing it changed nothing at runtime; a `Bound` one
held its claim until the application moved to `local-path`. Every row is cleared now, and the
`Cleared` column is the answer to "is this still here" without listing PersistentVolumes first.

## Where the ten affected applications stand

The plan calls this list "eleven", but its own table and this one both hold ten.

| Application | Planned disposition | State, updated to 2026-11-19 |
|---|---|---|
| filebrowser | rebuilt in W7.1 | rebuilt, `local-path`, own namespace; old claims orphaned |
| calibre | rebuilt in Wave A | rebuilt as Calibre-Web Automated on `local-path` |
| firefly | rebuilt in Wave A | retired 2026-11-18 |
| linkstack | rebuilt in Wave A | retired 2026-11-18 |
| n8n | rebuilt in Wave A | rebuilt 2026-11-19: pod 1/1 on `n8n-config` (`local-path`), old claim and volume cleared |
| syncthing | rebuilt in Wave A | rebuilt 2026-11-19: chart creates `syncthing-config` (`local-path`), pod pinned to `nuc0`; old claim cleared 2026-11-19 |
| thelounge | rebuilt in Wave A | rebuilt 2026-11-19 on `thelounge-config` (`local-path`); old volume and claim cleared |
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

If a PV is still listed after its claim is gone, read its finalizers before touching it again:
`kubectl get pv <name> -o jsonpath='{.metadata.finalizers}'`. The patch sets them to nothing, so a
name that reappears has been re-created, not re-blocked.

Expected: `NotFound` for each volume, and no Longhorn claim left. Match on the storage class, not on
the name — the rebuilt `filebrowser` application has a `filebrowser-config` claim of its own, on
`local-path`, and a name-only grep finds it instead.

```bash
kubectl get pv filebrowser-config filebrowser-db-2 prometheus-data 2>&1
kubectl get pvc -A | grep longhorn
```

The second command printed only the two claims of phase 2.

### Phase 2 — the two claims that were in use, cleared last

`syncthing-conf` and `thelounge-volume` were mounted by running pods at the snapshot. Rebuild each
application on `local-path` (Wave A), confirm the new pod runs on the new claim, force-delete the old
pod (its teardown needs the driver that is gone), then run the Phase 1 loop with those two names.
Their PVs stayed `Bound` until then, which was correct. `n8n-2` was cleared this way on 2026-11-19,
and `thelounge-volume` and `syncthing-conf` followed the same day.

For syncthing, the order matters and the concerns are separate: the chart change (new
`syncthing-config` claim, pod pinned to `nuc0`) merges and syncs first, then the stuck
`syncthing-75bc646f6b-b8cmq` pod is force-deleted so the new claim binds on `nuc0`, and only once
the new pod runs does the loop below clear the claim and the volume — both are named
`syncthing-conf`, PV first.

```bash
kubectl -n syncthing delete pod syncthing-75bc646f6b-b8cmq --force --grace-period=0
kubectl patch pv syncthing-conf -p '{"metadata":{"finalizers":null}}' --type=merge
kubectl -n syncthing patch pvc syncthing-conf -p '{"metadata":{"finalizers":null}}' --type=merge
```

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

Expected: `0`, `0` and `0`. When the page was written the first two read `2` — the deferred claims of
Phase 2 and their volumes — and they fell to zero when `thelounge` and `syncthing` were rebuilt on
2026-11-19. A `6` or `23` on either line means a phase did not run — `23` is what the volume count
reads when phase 1 worked and phase 3 did not. A `1` on the last line is the terminating namespace
still in the list; wait and re-run.

## Found on the way, outside this cleanup

Three other leftovers are visible in the same listings and must **not** be cleared with the same
recipe:

- **Four externally-provisioned NFS volumes carry a deletion timestamp while still in use**:
  `pv-traefik-nfs` (mounted by `kube-system/traefik-nfs-c8ff497b8-jdgml` and holding the ACME
  certificate store), `pv-plex-media`, `pv-stash-nfs-nfs-stash` and
  `pv-nfs-qbittorrent-download-nfs-subdir-external-provisioner`. Their claims are `Bound` and pods are
  reading them now. They were asked to delete in December 2023 and are held only by
  `kubernetes.io/pv-protection` — the guard that every in-use volume carries — and their
  `spec.csi` is empty, which is the one thing that keeps the phase 3 filter from selecting them.
  Patch their finalizers away and the live mount goes with the object. They were decided to be kept
  on 2026-11-20; the per-volume reasons and the replacement procedure are in
  [`../how-it-works/storage.md`](../how-it-works/storage.md). **Update 2026-11-21:** all four were
  rebuilt with that procedure, so no volume carries a deletion timestamp now.
- **Four `local-path` volumes are `Released`** and hold no claim: `pvc-51a71183…`
  (`calibre-web-automated-config`), `pvc-61023024…` (`pgadmin-config`), `pvc-76b5d66d…`
  (`cross-seed2`), `pvc-801276ae…` (`plex-transcode`). Deleting the objects is safe; the directories
  on disk stay where they are, because `local-path` volumes use the `Retain` reclaim policy.
- **Two pods are stuck `Terminating` on `nuc0`**: `media-stack/filebrowser-c9949b4cf-kpdxj` and
  `monitoring/prometheus-server-7cbccb44b5-spn89`. Both are already dead (`Succeeded` / `Failed`)
  and are waiting on a container runtime teardown. They disappear with a node restart or a
  `kubelet` restart; nothing else depends on them.
