# The Let's Encrypt certificate store

Date: 2026-11-22 · values paths re-checked 2026-11-23

> **In one line.** Traefik keeps every certificate it has ever requested in one JSON file on an NFS
> claim, and renews all of them forever, so a retired hostname has to be deleted from that file by
> hand with the router stopped.
>
> **Use it when** a certificate exists for a hostname no route serves, a renewal alert names a host
> nobody visits, or a hostname is renamed and the old name must stop consuming the Let's Encrypt
> issuance limit · **Verified** 2026-11-22 · **Kind:** how-to · **Sources**
> `cluster/helm/platform/traefik/values.yaml`, `docs/runbooks/traefik-release.md`, and the live
> `acme.json` read through the cluster.

## What the store is

Traefik answers TLS for every `*.kub.home.ashudev.com` and public route from one certificate
resolver, `ovhresolver`: ACME over the DNS-01 challenge, validated by writing a TXT record through
the OVH API, with Let's Encrypt as the authority. Its state is one file, `acme.json`, at
`/data/acme.json` inside the router. Two certificates live in it — the `ashudev.com` wildcard and the
`ashuvidz.com` pair.

| | |
|---|---|
| File | `/data/acme.json` on claim `kube-system/pvc-traefik-nfs` |
| Backing volume | `pv-traefik-nfs`, NFS `192.168.1.41:/mnt/storage/traefik`, reclaim policy `Retain` |
| Owner | uid/gid `65532`, mode `0600`; the Traefik image runs as that user |
| Contents | the `ovhresolver` account registration, then one array entry per certificate |
| Not in it | the internal `*.kub.local` leaf, which is the Secret `kube-system/local-cert` — see [`internal-ca.md`](internal-ca.md) |

The file holds no index of which hostnames are live. A route that sets `tls.certResolver: ovhresolver`
asks for a certificate; once issued, the entry stays, and Traefik renews every entry it holds until
it reaches the renewal window (30 days before expiry) whether or not a router still names it. Hosts
retired from the cluster therefore keep being reissued, and every `*.kub.home.ashudev.com` name counts
against the weekly issuance limit of its registered domain (`ashudev.com`).

The router is one `Deployment` replica on `streaming`, applied by the ArgoCD `Application` `traefik`,
which never syncs itself: scaling it down is not reverted by self-heal, because nothing is applied
without a person running a sync. At the 2026-11-23 adoption the live release was chart `42.4.1` /
Traefik `v3.7.19`, read from
[`cluster/helm/platform/traefik/values.yaml`](../../cluster/helm/platform/traefik/values.yaml), whose
keys nest under `traefik:` because that chart wraps the upstream one. `values-25.0.3.yaml` and
`values-2.11.yaml` in `cluster/infrastructure/traefik/` belong to the earlier charts, and
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
jq -r '.ovhresolver.Certificates[].domain.main' acme.json | sort
kubectl -n kube-system delete pod acme-inspect
```

A name in the store that appears in neither the router list nor [`reference/routes.md`](../reference/routes.md)
is an orphan. A bundle can serve names beyond the one it is filed under, so decide from the
certificate: `subjectAltName` on the PEM inside the entry, not the `domain` field.

## Pruning the store

Preconditions: the human kubeconfig (the read-only MCP ServiceAccount cannot create the maintenance
pod), and acceptance of **one restart of every route** — the whole cluster's HTTPS is down for the
half-minute the router is stopped.

1. Build the trimmed file locally and check the count before touching the cluster: with `Certs`
   loaded from the file, keep every entry whose `domain.main` is in the live-router list.

   ```bash
   jq --argjson keep '["host1.example","host2.example"]' \
     '.ovhresolver.Certificates |= map(select(.domain.main as $d | $keep | index($d)))' \
     acme.json > acme-new.json
   jq '.ovhresolver.Certificates | length' acme.json acme-new.json
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
     python3 -c 'import json;print(len(json.load(open("/data/acme.json"))["ovhresolver"]["Certificates"]))'

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

## The 2026-11-22 second run

Same day, after the wildcard switch, the sixteen per-host entries were pruned and the store
re-ordered. Keep-list: the `ovhresolver` wildcard only, so `myresolver` was left with no entry and
rebuilt its pair from the anchor route.

- Before: 16 `myresolver` entries plus the `ovhresolver` wildcard, 220,148 bytes.
- Backup: `/data/acme.json.pre-wildcard-cleanup-2026-11-22`; the duplicate-wildcard step left
  `/data/acme.json.pre-duplicate-wildcard-2026-11-22` beside it.
- After: one `ovhresolver` entry and one `myresolver` entry, 32,325 bytes.
- The router ordered both at start: the four-name `ashudev.com` bundle (only `kub.home.ashudev.com`
  needed a fresh DNS-01 validation; the other three authorizations were still valid) and the
  `ashuvidz.com` + `www` pair over TLS-ALPN. Both finished inside two minutes.
- All sixteen hostnames completed a TLS handshake with the right certificate, and `*.kub.local` stayed
  on the internal CA.
- One avoidable detour: a filter on `domain.sans` kept zero entries and deleted the wildcard along
  with its duplicate. Nothing was lost — the router re-issued it at once from still-valid
  authorizations — but the file had to be written twice. Filter on the PEM, not on `domain`.

## The 2026-11-22 third run: the TLS-ALPN resolver retired

Later the same day, once `ashuvidz.com` answered from OVH nameservers. This run did not prune
hostnames; it removed a whole resolver key.

- Before: two keys. `myresolver` held one entry (the `ashuvidz.com` pair, from the 01:45 run), and
  `ovhresolver` held two — the `ashudev.com` bundle and the pair it had just ordered. 44,935 bytes,
  md5 `47e350d99803ba6f5a20fd53727a7078`.
- Backup: `/data/acme.json.pre-resolver-retirement-2026-11-22`, same size and md5.
- After: one key, `ovhresolver`, with both entries. 28,811 bytes, sha256 `3a7cabae…`; byte-identical
  after the restart, so the router rewrote nothing on load.

Two facts from it that the section below only implied:

- **A changed resolver on a live router orders nothing.** The `ashuvidz.com` IngressRoute was edited
  from `myresolver` to `ovhresolver` while the old release ran, and for four minutes the router did
  nothing at all — no order, no error. The bundle was ordered 17 seconds after the router restarted
  with the new values. Do not wait on a route edit; restart the router, which is what a release move
  does anyway.
- **The order needs the second zone in the credential, and that fails quietly in the router.** A key
  without rights on `ashuvidz.com` answers the router's record write with `403`; the router logs a
  failed challenge and keeps serving whatever it already had. Prove the key against both zones with a
  throwaway TXT record before moving the release (see the retirement section below).

Timeline, all UTC: router restarted 19:18:26, DNS-01 prepared 19:18:35, validations succeeded
19:21:06, certificate 19:21:09. Let's Encrypt dates it an hour earlier than the issuance
(`notBefore` 18:22:37, a clock-skew backdate), expiring 2027-02-20. All seven public and
`ashudev.com` names checked completed a handshake with their own name in the certificate afterwards.
The router was then stopped for the prune, and came back with the store above.

## The two-certificate store

Since 2026-11-22 the release asks for two certificates instead of one per name
([design](../superpowers/specs/2026-11-22-traefik-acme-wildcard-design.md)), and since the same
day both are ordered by one resolver:

| Resolver | Names | Challenge |
|---|---|---|
| `ovhresolver` | `ashudev.com`, `*.ashudev.com`, `*.kub.home.ashudev.com`, `kub.home.ashudev.com` | DNS-01 through the OVH API, using the scoped key in `kube-system/traefik-ovh` |
| `ovhresolver` (second order) | `ashuvidz.com`, `www.ashuvidz.com` | the same key; the zone has answered from OVH nameservers since 2026-11-22 |

Only the apex route of each domain carries `tls.certResolver` and `tls.domains`; every other route is
served from this store by SNI. The store therefore holds exactly **two** entries, and `kub.local` is
not one of them — the internal leaf is the Secret `kube-system/local-cert`, never in `acme.json`. A
new application under `ashudev.com` now orders nothing, so two is the number to expect.

### Retiring the TLS-ALPN resolver (2026-11-22)

`myresolver` validated over TLS on port 443, and it existed for one reason: `ashuvidz.com` answered
from AWS nameservers, so no API could write its challenge record. When the zone's delegation moved to
`ns17/dns17.ovh.net`, the `ashuvidz.com` anchor switched to `ovhresolver` and `myresolver` left the
values file; its entry was then deleted from the store by the prune above.

Two things have to be true before the release that does this:

- **The OVH credential can write both zones.** The key in `kube-system/traefik-ovh` was scoped to
  `/domain/zone/ashudev.com/{record,record/*,refresh}` by its first run, and a key cannot widen
  itself (`PUT /auth/currentCredential` answers `405`). A new consumer key is created for the pair of
  zones, validated once in the browser, and the Secret updated with it — the router restart that
  follows the upgrade picks it up, because the values mount it with `envFrom`. The zone is reached
  by name in the API call, so a credential without rights on the second zone fails the order with a
  `403` and the name keeps whatever certificate the store already holds for it.
- **The order is watched, not assumed.** DNS-01 for `ashuvidz.com` places two authorizations; the
  first release moves both names from a certificate `myresolver` owns to one `ovhresolver` owns, so
  the apex answers with no certificate at all until the order completes.

The retirement itself is a sync with the values that no longer carry `myresolver` — a commit, then
`argocd app sync traefik` — then the
prune in §"Pruning the store" with the keep-list every `ovhresolver` entry, then a handshake per
public name. `values-25.0.3.yaml` and `values-2.11.yaml` keep the old resolver because they describe
the older charts; the live file is `cluster/helm/platform/traefik/values.yaml`.

### When Traefik orders a certificate

Two rules decide it, and both surprise.

- **A bundle is re-ordered only when a name in it is uncovered.** Traefik asks the CA for the whole
  `tls.domains` list of the router, but only after finding at least one name in that list with no
  matching certificate in the store. Adding a name to a SAN list does nothing while the existing
  per-host certificates still cover every old name; that is why the sixteen per-host entries had to
  go before the `ashuvidz.com` pair could be issued at all.
- **The stored `domain` field is not the certificate's name list.** For a bundle where one name was
  new, the entry records that one name — `{"main": "kub.home.ashudev.com"}` with no `sans` — while
  the PEM beside it carries four names. A keep-list built from `domain.main` drops bundle members, and
  a filter on `domain.sans` matches nothing. Read what an entry really covers with
  `openssl x509 -noout -ext subjectAltName` on its `certificate` field.

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
| Traefik logs an ACME error for a name you just removed | a router still sets `tls.certResolver` for it and re-requested it; find the route, not the file. Naming a resolver that no longer exists in the values fails the whole router's TLS instead |
| a removed name still validates | another kept certificate carries it in `sans`; that is harmless until a route serves it |
| a SAN added to a route changes nothing | every name in the bundle is already covered by an existing entry; prune that entry, the router orders the bundle at the next start |
| a keep-list built from `domain.main` drops names the certificate carries | the stored `domain` records only the name that was newly validated; read the PEM's `subjectAltName` instead |
