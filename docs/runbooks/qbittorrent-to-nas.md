# Move qBittorrent to `nas`

**Destructive.** Both of the app's claims are deleted and rebuilt on another machine. Nothing is lost
that a copy does not hold: the two tarballs, and the two old directories that `local-path` leaves
behind on `streaming`. Read the rollback before starting.

> **In one line.** Move the qBittorrent pod from `streaming` to `nas`, so that `/downloads` is a
> directory on the machine that serves it instead of an NFS client, and carry its two configuration
> claims across with it.
>
> **Use it when** the Web UI stall fix ships: the chart now pins the pod to `nas` and mounts the
> export from the host, and the claims are still bound to `streaming` · **Verified** not yet run:
> this is the plan the chart change requires, written before the first run · **Kind:** how-to
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
| `nfs-qbittorrent-config` export | on `nas`, nothing mounts it | untouched; it stays the day-old rollback copy, retired by a follow-up card |

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
- Around 2 GiB free under `/mnt/ultrafaststorage/nfs` for the two tarballs.
- The numbers to check against, measured on 2026-11-25 22:40 with the app answering: **6,045 files**
  in the config claim, **379** `.torrent`, **379** `.fastresume`, 435 MB on disk of which 156 MB is
  resume data.
- No download in flight that matters. An unfinished torrent is re-fetched, but it is not resumed
  across this move: qBittorrent's checkpoint is taken on a clean shutdown, and the pod is stopped
  with `scale`, which is a clean shutdown.

Downtime is the tar plus the restore: expect ten to fifteen minutes on a 435 MB claim.

## Step 0 — record the before state

```bash
kubectl -n media-stack exec deploy/qbittorrent -c qbittorrent -- sh -c '
  echo "files:      $(find /config -type f | wc -l)"
  echo "torrents:   $(find /config -name "*.torrent" | wc -l)"
  echo "fastresume: $(find /config -name "*.fastresume" | wc -l)"'

kubectl -n media-stack exec deploy/qbittorrent -c qbittorrent -- \
  curl -s --max-time 10 http://127.0.0.1:8080/api/v2/torrents/info | grep -o '"hash"' | wc -l
```

Write the four numbers down. The last one is the torrent list the app itself reports; the `.torrent`
count and it should be equal, and they are what step 4 and step 8 compare against.

If the Web UI is stalled right now, the second command hangs and the first does not: `find` is a new
process on a local path, while `curl` asks the parked event loop. Drop the `curl` line, take the
torrent count from the last measurement (379 on 2026-11-25 22:40), and do not spend time on the
recovery in [`../qbittorrent-webui-stalls.md`](../qbittorrent-webui-stalls.md#the-recovery-that-worked)
— step 2 ends the stall by stopping the app, and nothing in this procedure needs it answering.

## Step 1 — take the Application's auto-sync off

In the ArgoCD UI: `qbittorrent` → **App details** → **Auto-sync** off. Then confirm from a shell:

```bash
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
          set -e
          mkdir -p /export/.migration
          tar -C /config -czf /export/.migration/qbittorrent-config.tgz .
          tar -C /vpn    -czf /export/.migration/qbittorrent-vpn.tgz .
          chmod 600 /export/.migration/*.tgz
          echo "files:      $(find /config -type f | wc -l)"
          echo "torrents:   $(find /config -name '*.torrent' | wc -l)"
          echo "fastresume: $(find /config -name '*.fastresume' | wc -l)"
          echo "entries:    $(tar -tzf /export/.migration/qbittorrent-config.tgz | wc -l)"
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

Success is four counts equal to the ones from step 0, `entries` close to the file count (the
directories are entries too), and two `.tgz` files of a few hundred MB and a few KB.

The tarballs go into a dot directory on purpose: the export is shared with the *arrs and FileBrowser,
and nothing they do walks a hidden folder at its root.

## Step 4 — verify the archive, not the copy

```bash
kubectl -n media-stack exec pod/qbittorrent-config-copy -- sh -c '
  tar -tzf /export/.migration/qbittorrent-config.tgz | grep -c "\.torrent$"
  tar -tzf /export/.migration/qbittorrent-config.tgz | grep -c "\.fastresume$"
  tar -tzf /export/.migration/qbittorrent-config.tgz | grep "qBittorrent/qBittorrent.conf"
  tar -tzf /export/.migration/qbittorrent-vpn.tgz | grep -i "wg0.conf"'
```

The two counts must be 379 each, and both `grep` lines must print a path. Anything else means the
copy is short: keep the app stopped, fix what is missing, and re-run step 3 — nothing has been deleted
yet, this is the cheap point to stop.

```bash
kubectl -n media-stack delete pod/qbittorrent-config-copy
```

## Step 5 — delete the two claims and their volumes

```bash
kubectl -n media-stack delete pvc qbittorrent-config qbittorrent-vpn-config
kubectl delete pv pvc-32470e8b-7fd8-4125-b0ae-07d5573d7a39 pvc-46192a70-1727-468a-9b2a-d21a30984e16
```

Both volumes are `Retain`, so their directories stay on `streaming` under `/mnt/ssd0/` as the
rollback copy — the deletion releases the names, it does not free the data. Their exact paths are in
`kubectl get pv` before the deletion:

```bash
kubectl get pv pvc-32470e8b-7fd8-4125-b0ae-07d5573d7a39 \
  pvc-46192a70-1727-468a-9b2a-d21a30984e16 -o custom-columns=NAME:.metadata.name,HOST:.spec.hostPath.path
```

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
git -C <checkout> switch develop && git pull --ff-only      # the sync reads develop, not a branch
argocd app sync qbittorrent --revision develop
kubectl -n media-stack rollout status deploy/qbittorrent --timeout=300s
kubectl -n media-stack get pod -o wide -l app.kubernetes.io/name=qbittorrent
```

`--revision develop` is the belt to the braces: it names the commit the pod template comes from, so a
sync cannot quietly apply an older one.

Success is `2/2 Running` on `nas` (both containers in the pod are the app and the WireGuard sidecar;
the init containers run first and exit). If it is
`Pending`, look at the reason before touching anything: `claim … is being used by another` or
`volume node affinity conflict` means a claim still points at `streaming`, and step 5 or 6 was not
finished.

Then the four checks that say the app is back, and the same ones the 24-hour gate uses:

```bash
# the torrent list the app reports: 379
kubectl -n media-stack exec deploy/qbittorrent -c qbittorrent -- \
  curl -s --max-time 10 http://127.0.0.1:8080/api/v2/torrents/info | grep -o '"hash"' | wc -l

# the exporter is scraping again: 1, and the scrape well under a second
curl -sG http://prometheus.kub.local/api/v1/query --data-urlencode \
  'query=up{app_kubernetes_io_instance="qbittorrent-exporter"}'
curl -sG http://prometheus.kub.local/api/v1/query --data-urlencode \
  'query=max(scrape_duration_seconds{app_kubernetes_io_instance="qbittorrent-exporter"})'

# the tunnel is up: a handshake, not a failed wg-quick
kubectl -n media-stack exec deploy/qbittorrent -c qbittorrent-vpn -- wg show
```

A torrent count of zero is the failure this whole copy exists to prevent: it means the config claim
did not arrive, and the app is running on an empty one. Stop the deployment before it writes over the
restore (`scale --replicas=0`) and go back to step 4.

## Step 9 — close the migration

- Auto-sync back on for `qbittorrent`, and confirm with the same `jsonpath` as step 1: the printed
  policy is the one that was there before.
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
- A follow-up card retires the `nfs-qbittorrent-config` export, its Application and its row in
  `ansible/roles/nfs/templates/exports.j2`.

## The gate

The move is proved when, over 24 hours with torrents running:

- `max_over_time(scrape_duration_seconds{app_kubernetes_io_instance="qbittorrent-exporter"}[24h])` stays
  under 1 second, and
- `count_over_time(up{app_kubernetes_io_instance="qbittorrent-exporter"}==0[24h])` is zero, and
- the torrent list is still 379.

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
