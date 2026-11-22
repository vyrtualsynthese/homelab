# The Let's Encrypt certificate store

Date: 2026-11-22

> **In one line.** Traefik keeps every certificate it has ever requested in one JSON file on an NFS
> claim, and renews all of them forever, so a retired hostname has to be deleted from that file by
> hand with the router stopped.
>
> **Use it when** a certificate exists for a hostname no route serves, a renewal alert names a host
> nobody visits, or a hostname is renamed and the old name must stop consuming the Let's Encrypt
> issuance limit · **Verified** 2026-11-22 · **Kind:** living procedure

## What the store is

Traefik answers TLS for every `*.kub.home.ashudev.com` and public route through the certificate
resolver `myresolver`: ACME over the TLS-ALPN challenge, with Let's Encrypt as the authority. Its
state is one file, `acme.json`, at `/data/acme.json` inside the router.

| | |
|---|---|
| File | `/data/acme.json` on claim `kube-system/pvc-traefik-nfs` |
| Backing volume | `pv-traefik-nfs`, NFS `192.168.1.41:/mnt/storage/traefik`, reclaim policy `Retain` |
| Owner | uid/gid `65532`, mode `0600`; the Traefik image runs as that user |
| Contents | the `myresolver` account registration, then one array entry per certificate |
| Not in it | the internal `*.kub.local` leaf, which is the Secret `kube-system/local-cert` — see [`internal-ca.md`](internal-ca.md) |

The file holds no index of which hostnames are live. A route that sets `tls.certResolver: myresolver`
asks for a certificate; once issued, the entry stays, and Traefik renews every entry it holds until
it reaches the renewal window (30 days before expiry) whether or not a router still names it. Hosts
retired from the cluster therefore keep being reissued, and every `*.kub.home.ashudev.com` name counts
against the weekly issuance limit of its registered domain (`ashudev.com`).

The router is one `Deployment` replica on `streaming`, installed with `helm` by hand; no ArgoCD
`Application` owns it, so scaling it down is not reverted by self-heal. At the 2026-11-22 run the
live release was chart `42.4.1` / Traefik `v3.7.19`, installed from
[`cluster/infrastructure/traefik/values-v3.yaml`](../../cluster/infrastructure/traefik/values-v3.yaml);
the sibling `values.yaml` and `values-2.11.yaml` belong to the earlier charts, and
[`traefik-release.md`](traefik-release.md) says which file goes with which.

## Finding the orphans

The certificate entries to remove are the store's names minus the hostnames a live router serves. Read
both from the cluster, never from the repository: a chart's route may have been removed while the
entry stayed.

```bash
# every host a live IngressRoute matches
kubectl get ingressroutes.traefik.io -A -o json \
  | jq -r '.items[].spec.routes[].match' \
  | grep -o 'Host(`[^`]*`)' | sed 's/Host(`//;s/`)//' | sort -u
```

Read the store itself through a throwaway pod mounting the claim read-only. The Traefik image is
`scratch` — it has no shell, so `kubectl exec` into it is not an option.

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: acme-inspect
  namespace: kube-system
spec:
  restartPolicy: Never
  nodeSelector:
    kubernetes.io/hostname: streaming
  containers:
    - name: inspect
      image: busybox:1.38.0
      command: ["sh", "-c", "cat /data/acme.json"]
      volumeMounts:
        - name: data
          mountPath: /data
          readOnly: true
  volumes:
    - name: data
      persistentVolumeClaim:
        claimName: pvc-traefik-nfs
        readOnly: true
```

```bash
kubectl apply -f acme-inspect.yaml
kubectl -n kube-system wait --for=jsonpath='{.status.phase}'=Succeeded pod/acme-inspect
kubectl -n kube-system logs acme-inspect > acme.json
jq -r '.myresolver.Certificates[].domain.main' acme.json | sort
kubectl -n kube-system delete pod acme-inspect
```

A name in the store that appears in neither the router list nor [`reference/routes.md`](../reference/routes.md)
is an orphan. A certificate may carry extra names in its `sans` while its `domain.main` is still
served; keep that entry, because the served name needs it.

## Pruning the store

Preconditions: the human kubeconfig (the read-only MCP ServiceAccount cannot create the maintenance
pod), and acceptance of **one restart of every route** — the whole cluster's HTTPS is down for the
half-minute the router is stopped.

1. Build the trimmed file locally and check the count before touching the cluster: with `Certs`
   loaded from the file, keep every entry whose `domain.main` is in the live-router list.

   ```bash
   jq --argjson keep '["host1.example","host2.example"]' \
     '.myresolver.Certificates |= map(select(.domain.main as $d | $keep | index($d)))' \
     acme.json > acme-new.json
   jq '.myresolver.Certificates | length' acme.json acme-new.json
   ```

2. Stop the router and confirm the pod is gone:

   ```bash
   kubectl -n kube-system scale deploy traefik --replicas=0
   kubectl -n kube-system get pods -l app.kubernetes.io/name=traefik
   ```

3. Start a maintenance pod on the same node, as the file's owner, with the claim writable. Root is
   squashed on that NFS export, so writing as root fails:

   ```yaml
   apiVersion: v1
   kind: Pod
   metadata:
     name: acme-maintain
     namespace: kube-system
   spec:
     restartPolicy: Never
     nodeSelector:
       kubernetes.io/hostname: streaming
     securityContext:
       runAsUser: 65532
       runAsGroup: 65532
       fsGroup: 65532
     containers:
       - name: maintain
         image: python:3.12-alpine
         command: ["sleep", "3600"]
         volumeMounts:
           - name: data
             mountPath: /data
     volumes:
       - name: data
         persistentVolumeClaim:
           claimName: pvc-traefik-nfs
   ```

4. Copy the old file aside **on the same volume**, then write the trimmed file in place. In-place
   truncation keeps the inode, owner and mode that Traefik expects.

   ```bash
   kubectl -n kube-system exec acme-maintain -- \
     cp -p /data/acme.json /data/acme.json.pre-orphan-cleanup-$(date +%F)
   kubectl -n kube-system cp acme-new.json acme-maintain:/tmp/acme-new.json
   kubectl -n kube-system exec acme-maintain -- \
     sh -c 'cat /tmp/acme-new.json > /data/acme.json && python3 -m json.tool /data/acme.json >/dev/null'
   kubectl -n kube-system exec acme-maintain -- ls -l /data
   ```

   Use `kubectl cp`, not `kubectl exec -i | base64 -d`: on 2026-11-22 the exec pipe delivered the
   first 108 KiB of a 204 KiB file and no error, and the store failed to parse. `kubectl cp` needs
   `tar` in the container; the Python Alpine image has it.

5. Start the router again and wait for readiness:

   ```bash
   kubectl -n kube-system scale deploy traefik --replicas=1
   kubectl -n kube-system rollout status deploy/traefik
   ```

6. Verify, and only then delete the maintenance pod:

   ```bash
   # the store still parses, and holds the expected number of entries
   kubectl -n kube-system exec acme-maintain -- \
     python3 -c 'import json;print(len(json.load(open("/data/acme.json"))["myresolver"]["Certificates"]))'

   # no error in the router log
   kubectl -n kube-system logs deploy/traefik --since=5m | grep -iE 'error|acme'

   # each kept name still gets its own certificate, and answers
   curl -sI --resolve authentik.kub.home.ashudev.com:443:192.168.1.46 \
     https://authentik.kub.home.ashudev.com/ | head -1

   kubectl -n kube-system delete pod acme-maintain
   ```

   On success the `traefik_tls_certs_not_after` series count drops by the number of entries removed,
   plus one for the internal certificate. A removed name now answers with the internal wildcard
   certificate, which does not match it, and returns `404` because no router serves it.

**Rollback:** scale the router to zero, copy the `acme.json.pre-orphan-cleanup-*` file back over
`/data/acme.json`, scale to one. The router re-reads the file at start; no ACME account action is
needed, because the account block was never touched.

## The two-certificate store

Since 2026-11-22 the release asks for two certificates instead of one per name
([design](../superpowers/specs/2026-11-22-traefik-acme-wildcard-design.md)):

| Resolver | Names | Challenge |
|---|---|---|
| `ovhresolver` | `ashudev.com`, `*.ashudev.com`, `*.kub.home.ashudev.com`, `kub.home.ashudev.com` | DNS-01 through the OVH API, using the scoped key in `kube-system/traefik-ovh` |
| `myresolver` | `ashuvidz.com`, `www.ashuvidz.com` | TLS on port 443, until the `ashuvidz.com` zone leaves AWS nameservers |

Only the apex route of each domain carries `tls.certResolver` and `tls.domains`; every other route is
served from this store by SNI. Once the switch is live, the fifteen per-host entries are orphans too,
and the second prune keeps exactly three: `ashudev.com`, `ashuvidz.com` and `kub.local`. A new
application under `ashudev.com` now orders nothing, so the store should stay at three.

## The 2026-11-22 run

The store held 34 certificates, 18 of them for hosts no router served: `atlantis`, `cross-seed`,
`dashy.kub.home`, `discourse`, `firefly`, `firefly-importer`, `forum.ashuvidz.com`, `ghostfolio`,
`guacamole`, `linkstack`, `matrix`, `monitorss`, `overseerr`, `postiz`, `synapse`, `tautulli.kub.home`,
`wiki.ashuvidz.com` and `wiki.kub.home`.

- Before: 34 entries, 429,905 bytes, md5 `23a5546e878367a25953bbca9cace389`.
- Backup: `/data/acme.json.pre-orphan-cleanup-2026-11-22`, same md5.
- After: 16 entries, 204,083 bytes, md5 `fe822754109962d75c975b5865550096`; `/data/acme.json` was
  byte-identical after the restart, so Traefik rewrote nothing on load.
- `traefik_tls_certs_not_after` went from 35 series on 2026-11-21 to 17 (16 Let's Encrypt entries plus
  `kub.local`).
- All 16 kept hostnames completed a TLS handshake with their own name in the certificate's
  `subjectAltName` and answered over HTTPS; the removed names returned `404` or failed the name check
  against the internal wildcard.
- **Renewal check:** the earliest of the kept certificates expires on 2027-01-06, so its renewal window
  opens around 2026-12-07. If the entry count is still 16 after that date, the removed names are gone
  for good; a seventeenth entry means a route still asks for an orphan and the router list above is
  the place to look.

## Traps

| Symptom | Cause |
|---|---|
| `kubectl exec` into the router fails with "no such file or directory" | the Traefik image is `scratch`; mount the claim in a maintenance pod instead |
| writing `/data/acme.json` as root answers `Permission denied` | the export root-squashes; run the pod as uid `65532`, the file's owner |
| the trimmed file does not parse after an `exec -i` pipe | the pipe truncated the payload; use `kubectl cp` |
| `/api/rawdata` answers `404` on the pod's port 9000 | the dashboard API is not attached to that entry point; read `acme.json` instead |
| Traefik logs an ACME error for a name you just removed | a router still sets `tls.certResolver: myresolver` for it and re-requested it; find the route, not the file |
| a removed name still validates | another kept certificate carries it in `sans`; that is harmless until a route serves it |
