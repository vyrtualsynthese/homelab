# Move qBittorrent to `nas`

**Destructive.** Both of the app's claims are deleted and rebuilt on another machine. Nothing is lost
that a copy does not hold: the two tarballs, and the two old directories that `local-path` leaves
behind on `streaming`. Read the rollback before starting.

> **In one line.** Move the qBittorrent pod from `streaming` to `nas`, so that `/downloads` is a
> directory on the machine that serves it instead of an NFS client, and carry its two configuration
> claims across with it.
>
> **Use it when** the Web UI stall fix ships: the chart now pins the pod to `nas` and mounts the
> export from the host, and the claims are still bound to `streaming` · **Verified** 2026-11-25, one
> run: 5,935 files and 361 torrents copied and restored, 86 seconds from stop to running, and the
> exporter back at 0.086 s from a pinned 5.00 · **Kind:** how-to
>
> **Sources** `cluster/helm/media-stack/qbittorrent/values.yaml`,
> `cluster/helm/media-stack/qbittorrent/templates/persistentvolumeclaim.yaml`,
> `cluster/helm/backup/values.yaml`, [`../qbittorrent-webui-stalls.md`](../qbittorrent-webui-stalls.md)

## Why the app moves

The Web UI and the API go dead while torrents download, and the cause is the app's own write to
`/downloads`: about 2,000 cycles a second of open, 16 KiB write and close, through the kernel's NFSv4
client, until one `close()` stops returning and the thread that serves the UI waits on it. Read
[`../qbittorrent-webui-stalls.md`](../qbittorrent-webui-stalls.md) for the measurement; the short form
is that a local filesystem did not reproduce it, and the one local attempt failed on the disk under it
(`streaming`'s 25,241-hour BX500), not on the local path.

`nas` is the machine that exports `/mnt/ultrafaststorage/nfs`, on the NVMe that already answers the
other clients at 456 MB/s. Running the pod there means the same directory is read and written as a
local path: no client, no page waiting on a write nothing performs.

## What moves, and what does not

| Object | Where it is now | What happens |
|---|---|---|
| `qbittorrent-config` (2Gi) | `local-path`, PV pinned to `streaming` | copied, claim and PV deleted, rebuilt on `nas` |
| `qbittorrent-vpn-config` (128Mi) | `local-path`, PV pinned to `streaming` | copied, claim and PV deleted, rebuilt on `nas` |
| `/downloads` | NFS claim `pvc-nfs-qbittorrent-download-…` | this pod stops using the claim; nothing is copied, the chart mounts the export root as a host path |
| `pvc-nfs-qbittorrent-download-…` | `nfs-qbittorrent-download` Application | untouched, and the *arrs, unpackerr and FileBrowser keep mounting it |
| `nfs-qbittorrent-config` export | on `nas`, nothing mounts it | untouched by this move; card 769 retired it afterwards with its Application and its `fsid=11` export line |

A `local-path` claim is a directory on one machine's disk and its volume carries that machine's name,
so it cannot follow a pod to another node. That is the whole reason for the copy: **both** claims are
bound to `streaming`, and a pod pinned to `nas` with either of them still on `streaming` stays
`Pending`.

The VPN claim holds the tunnel's keys. Copying it is not optional: a rebuilt empty claim leaves the
WireGuard sidecar with no peer identity, and the app then downloads from the house address.

## Preconditions

- `kubectl` as cluster-admin. The agent's shell is read-only by design and cannot run this.
- The ArgoCD UI or CLI, to switch the `qbittorrent` Application's auto-sync off and back on. The
  switch is not in git: no `Application` under `cluster/argocd/projects/` records
  `syncPolicy.automated`, on purpose.
- Around 1 GiB free under `/mnt/ultrafaststorage/nfs` for the two tarballs: the first run's config
  archive was 134 MB, and 435 MB is what the claim holds on disk.
- The numbers to check against, measured at the first run on 2026-11-25 23:42: **5,935 files** in the
  config claim, **361** `.torrent`, **361** `.fastresume`, of which 156 MB is resume data. These are
  counts from the claim on disk, not from the WebUI: the app was holding fewer torrents than its own
  `BT_backup` listed (317), and it restored exactly those 317.
- No download in flight that matters. An unfinished torrent is re-fetched, but it is not resumed
  across this move: qBittorrent's checkpoint is taken on a clean shutdown, and the pod is stopped
  with `scale`, which is a clean shutdown.

Downtime is the tar, the restore and the sync. The first run took **86 seconds** end to end — stopped
at 23:41:38, new pod running on `nas` at 23:43:04 — because the copy is 20 s of gzip and the restore
12 s. Budget a few minutes for the typing, not for the data.

## Step 0 — record the before state

```bash
kubectl -n media-stack exec deploy/qbittorrent -c qbittorrent -- sh -c '
  echo "files:      $(find /config -type f | wc -l)"
  echo "torrents:   $(find /config -name "*.torrent" | wc -l)"
  echo "fastresume: $(find /config -name "*.fastresume" | wc -l)"'

kubectl -n media-stack exec deploy/qbittorrent -c qbittorrent -- \
  sh -c 'curl -s --max-time 10 "http://$(hostname -i):8080/api/v2/torrents/info" | grep -o "\"hash\"" | wc -l'
```

Write the four numbers down. The `.torrent` and `.fastresume` counts should be equal to each other,
and they are what step 3 compares against. The last number is the torrent list the app itself reports,
which is lower than `.torrent` on every healthy run (317 against 361 at the first): it is the one step
8 must reproduce, never the claim's.

**From the pod's own address, never `127.0.0.1`.** The auth bypass in `qBittorrent.conf` covers
`10.42.0.0/16`, and loopback is not in it: a `curl` to `127.0.0.1` answers `403`, and the count reads
zero on a healthy app. `$(hostname -i)` is the address that works.

If the Web UI is stalled right now, the second command hangs and the first does not: `find` is a new
process on a local path, while `curl` asks the parked event loop. Drop the `curl` line and take the
count from the exporter's last `qbittorrent_global_torrents` value instead (317 on 2026-11-25 22:42),
and do not spend time on the
recovery in [`../qbittorrent-webui-stalls.md`](../qbittorrent-webui-stalls.md#the-recovery-that-worked)
— step 2 ends the stall by stopping the app, and nothing in this procedure needs it answering.

## Step 1 — take the Application's auto-sync off

In the ArgoCD UI: `qbittorrent` → **App details** → **Auto-sync** off. Or from a shell:

```bash
argocd app set qbittorrent --sync-policy none
kubectl -n argocd get application qbittorrent -o jsonpath='{.spec.syncPolicy}'
```

Success is no output at all. Anything printed means a policy is still there; stop and remove it,
because a sync while the old claims are gone puts a pod back on `streaming` and provisions new
volumes there.

## Step 2 — stop the app

```bash
kubectl -n media-stack scale deploy/qbittorrent --replicas=0
kubectl -n media-stack wait --for=delete pod -l app.kubernetes.io/name=qbittorrent --timeout=300s
```

Do not go on while a `qbittorrent` pod exists: the claim is `ReadWriteOnce` and the copy pod cannot
mount it beside the app.

## Step 3 — copy both claims into the export

One pod, on `streaming` (where both claims are attached), reading them and writing into the export
over NFS. It runs as uid 1100, the same identity the app wrote with, so nothing in the archive needs
its ownership changed: the export maps a client's root to 1100 and passes 1100 through, and the app
runs as 1100.

```bash
kubectl -n media-stack apply -f - <<'EOF'
apiVersion: v1
kind: Pod
metadata:
  name: qbittorrent-config-copy
spec:
  restartPolicy: Never
  nodeSelector:
    kubernetes.io/hostname: streaming
  securityContext:
    runAsUser: 1100
    runAsGroup: 1100
  volumes:
    - name: config
      persistentVolumeClaim:
        claimName: qbittorrent-config
    - name: vpn
      persistentVolumeClaim:
        claimName: qbittorrent-vpn-config
    - name: export
      persistentVolumeClaim:
        claimName: pvc-nfs-qbittorrent-download-nfs-subdir-external-provisioner
  containers:
    - name: copy
      image: alpine:3.22
      command: ["sh", "-c"]
      args:
        - |
          set -eu
          mkdir -p /export/.migration
          tar -C /config -czf /export/.migration/qbittorrent-config.tgz .
          tar -C /vpn    -czf /export/.migration/qbittorrent-vpn.tgz .
          chmod 600 /export/.migration/*.tgz

          # The archive is checked against the claim it came from, not against a number
          # written down here: the count on the right is what the tar says it holds.
          cfg=/export/.migration/qbittorrent-config.tgz
          files=$(find /config -type f | wc -l)
          on=$(find /config -name '*.torrent' | wc -l)
          res=$(find /config -name '*.fastresume' | wc -l)
          a_files=$(tar -tzf "$cfg" | grep -vc '/$' || true)
          a_on=$(tar -tzf "$cfg" | grep -c '\.torrent$' || true)
          a_res=$(tar -tzf "$cfg" | grep -c '\.fastresume$' || true)
          echo "claim:   $files files, $on .torrent, $res .fastresume"
          echo "archive: $a_files files, $a_on .torrent, $a_res .fastresume"
          tar -tzf "$cfg" | grep -q 'qBittorrent/qBittorrent.conf'
          tar -tzf /export/.migration/qbittorrent-vpn.tgz | grep -qi 'wg0.conf'
          [ "$a_on" = "$on" ] && [ "$a_res" = "$res" ]
          echo "copy verified"
          ls -l /export/.migration
      volumeMounts:
        - name: config
          mountPath: /config
        - name: vpn
          mountPath: /vpn
        - name: export
          mountPath: /export
EOF
kubectl -n media-stack wait --for=condition=Ready pod/qbittorrent-config-copy --timeout=300s
kubectl -n media-stack logs -f pod/qbittorrent-config-copy
```

The pod does its own checking, because a pod that has finished cannot be `exec`'d into: the two
counts on the `archive:` line are compared with the `claim:` line, `qBittorrent.conf` and `wg0.conf`
must both be found, and a short copy ends in a non-zero exit. `copy verified` is the line that
matters, followed by two `.tgz` files of a few hundred MB and a few KB.

A failure keeps the pod in `Error` and the archive in place: read the log, and run step 3 again once
what is missing is clear. Nothing has been deleted yet — this is the cheap point to stop.

The tarballs go into a dot directory on purpose: the export is shared with the *arrs and FileBrowser,
and nothing they do walks a hidden folder at its root.

## Step 4 — delete the copy pod

```bash
kubectl -n media-stack get pod qbittorrent-config-copy      # Completed
kubectl -n media-stack logs pod/qbittorrent-config-copy     # still readable after it exits
kubectl -n media-stack delete pod qbittorrent-config-copy
```

## Step 5 — delete the two claims and their volumes

The volume names are derived from each claim's identity, so read them rather than retype them:

```bash
CFG_PV=$(kubectl -n media-stack get pvc qbittorrent-config     -o jsonpath='{.spec.volumeName}')
VPN_PV=$(kubectl -n media-stack get pvc qbittorrent-vpn-config -o jsonpath='{.spec.volumeName}')
echo "$CFG_PV $VPN_PV"     # the first run's were pvc-32470e8b-… and pvc-46192a70-…
kubectl get pv "$CFG_PV" "$VPN_PV" -o custom-columns=NAME:.metadata.name,HOST:.spec.hostPath.path

kubectl -n media-stack delete pvc qbittorrent-config qbittorrent-vpn-config
kubectl delete pv "$CFG_PV" "$VPN_PV"
```

Both volumes are `Retain`, so their directories stay on `streaming` under `/mnt/ssd0/` as the
rollback copy — the deletion releases the names, it does not free the data. The `HOST` column above
is where they are.

The volumes must be deleted, not only the claims: a released volume of the same name keeps the new
claim `Pending` forever, because the binding is to that volume's identity.

## Step 6 — create the two claims, and let `nas` bind them

The claims come from the chart, so they are rendered rather than retyped. Both are
`WaitForFirstConsumer`: they stay `Pending` until something on `nas` mounts them, which is the restore
Job below.

```bash
helm template qbittorrent cluster/helm/media-stack/qbittorrent -n media-stack \
  --show-only templates/persistentvolumeclaim.yaml | kubectl -n media-stack apply -f -
kubectl -n media-stack get pvc qbittorrent-config qbittorrent-vpn-config
```

Run it from a checkout of `develop`, with the chart at `cluster/helm/media-stack/qbittorrent`. The
`-n media-stack` on `kubectl apply` is not decoration: the rendered claim carries no namespace, and
without it the two claims land in the current context's default namespace while the Job waits for
nothing. `Pending` is the expected state here.

## Step 7 — restore onto `nas`

The Job is pinned to `nas`, mounts the export from the host, and extracts both archives. It binds both
claims to `nas` by being the first consumer, and it writes a test file into the export first: that is
the check that the local path is writable by uid 1100 before the app relies on it.

```bash
kubectl -n media-stack apply -f - <<'EOF'
apiVersion: batch/v1
kind: Job
metadata:
  name: qbittorrent-config-restore
spec:
  backoffLimit: 0
  template:
    spec:
      restartPolicy: Never
      nodeSelector:
        kubernetes.io/hostname: nas
      securityContext:
        runAsUser: 1100
        runAsGroup: 1100
      volumes:
        - name: config
          persistentVolumeClaim:
            claimName: qbittorrent-config
        - name: vpn
          persistentVolumeClaim:
            claimName: qbittorrent-vpn-config
        - name: export
          hostPath:
            path: /mnt/ultrafaststorage/nfs
            type: Directory
      containers:
        - name: restore
          image: alpine:3.22
          command: ["sh", "-c"]
          args:
            - |
              set -e
              touch /export/.write-test && rm /export/.write-test
              tar -xzf /export/.migration/qbittorrent-config.tgz -C /config
              tar -xzf /export/.migration/qbittorrent-vpn.tgz    -C /vpn
              test -f /config/qBittorrent/qBittorrent.conf
              test -n "$(find /vpn -name 'wg0.conf')"
              echo "files:      $(find /config -type f | wc -l)"
              echo "torrents:   $(find /config -name '*.torrent' | wc -l)"
              echo "fastresume: $(find /config -name '*.fastresume' | wc -l)"
          volumeMounts:
            - name: config
              mountPath: /config
            - name: vpn
              mountPath: /vpn
            - name: export
              mountPath: /export
EOF
kubectl -n media-stack logs -f job/qbittorrent-config-restore
```

Success is the same three counts once more, and a message that the Job completed. A `touch` that
failed means the export's permissions do not admit uid 1100 from the host; stop and fix that before
the app starts, because the app would come up and fail every download instead of the mount.

```bash
kubectl -n media-stack get pvc qbittorrent-config qbittorrent-vpn-config   # now Bound
kubectl get pv -o custom-columns=NAME:.metadata.name,NODE:.spec.nodeAffinity.required.nodeSelectorTerms[0].matchExpressions[0].values[0],HOST:.spec.hostPath.path | grep qbittorrent
kubectl -n media-stack delete job qbittorrent-config-restore
```

Both claims must read `Bound`, both volumes must name `nas`, and the Job is deleted once its log has
been read: a `Completed` Job keeps its name, so a second attempt would be refused until it is gone.

## Step 8 — sync, and start

The chart change is merged to `develop` before this runs — that is what makes the sync below render
the new pod. Sync the Application by hand (auto-sync is still off), or let the operator do it from the
UI:

```bash
argocd app sync qbittorrent --revision develop
kubectl -n media-stack rollout status deploy/qbittorrent --timeout=300s
kubectl -n media-stack get pod -o wide -l app.kubernetes.io/name=qbittorrent
```

`--revision develop` names the commit the pod template comes from, so a sync cannot quietly apply an
older one. The local checkout plays no part: ArgoCD reads the repository.

Success is `2/2 Running` on `nas` (both containers in the pod are the app and the WireGuard sidecar;
the init containers run first and exit). If it is
`Pending`, look at the reason before touching anything: `claim … is being used by another` or
`volume node affinity conflict` means a claim still points at `streaming`, and step 5, 6 or 7 was not
finished.

Then the four checks that say the app is back, and the same ones the 24-hour gate uses:

```bash
set -a; . ~/.config/secrets/homelab.env; set +a
# the torrent list the app reports: 317 at the first run, and the same before and after
kubectl -n media-stack exec deploy/qbittorrent -c qbittorrent -- \
  sh -c 'curl -s --max-time 10 "http://$(hostname -i):8080/api/v2/torrents/info" | grep -o "\"hash\"" | wc -l'

# the exporter is scraping again: 1, and the scrape well under a second (0.086 s on the first run)
curl -sG -u "ops:$PROMETHEUS_AUTH_PASSWORD" http://prometheus.kub.internal/api/v1/query --data-urlencode \
  'query=up{app_kubernetes_io_instance="qbittorrent-exporter"}'
curl -sG -u "ops:$PROMETHEUS_AUTH_PASSWORD" http://prometheus.kub.internal/api/v1/query --data-urlencode \
  'query=max(scrape_duration_seconds{app_kubernetes_io_instance="qbittorrent-exporter"})'

# the tunnel is up: a handshake, and bytes moving
kubectl -n media-stack exec deploy/qbittorrent -c qbittorrent-vpn -- wg show
```

Compare that count with step 0's, not with a number written here: the app holds fewer torrents than
its `BT_backup` lists, and what must not change is the count the app reports.

A torrent count of zero is the failure this whole copy exists to prevent — **after** checking the
`403` trap above, which reads as zero on a healthy app. If the pod's own address answers with an empty
list, the config claim did not arrive and the app is running on an empty one: stop the deployment
before it writes over the restore (`scale --replicas=0`) and go back to step 3, the archive is the
authority.

## Step 9 — close the migration

- Auto-sync back on for `qbittorrent` — `argocd app set qbittorrent --sync-policy automated
  --auto-prune --self-heal` — and confirm with the same `jsonpath` as step 1: it now prints that
  policy instead of nothing.
- Delete the two tarballs once a download has completed and a *arr import has picked it up — that is
  the proof the shared path still works for the other consumers:
  `kubectl -n media-stack exec deploy/qbittorrent -c qbittorrent -- rm -rf /downloads/.migration`.
- Delete the two old directories on `streaming` after the 24-hour gate below, and only then:
  `/mnt/ssd0/pvc-32470e8b-7fd8-4125-b0ae-07d5573d7a39_media-stack_qbittorrent-config` and
  `/mnt/ssd0/pvc-46192a70-1727-468a-9b2a-d21a30984e16_media-stack_qbittorrent-vpn-config`. They are
  root-owned on the node; they are the last copy of the config, so removing them is the end of the
  rollback.
- The `ScrapeTargetDown` silence on the qBittorrent exporter runs to 2026-12-09. It comes off when
  this is observed, not before: see [`../qbittorrent-webui-stalls.md`](../qbittorrent-webui-stalls.md).
- Card 769 retires the `nfs-qbittorrent-config` export, its Application and its row in
  `ansible/roles/nfs/templates/exports.j2`.

## The first run, 2026-11-25 23:41–23:43

- **86 seconds** from the stop to the new pod running on `nas`.
- The copy took 20 s and produced a 134 MB archive; it printed `claim: 5,935 files, 361 .torrent, 361
  .fastresume`, the same on the `archive:` line, and `copy verified`.
- The restore took 12 s and printed the same three counts. Both claims bound to new volumes on `nas`
  (`pvc-b111a24a-…` for the config, `pvc-04b87c12-…` for the tunnel), and the old ones stayed on
  `streaming` as the rollback.
- The app restored **317** torrents — the same number the exporter reported before the stop
  (`qbittorrent_global_torrents` last read 317; over the preceding 24 h it moved between 316 and 383,
  which is the restarts dropping entries, not this move).
- The exporter went from a scrape pinned at **5.00 s** to **0.086 s**, `up=1`, with
  `connection_status=connected`; the tunnel showed a handshake 21 s old and 3.15 GiB received.
- `radarr/` and `tv-sonarr/` were being written again one minute after the pod started, which is the
  proof the shared directory still works for the other consumers. The tarballs were deleted then.
- A `pvc-c46cd7e4-…` from the removed `qbittorrent-temp` claim is still `Released` on `local-path`,
  400 GiB claimed and empty. It is a leftover of the 2026-11-25 removal, not of this move, and it has
  no consumer left to bind it.

The 24-hour gate below had not been observed when this section was written.

## The gate

The move is proved when, over 24 hours with torrents running:

- `max_over_time(scrape_duration_seconds{app_kubernetes_io_instance="qbittorrent-exporter"}[24h])` stays
  under 1 second, and
- `count_over_time(up{app_kubernetes_io_instance="qbittorrent-exporter"}==0[24h])` is zero, and
- the torrent list the app reports is the one it restored (317 at the first run).

Both queries are written out in [`../qbittorrent-webui-stalls.md`](../qbittorrent-webui-stalls.md#the-gate-and-the-way-back).
A restart is not part of the gate: before this change the liveness probe was restarting the container
during the stall and the stall survived it.

## Rollback

Two independent ways back, both cheap while the tarballs exist.

**Before the app has written anything new** — the common case, a step failed: stop the deployment,
delete the two claims and their `nas` volumes, and put the claims back on `streaming`. Creating a
`local-path` claim by hand and leaving it `Pending` is not enough: it has to be consumed by a pod on
`streaming`, which is what a copy Job pinned there does inside the same namespace. The tarballs are
still in the export at that point, and the old directories on `streaming` never left.

**After the app has run** — the gate failed: the same move in reverse, with the tarballs as the source
(the live claim has been written to since, so the archive is the consistent copy), and a revert of the
chart change in a pull request of its own: `nodeSelector` back to `streaming` and `storage.downloads`
back to the NFS claim. That claim was never deleted, so it is still there, and nothing in it changed
during the move.

Nothing on the export is deleted by this procedure except the two tarballs, and nothing on the old
NFS config export has been read or written since 2026-11-25. That export is the third copy, a day
older than the two above.
