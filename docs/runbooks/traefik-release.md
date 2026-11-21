# Owning and moving the Traefik release

Date: 2026-11-21 · **Ownership moved** 2026-11-21, chart `25.0.3` (Traefik `2.10.7`), revision 8 ·
**Kind:** living runbook

> **In one line.** The Traefik release is this repository's; K3s's addon is disabled, and the
> release is moved with `helm` from one values file.
>
> **Use it when** Traefik has to move to a newer chart, its values change, or the release has to be
> inspected or rolled back · **Verified** 2026-11-21: digest unchanged, 68 routes, every host
> answering, `/metrics` serving 150 series, 121 of them stored in Prometheus.

## The shape of the problem

Traefik is not a chart in this repository. It is the upstream Traefik chart, installed in
`kube-system`, with one values file:

| What | Where |
|---|---|
| The values the release runs with | `cluster/infrastructure/traefik/values.yaml` |
| The values for the next chart lines, written and not applied | `values-2.11.yaml` (chart `27.0.2`), `values-v3.yaml` (chart `42.4.1`) |
| The chart | `traefik` `25.0.3+up25.0.0`, taken from K3s's static path (see below) |
| The certificate store | claim `pvc-traefik-nfs` mounted at `/data`; PV `pv-traefik-nfs`, NFS, `Retain` |
| The objects it owns | `Deployment`, `Service`, `IngressClass`, `IngressRoute traefik-dashboard`, `ClusterRole`, `ClusterRoleBinding`, `ServiceAccount` |

Until 2026-11-21 it was the K3s addon's release. K3s shipped two `HelmChart` objects, `traefik` and
`traefik-crd`, plus a `HelmChartConfig` applied from a drop-in file on every server, and its
helm-controller runs no Job at all in this cluster (task 530). So the addon's chart and values were
frozen, and the release had to be moved by hand. Ownership moved the same day: the addon is disabled
in the node config, the packaged objects are gone, and `values.yaml` is the only input.

**The chart source.** The upstream index (`https://traefik.github.io/charts`) no longer carries
`25.0.3`; its oldest 25.x is gone and it starts at 42.x. K3s still serves the chart it was installed
from, from its own binary:

```bash
kubectl get --raw "/static/charts/traefik-25.0.3+up25.0.0.tgz" > /tmp/traefik-25.0.3.tgz
tar xzf /tmp/traefik-25.0.3.tgz -C /tmp     # gives /tmp/traefik
grep -E '^version:|^appVersion:' /tmp/traefik/Chart.yaml   # 25.0.3+up25.0.0, v2.10.5
```

That tarball is the upstream chart as K3s repackaged it: same templates, version string
`25.0.3+up25.0.0`. Using it keeps the release's stored chart version unchanged, so an adoption
upgrade changes no label. The next move is to `27.0.2` for Traefik 2.11, which **is** on the index.

## Before

Record what runs, so the after is compared rather than assumed:

```bash
helm -n kube-system history traefik | tail -3
helm -n kube-system get values traefik | head -5
kubectl -n kube-system get deploy traefik -o jsonpath='{.spec.template.spec.containers[0].image}{"\n"}'
POD=$(kubectl -n kube-system get pod -l app.kubernetes.io/name=traefik -o jsonpath='{.items[0].metadata.name}')
kubectl -n kube-system exec "$POD" -- sha256sum /data/acme.json
for g in traefik.containo.us traefik.io; do
  for k in ingressroutes middlewares tlsoptions tlsstores; do
    printf '%-20s %-14s %s\n' "$g" "$k" "$(kubectl get $k.$g -A --no-headers 2>/dev/null | wc -l)"
  done
done
```

On 2026-11-21 before the cutover: release revision 7, chart `25.0.3+up25.0.0`, image
`rancher/mirrored-library-traefik:2.10.7`, one pod on `streaming`, `/data/acme.json`
`f074495f…`, 68 `IngressRoute`, 27 `Middleware`, 14 `TLSOption`, 1 `TLSStore` on
`traefik.containo.us`, and 1 `IngressRoute` on `traefik.io`. The full capture is
`cluster/infrastructure/traefik/snapshot/2026-11-21/`.

## Upgrading the release

Read the section above for the chart source, then:

```bash
helm upgrade traefik /tmp/traefik -n kube-system \
  --reset-values -f cluster/infrastructure/traefik/values.yaml \
  --dry-run=server > /tmp/dryrun.yaml
```

Diff `/tmp/dryrun.yaml` against `kubectl -n kube-system get deploy traefik -o yaml` before applying
anything. The only difference a values change should introduce is the change you meant to make. Then:

```bash
helm upgrade traefik /tmp/traefik -n kube-system \
  --reset-values -f cluster/infrastructure/traefik/values.yaml --wait --timeout 5m
```

`--reset-values` is not optional. Without it Helm merges with the release's stored values, and a key
removed from the file stays live — that is how `deployment.podAnnotations` and `service.ipFamilyPolicy`
survived the 2023 values for two years.

Afterwards, confirm the release holds the file rather than a merge:

```bash
helm -n kube-system get values traefik > /tmp/after.yaml
diff <(sed 's/[[:space:]]*$//' /tmp/after.yaml) \
     <(sed 's/[[:space:]]*$//' cluster/infrastructure/traefik/values.yaml) && echo "release == file"
```

## The ownership cutover, as it was done

This is history, not a repeatable step: run it only if the addon ever has to be removed again, for
example on a rebuilt server. **The order matters.**

1. **Strip the uninstall finalizers first.** Both K3s `HelmChart` objects carried
   `wrangler.cattle.io/on-helm-chart-remove`, whose handler runs `helm uninstall`. On the CRD chart
   that would delete all nine `traefik.containo.us` CRDs and, by cascade, every `IngressRoute` and
   `Middleware` in the cluster.

   ```bash
   for n in traefik traefik-crd; do
     kubectl -n kube-system patch helmchart $n --type=merge -p '{"metadata":{"finalizers":null}}'
   done
   ```

   The addon controller re-applies its manifest every few seconds; confirm the finalizers stay empty
   for a minute before going on.

2. **Disable the addon on the three servers**, one at a time, in the order `nuc0`, `rbp2`,
   `streaming`. In `/etc/rancher/k3s/config.yaml`, merge — do not append a second top-level key:

   ```yaml
   disable:
     - traefik
   ```

   On this cluster the key already held `local-storage`, so it ends
   `disable: [local-storage, traefik]`. Order does not matter; a second top-level `disable:` key
   does, because K3s refuses the file.

   Then remove the drop-in that fed the `HelmChartConfig`, and restart:

   ```bash
   sudo rm -f /var/lib/rancher/k3s/server/manifests/traefik-config.yaml
   sudo systemctl restart k3s
   ```

   `nas` is an agent: it runs no addon controller, and `k3s agent` does not accept `disable`, so it
   gets no change. Adding the key there can stop the service.

3. **Check after each node, before the next one:** the node is `Ready`, `kubectl -n kube-system get
   helmcharts,jobs` is empty, `helm -n kube-system list` still shows the release `deployed`, and the
   pod still runs. A `helm-uninstall-*` Job means the controller woke up; stop and roll back.

4. **Adopt the release** with the upgrade command above.

## After

```bash
helm -n kube-system history traefik | tail -2
kubectl -n kube-system get deploy traefik -o jsonpath='{.status.readyReplicas}{"/"}{.spec.replicas}{"\n"}'
POD=$(kubectl -n kube-system get pod -l app.kubernetes.io/name=traefik -o jsonpath='{.items[0].metadata.name}')
kubectl -n kube-system exec "$POD" -- sha256sum /data/acme.json     # must equal the before hash
```

Then prove the door still opens. A name that answers with a certificate issued by the internal CA
means the certificate store was not loaded:

```bash
for h in plex.kub.home.ashudev.com ashudev.com ashuvidz.com dashy.ashudev.com; do
  curl -s -o /dev/null -w "$h %{http_code}\n" --resolve $h:443:192.168.1.46 https://$h/
done
curl -s -o /dev/null -w 'dashboard http %{http_code}\n' --resolve traefik.kub.local:80:192.168.1.46 http://traefik.kub.local/dashboard/
curl -sk -o /dev/null -w 'dashboard https %{http_code}\n' --resolve traefik.kub.local:443:192.168.1.46 https://traefik.kub.local/dashboard/
timeout 5 bash -c '</dev/tcp/192.168.1.46/22000' && echo "syncthing tcp open"
```

Expected since 2026-11-21: `plex` `401` (Plex asking for a sign-in), `ashudev.com` and `ashuvidz.com`
`200`, `dashy` `302` to authentik, the dashboard **`301` on http then `401` on https** from its
basic-auth middleware over the internal CA, and Syncthing's port open. The dashboard answered `401`
straight on `web` until that day; the redirect comes from `redirect-to-https`, whose
`redirectScheme.permanent: true` makes Traefik answer `301`, `404` means the redirect twin is
missing, and `401` on the https line means the route carries `traefik-dashboard`.

Finally the routers Traefik built, and the metrics:

```bash
kubectl -n kube-system logs -l app.kubernetes.io/name=traefik --since=10m |
  grep -iE "multiple|duplicate|conflict" || echo "no duplicate routers"
kubectl -n kube-system exec "$POD" -- wget -qO- http://127.0.0.1:9100/metrics | grep -c '^traefik_'
```

## One pod, not a DaemonSet

The values run `deployment.kind: Deployment`, `replicas: 1`, `updateStrategy: Recreate`, pinned to
`streaming`. Task 482.

Two routers on one `acme.json` corrupt the file and re-request every name, and Let's Encrypt issues at
most five duplicates a week — so one morning of that takes every public hostname down until the window
passes. Traefik's open-source image has no lock on the file. `Recreate` matters for the same reason: a
rolling update runs the old and the new pod together for a few seconds.

Spreading the router is not an option that exists here either. The house router forwards to
`192.168.1.46` alone, so a second pod would see no external traffic; the measured load does not ask for
one (busiest hour of the seven days to 2026-11-21: 792 Mbit/s in, 248 Mbit/s out, on a 10 GbE link).

## Metrics

`metrics.prometheus.manualRouting` must stay `false`. `true` disables Traefik's default internal
router, which is the thing that serves `prometheus@internal` on the metrics entry point, and there is
no custom router to replace it: `/metrics` answered 404 at 9100 and no `traefik_*` series existed
(task 531). Prometheus scrapes the pod through its annotations on 9100, which the chart writes on the
pod itself, so the values carry no `podAnnotations`; the failure is one alert and an empty dashboard,
not a visible outage.

## Rolling back

- **Values**: `helm -n kube-system get history traefik` lists the revisions. `helm rollback` to the
  revision before the change restores that revision's stored values, which is safe only if those
  values were the current file. If in doubt, upgrade again with the snapshot:
  `helm upgrade traefik /tmp/traefik -n kube-system --reset-values -f cluster/infrastructure/traefik/snapshot/2026-11-21/release-values.yaml`.
- **The addon**: re-enabling it takes the old path back. Remove `- traefik` from `disable` on the
  three servers, restore the drop-in out of git, and restart one node at a time. K3s recreates its
  `HelmChart` objects.

  ```bash
  git show "$(git log --diff-filter=D --format=%H -1 -- \
    cluster/infrastructure/traefik/traefik-config.yaml)^:cluster/infrastructure/traefik/traefik-config.yaml" \
    | sudo tee /var/lib/rancher/k3s/server/manifests/traefik-config.yaml >/dev/null
  ```

  The captured `snapshot/2026-11-21/k3s-helmchartconfig-traefik.yaml` is that object as the
  controller held it, with its `objectset.rio.cattle.io` annotations, uid and resourceVersion: it is
  the record, not a drop-in. Do not re-enable the addon with the current release installed — the two
  owners fight over the Service.
- **The certificate** is never touched by any of this as long as the claim stays `pvc-traefik-nfs`
  and the file keeps its permissions (`65532:65532`, mode `600`). No rollback re-issues anything.

## Traps met on the way

| Symptom | Cause |
|---|---|
| `Required CRDs are missing. Please install the corresponding CRD chart` | the CRDs are older than the chart; apply the CRD chart's templates with `kubectl` first |
| `helm uninstall traefik-crd` looks harmless and deletes every route | that release's chart carries the nine CRDs in `templates/`; uninstalling it removes them. Never run it |
| a `HelmChart` deletion hangs, or runs an uninstall | `wrangler.cattle.io/on-helm-chart-remove`; strip it before anything else deletes the object |
| the release moves but the dashboard route appears twice | chart 25 renders it on `traefik.io`; the 2023 `traefik.containo.us` copy has to be deleted by hand, once |
| Syncthing stops between devices after an upgrade | the `sttcp`, `studp` and `stdiscovery` entry points are missing from the values |
| every gate stops resolving, or routes are rejected | `providers.kubernetesCRD.allowCrossNamespace` is off; every gate is referenced as `kube-system-<name>@kubernetescrd` |
| a K3s node does not come back after the config edit | a second top-level `disable:` key; K3s refuses the file. Restore `/root/config.yaml.bak-*` |
| `minecraft-minecraft-server-public … subset not found` | the minecraft Deployment is scaled to `0/0`; the error predates any upgrade |
| the dashboard answers `404` on `http://traefik.kub.local` | the chart route lives on `websecure` only; `cluster/infrastructure/traefik/dashboard/redirect.yaml` is the `web` twin that sends the browser to TLS with a `301` |
| the dashboard answers `401` on plain HTTP, no TLS | before 2026-11-21 the route was on `web` with basic auth, so the password crossed the LAN in clear; move it to `websecure` with `tls.options: internal-tls` |
| `https://traefik.kub.local` fails the certificate | the route has no `tls.options: internal-tls`, so it gets no certificate from `kube-system/local-cert` |
| `acme.json` grows with names nothing serves | certificates stay in the store after their route is deleted, and Traefik renews everything it holds; the orphans are listed in `docs/audit/traefik-2026-11-21.md` |
| metrics reachable on `192.168.1.46:9101` | `ports.metrics.expose` was `true`; Prometheus scrapes the pod's 9100 through the annotation and needs no Service port |
