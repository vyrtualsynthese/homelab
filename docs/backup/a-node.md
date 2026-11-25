# A dead node

> **In one line.** A dead node takes the applications pinned to it and the claims on its own disk, but
> not the data: every claim is in a nightly stream, `/etc` is in it too, and `local-path` never deletes
> a directory, so a rebuilt node can hand its claims back.
>
> **Use it when** a machine will not boot, its disk has failed, or it is being replaced ·
> **Verified** 2026-11-22 · **Kind:** how-to
>
> **Sources** `cluster/helm/backup/values.yaml`, `docs/how-it-works/nodes.md`,
> `docs/how-it-works/storage.md`, `docs/runbooks/restore.md`

## What went with it

Workloads are pinned by `kubernetes.io/hostname`, so the applications on that node are down until it
returns or they are moved. The tables below are the claims that node's stream restores, and a claim
missing from them that no sentence here explains is a defect in the matrix rather than a reason to
improvise.

Three claims are on a node's disk and deliberately in no stream: Prometheus (25 Gi) and Loki (20 Gi)
on `nas`, whose history is worth less than a copy of it, and the PostgreSQL **data** claim on `nas`,
which is not copied because its databases are dumped instead — that dump is `postgres-dumps`, the
first row below.

| Node | Claims on its `local-path` |
|---|---|
| `nas` | `postgres-dumps`, `grafana`, `crowdsec-db-pvc`, `crowdsec-config-pvc`, `qbittorrent-config`, `qbittorrent-vpn-config` |
| `nuc0` | `pgadmin-config`, `n8n-config`, `news-freshrss-config`, the two `filebrowser` claims, `crowdsec-web-ui-config`, `koillection-conf`, `syncthing-config`, `thelounge-config`, `vikunja-data`, `recyclarr-config` |
| `streaming` | the sixteen media-stack configuration claims — `sonarr`, `sonarr-anime`, `radarr`, `radarr-anime`, `lidarr`, `prowlarr`, `bazarr`, `bazarr-anime`, `autobrr`, `cross-seed`, `seerr`, `tautulli`, `maintainerr`, `plex`, `kavita`, `calibre-web-automated` — plus `calibre-web-automated-ingest` |

The NFS exports are not on a node's disk: they are on `nas`, and losing `streaming` or `nuc0` leaves
them serving. Losing `nas` is a different page — [what a loss costs](what-a-loss-costs.md) is the
weighing, and the exports there are one copy by decision.

## The order to rebuild in

1. **Keep the disk.** Never wipe a failing disk, and never delete a claim to "reset" it: `local-path`
   reclaims `Retain`, so the bytes outlive the claim object and can be copied off a mounted disk. This
   is the step people skip, and it is the only one that is irreversible when skipped.
2. **The host, before the cluster.** The node's identity, its `/etc` — `rancher/k3s`, `exports`,
   `netplan`, `systemd/system` — all four are in that node's stream under `/host/`.
3. **Rejoin it to K3s** at the version the other nodes run, and let the existing Applications schedule
   back. Nothing needs re-creating in git: the placement is a `nodeSelector`, and it is already written.
4. **The NFS exports**, if the dead node is `nas`: the directories are on the array, and the
   provisioner release recreates the classes.
5. **The claims**, from `nodes-<node>`, last. For each one, follow
   [a file, or a whole claim](a-file-or-a-claim.md): a `sqlite` row is rebuilt from both halves, a
   `files` row from its directory. Do them in this order — the databases first, an application's
   configuration second — because an application started against an empty database writes over what you
   were about to restore.

**Success** is each Application `Synced` and `Healthy`, each application listing what it held, and
`kubectl get pv` printing nothing in `Terminating`. **Rollback** is the claim-level one: the restored
tree sits beside the old one inside the claim until you delete it.

## The traps

- **A rebuilt node's claims are new objects.** `local-path` binds on first use, so the first pod to ask
  gets a fresh directory. Restore into that directory before the application is allowed to run, or the
  application writes an empty database into it and the restore has to be done twice.
- **A borrowed volume.** An application whose claim is an NFS class does not care which node it runs
  on; an application whose claim is on a dead node's disk does. Check the claim's class before
  concluding the node is the fault — [storage.md](../how-it-works/storage.md) has the eight classes.
- **The etcd upload is not a node's backup.** A control-plane server uploads the cluster's object
  state, which holds no claim and no file of the node's own: a rebuilt node takes its claims and its
  `/etc` from that node's stream. Two control-plane nodes down is a cluster rebuild, not a node
  rebuild: that is [when the cluster is gone](no-cluster.md), and the object state comes back through
  [restore-etcd.md](../runbooks/restore-etcd.md).

The full command sequence for a claim remains [restore.md](../runbooks/restore.md); this page decides
what to do first, and in what order.
