# syncthing

Syncthing. The chart owns the Deployment, the Service, the four Traefik routes and the config
claim. The shared folder is backed up by the `nfs` stream of `cluster/helm/backup`, not by this
chart.

## Why this chart was rebuilt

The config lived on a Longhorn volume that died with the driver in August 2026 and could never
finish deleting. That claim, `syncthing-conf`, held its name until the operator cleared it on
2026-11-19 — the pod and the volume are gone — so the chart creates `syncthing-config` on
`local-path`, and the pod mounts that.

**The old config is gone by decision** — device identity, folder definitions, ignore patterns and
the index database. The shared folder on `pvc-nfs-syncthing` is intact; only the config was lost.
The rebuild therefore loses nothing that can be recovered, but the device has to be paired again and
the folder re-created before sync resumes. See
[`docs/cleanup/longhorn-disposition.md`](../../../docs/cleanup/longhorn-disposition.md).

## The parts that are not obvious

| Thing | Why |
|---|---|
| Claim is `syncthing-config`, size `1Gi` | `syncthing-conf` was held by a Terminating object until 2026-11-19, which is why the name changed. 1Gi rather than the contract's `128Mi`: the index database for the media tree does not fit in 128Mi |
| Pod pinned to `nuc0` | `local-path` is hostPath-backed, so the claim lives on one node. `nuc0` is where the app already ran and where AGENTS.md places the general catalogue. The old `node-type: general` affinity is retired and matched no node, which is why a replacement pod sat `Pending` |
| Probes on `/rest/noauth/health` | The one unauthenticated endpoint that reports the process is up and serving. Readiness shares it on purpose: a disconnected folder is a sync state, not a reason to stop routing to the WebUI — there is no dependency that can fail while the process stays healthy |
| `PUID`/`PGID` 1100, no `runAsUser` | The LinuxServer init writes `/config` as root and then drops. `runAsUser` would stop it from starting; a fresh `local-path` claim is created `0777` owned by `1100:1100`, so the app owns its own config |
| No log level | Syncthing has no verbosity environment variable; the level lives in the GUI settings (`STTRACE` only raises). It logs at `info`, which is debt, and the WebUI is the other record |

## The WebUI gate

`syncthing.kub.home.ashudev.com` is gated by `kube-system-authentik-forwardauth`, after
`kube-system-lan-only`: both middlewares are the `app.ingress.middlewares` list in `values.yaml`, and
the authentik side is `cluster/helm/authentik/files/app-syncthing.yaml`, bound to `authentik Admins`.
Syncthing has no native single sign-on, so this is the gate or nothing.

The WebUI's own login is separate and lives on the `syncthing-config` claim. Clear it in
**Settings → GUI** and tick **Insecure Admin Access** — without that tick Syncthing refuses the GUI to
anything that is not localhost, which includes every browser arriving through Traefik. Do that
**only after** the gate is proven in a browser; with no login and no gate, the WebUI is open to the
LAN.

**If authentik is down** the WebUI is unreachable; sync itself keeps running, since the sync and
discovery ports do not cross Traefik. The way back is `kubectl -n syncthing port-forward
svc/syncthing 8384:8384`. Before the click above it asks for the local login; afterwards it asks for
nothing, which is deliberate: reaching it already requires `kubectl`, and that is cluster-admin. What
it never is, is a route from the LAN.

## Backup

This chart carries none. The old `syncthing-backup` CronJob wrote to
`s3:s3.amazonaws.com/ashudev-homelab-syncthing-backup`, a bucket that never existed, so it had
never written a snapshot; the CronJob, its script ConfigMap and the `backup` values were removed
on 2026-11-20. The folder at `/data` is the `/mnt/storage/nfs/syncthing` export, which the `nfs`
stream of `cluster/helm/backup` backs up at 06:00 — `docs/how-it-works/backups.md` is the page.

## After the first sync

The claim only binds once a pod schedules, and the chart does not create the folder for you:

1. The old `syncthing-75bc646f6b-b8cmq` pod still mount-reads the dead claim and cannot finish its
   own teardown. Force-delete it before the new claim binds.
2. Open the WebUI, finish first-run setup (GUI user and password), and note the new **device ID**.
3. Re-add each peer device and accept the new ID on the peers — they will not recognise the old one.
4. Re-create each folder **with the ID its peers already use** (read it from a peer's Syncthing UI,
   under the folder's settings), pointing at `/data`. Matching the ID is what adopts the files that
   are already on the volume; a new ID makes Syncthing treat them as a second copy.
5. Force a rescan and confirm a test file syncs both ways before trusting the index.
6. Then clear the old claim and volume, PV first
   (`docs/cleanup/longhorn-disposition.md`, phase 2).
