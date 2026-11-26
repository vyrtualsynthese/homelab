# Owning and moving the Traefik release

Date: 2026-11-21 · **Ownership moved** 2026-11-21 at chart `25.0.3`, revision 8 · **Moved to 2.11**
the same day, chart `27.0.2` (Traefik `2.11.63`), revision 10 · **Moved to v3** the same day, chart
`42.4.1` (Traefik `3.7.19`), revision 11 · **Configuration audited** 2026-11-22, revision 12 ·
**Adopted by ArgoCD** 2026-11-23: Application `traefik`, manual sync, one sync, no diff left, Helm
record deleted · **Moved to chart `43.0.0`** (Traefik `3.8.0`) 2026-11-25 through that Application,
with the certificate store's owner repaired the same day

> **In one line.** The Traefik release is this repository's, applied by an ArgoCD Application from a
> wrapper chart in git, and synced by hand. `helm` is the fallback when ArgoCD is the broken thing.
>
> **Use it when** Traefik has to move to a newer chart, its values change, the release has to be
> inspected or rolled back, or the Application has to be synced by hand or taken back out of ArgoCD ·
> **Verified** 2026-11-25 on chart `43.0.0`, Traefik `3.8.0`: the render against `42.4.1` was empty,
> the sync left `Synced`/`Healthy` at revision `3640e9fb`, the pod was replaced once with zero
> restarts, and both public domains answer with a Let's Encrypt certificate ·
> **Kind:** how-to · **Sources** `cluster/helm/platform/traefik/`,
> `cluster/argocd/projects/platform/traefik.yaml`, `cluster/infrastructure/traefik/`,
> `docs/how-it-works/network.md`.

## The shape of the problem

Traefik is not a chart in this repository: it is the upstream Traefik chart, wrapped by a small chart
of ours in `cluster/helm/platform/traefik` that pins it at one exact version, applied in `kube-system`
by the ArgoCD Application `traefik`. A wrapper carries the subchart's values under the dependency's
name, so the live file nests everything under `traefik:` — that indentation is the wrapper's, not
decoration.

| What | Where |
|---|---|
| The values the release runs with | `cluster/helm/platform/traefik/values.yaml` (chart `43.0.0`), nested under `traefik:` |
| The Application that applies them | `cluster/argocd/projects/platform/traefik.yaml`, manual sync, `helm.skipCrds` |
| The values a rollback to chart `27.0.2` needs | `cluster/infrastructure/traefik/values-2.11.yaml` (flat) |
| The values a rollback to chart `25.0.3` needs | `cluster/infrastructure/traefik/values-25.0.3.yaml` (flat) |
| The chart | `traefik` `43.0.0` from the upstream index, pinned in the wrapper's `Chart.yaml`; `25.0.3+up25.0.0` came from K3s's static path (see below) |
| The certificate store | claim `pvc-traefik-nfs` mounted at `/data`; PV `pv-traefik-nfs`, NFS, `Retain` |
| The objects it owns | `Deployment`, `Service`, `IngressClass`, `IngressRoute traefik-dashboard`, `ClusterRole`, `ClusterRoleBinding`, `ServiceAccount` |

Until 2026-11-21 it was the K3s addon's release. K3s shipped two `HelmChart` objects, `traefik` and
`traefik-crd`, plus a `HelmChartConfig` applied from a drop-in file on every server, and its
helm-controller runs no Job at all in this cluster (task 530). So the addon's chart and values were
frozen, and the release had to be moved by hand. Ownership moved the same day: the addon is disabled
in the node config, the packaged objects are gone, and the wrapper chart's `values.yaml` is the only
input.

**The chart source.** The upstream index (`https://traefik.github.io/charts`) no longer carries
`25.0.3`; its oldest 25.x is gone and it starts at 42.x. K3s still serves the chart it was installed
from, from its own binary:

```bash
kubectl get --raw "/static/charts/traefik-25.0.3+up25.0.0.tgz" > /tmp/traefik-25.0.3.tgz
tar xzf /tmp/traefik-25.0.3.tgz -C /tmp     # gives /tmp/traefik
grep -E '^version:|^appVersion:' /tmp/traefik/Chart.yaml   # 25.0.3+up25.0.0, v2.10.5
```

That tarball is the upstream chart as K3s repackaged it: same templates, version string
`25.0.3+up25.0.0`. Using it kept the release's stored chart version unchanged, so the adoption
upgrade changed no label. The release moved to `27.0.2` for Traefik 2.11 the same day, from the
upstream index, and to `42.4.1` the same evening; the wrapper now pins that version.

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

A move is now a change in git, because the version lives in the wrapper's `Chart.yaml`. Read the
target chart's own `values.yaml` first — the schema moves between lines, and this chart rejects
unknown keys (`additionalProperties: false`).

1. Bump `dependencies[0].version` in `cluster/helm/platform/traefik/Chart.yaml`, and `appVersion` to
   the application version inside the new chart's image tag.
2. Apply the new chart's `traefik.io` CRDs from a pulled tarball. The Application renders with
   `helm.skipCrds`, so a sync never touches a CRD, and a chart whose CRDs are missing fails its first
   render (the `ServersTransportTCP` lesson, item 9 of `AGENTS.md` §7).
3. Merge the values against the new schema, then render and diff against the live objects:

```bash
helm dependency build cluster/helm/platform/traefik
helm template traefik cluster/helm/platform/traefik -n kube-system > /tmp/render.yaml
kubectl -n kube-system get deploy,svc -n kube-system -l app.kubernetes.io/name=traefik -o yaml > /tmp/live.yaml
```

Diff the two before anything is applied. The only difference a values change should introduce is the
change you meant to make; the fields the API server defaults, the Service's port order and the
`load-balancer-cleanup` finalizer are expected and nothing else is.

4. Merge the pull request, then sync by hand — see the section below. Nothing reaches the cluster
   before that sync.

The hand-run command still exists, for the case where ArgoCD is the broken thing:

```bash
FILE=cluster/helm/platform/traefik/values.yaml
helm upgrade traefik cluster/helm/platform/traefik -n kube-system \
  --reset-values -f "$FILE" --dry-run=server > /tmp/dryrun.yaml
helm upgrade traefik cluster/helm/platform/traefik -n kube-system \
  --reset-values -f "$FILE" --wait --timeout 5m
```

`--reset-values` is not optional. Without it Helm merges with the release's stored values, and a key
removed from the file stays live — that is how `deployment.podAnnotations` and `service.ipFamilyPolicy`
survived the 2023 values for two years.

After a hand-run upgrade, confirm the release holds the file rather than a merge. `helm get values`
re-serialises the YAML and prints a `USER-SUPPLIED VALUES:` header, so a text diff never matches; the
file nests under `traefik:`, so compare that key:

```bash
helm -n kube-system get values traefik > /tmp/after.yaml
python3 - <<'EOF'
import yaml
raw = open('/tmp/after.yaml').read().split('\n', 1)[1]        # drop the header line
release = (yaml.safe_load(raw) or {}).get('traefik') or {}
live = yaml.safe_load(open('cluster/helm/platform/traefik/values.yaml'))['traefik']
print('release == file' if release == live else 'DIFFERS: the release holds a merge')
EOF
```

## Running it from ArgoCD

The Application `traefik` applies this chart to `kube-system`. **It never syncs itself:** the
operator's decision, because a bad sync here takes down every route, the ArgoCD UI included. The
file records no `automated` block, and adding one is a regression — a root-app sync rewrites the
Application to match git and the automation silently disappears.

```bash
argocd login argocd.kub.local --sso          # authentik; there is no local admin
argocd app diff traefik                      # read this before anything else
argocd app sync traefik
```

The first sync ran with `--server-side --force`, because the objects were created by Helm and its
field manager owns the fields ArgoCD was about to claim. That is a one-time need: later syncs are
plain applies.

**What the adoption measured.** On 2026-11-23, `develop` at `9b7b966e`, the diff read before the sync
was one line per object — `argocd.argoproj.io/tracking-id` — and nothing else, so the Application
needs no `ignoreDifferences` at all; entries are still added only for noise a diff printed. The pod
kept its name, its creation time and its zero restarts across the sync, which is what proves no
certificate was re-requested, and the routes answered as before (`plex` 401, `dashy` 302,
`ashuvidz.com` 200, the dashboard's `/dashboard/` 401 behind its own login and its port 80 twin 301).

A later plain sync changed no object's spec, but it did client-side-apply the dashboard
`IngressRoute` and write `kubectl.kubernetes.io/last-applied-configuration` onto it, with a warning,
because Helm never wrote that annotation on the objects it created. ArgoCD does not report it as a
diff. Making every sync server-side (`syncOptions: [ServerSideApply=true]`) would stop that
annotation appearing; it is not set today.

Two rules this configuration depends on:

- **`helm.skipCrds: true`.** Without it a sync applies the chart's `crds/` directory: fifteen
  `hub.traefik.io` CRDs that need a licence and gate nothing here, plus ownership of the ten
  `traefik.io` ones the manual step owns. The diff could then never be empty.
- **`ignoreDifferences` only for noise an observed diff named.** The Application in git carries none.
  The entries to expect are the Deployment's `deployment.kubernetes.io/revision` annotation and the
  Service's `load-balancer-cleanup` finalizer, because the controllers add both to every object they
  touch — but an entry goes in only after `argocd app diff` printed it, never from this guess.

## Getting back out of ArgoCD

Deleting the Application with the default cascade deletes the objects it manages — including the
Service the house router forwards to. Orphan them instead, then take the release back under `helm`:

```bash
kubectl -n argocd delete application traefik --cascade=orphan
helm dependency build cluster/helm/platform/traefik
helm upgrade traefik cluster/helm/platform/traefik -n kube-system --reset-values \
  -f cluster/helm/platform/traefik/values.yaml --wait --timeout 5m
helm -n kube-system list        # the release is Helm's again
```

The objects were never deleted, so the pod, the Service and its `nodePort` values, the claim and the
certificate store are the same throughout.

**The trap adoption creates.** Once ArgoCD owns the objects, the Helm release record from before is
still in the cluster until somebody deletes it — and `helm uninstall traefik` deletes the objects
themselves, which is every route in the house. The record is removed only after a clean ArgoCD sync,
and never before:

```bash
helm -n kube-system history traefik | tail -1               # read the revision <N>
kubectl -n kube-system delete secret sh.helm.release.v1.traefik.v<N>
helm -n kube-system list                                     # empty
```

Deleting that Secret forgets the release and touches no object.

**Done, 2026-11-23.** Ten records existed, revisions 8 to 17, the adopting revision among them.
All ten went, with the release's own label selector rather than one name:

```bash
kubectl -n kube-system get secret -l owner=helm,name=traefik   # the ten, before
kubectl -n kube-system delete secret -l owner=helm,name=traefik
helm -n kube-system list                                       # header only
helm -n kube-system history traefik                            # release: not found
```

The whole set and not just the deployed revision: a later `helm install traefik` starts again at
revision 1, and a leftover `…v1` Secret is a name the new record cannot take. The pod kept its name,
its creation time and its zero restarts, every route answered as before, and `argocd app diff` stayed
empty — forgetting the release touched no object.

## Moving to 2.11, chart `27.0.2`

Done 2026-11-21, revision 9 → 10, with `values-2.11.yaml` and `docker.io/traefik:v2.11.63`.

**Apply the CRDs first, one group at a time.** Helm never replaces a CRD that already exists, and
chart 27's schemas are the ones that declare `ipAllowList` in both groups. The two groups do not hold
the same files — `traefik.containo.us` has nine, `traefik.io` has ten, and
`serverstransporttcps` exists only in `traefik.io` — so pass the globs, never a hand-written list:

```bash
helm repo add traefik https://traefik.github.io/charts && helm repo update traefik
helm pull traefik/traefik --version 27.0.2 --untar --untardir /tmp/traefik-2.11
kubectl apply -f /tmp/traefik-2.11/traefik/crds/traefik.io_*.yaml
kubectl apply -f /tmp/traefik-2.11/traefik/crds/traefik.containo.us_*.yaml
kubectl get crd middlewares.traefik.io \
  -o jsonpath='{.spec.versions[0].schema.openAPIV3Schema.properties.spec.properties.ipAllowList.type}{"\n"}'
```

Then the upgrade command above, and the `After` checks. Measured that day: the Deployment diff was one
line, the image; every container argument, the `Recreate` strategy, the `streaming` node selector,
the claim, the `prometheus.io/*` annotations, the dashboard `IngressRoute` and the Service's six ports
(and their `nodePort` values) were unchanged. `/data/acme.json` kept
`f074495fb3b8ad9f…` — no re-issue — and the log carried no duplicate-router warning.

Rollback is `helm -n kube-system rollback traefik 9`: chart `25.0.3` with the stored values. The
chart 27 CRDs stay behind and are a superset of the 2.10 schemas, so the older router keeps working
against them.

## Moving to v3, chart `42.4.1`

Done 2026-11-21, revision 10 → 11, with `values-v3.yaml` and `docker.io/traefik:v3.7.19`. That file is
the wrapper chart's `values.yaml` since 2026-11-23, one indentation level down under `traefik:`; the
commands below are the record of how the move was run, not a script to re-run as written.

**Check the CRDs before applying them.** Chart 42's schemas are a strict superset of chart 27's:
`scripts`-free comparison of both trees found no removed property and no changed type on any shared
path. That is what makes this order safe — the v3 CRDs go in while 2.11 is still serving.

```bash
helm pull traefik/traefik --version 42.4.1 --untar --untardir /tmp/traefik-v3
for f in /tmp/traefik-v3/traefik/crds/traefik.io_*.yaml; do kubectl apply -f "$f"; done
curl -s -o /dev/null -w '%{http_code}\n' --resolve dashy.ashudev.com:443:192.168.1.46 \
  https://dashy.ashudev.com/                      # 302: 2.11 still routing
helm upgrade traefik /tmp/traefik-v3/traefik -n kube-system --reset-values \
  -f cluster/infrastructure/traefik/values-v3.yaml --dry-run=server > /tmp/dryrun.yaml
helm upgrade traefik /tmp/traefik-v3/traefik -n kube-system --reset-values \
  -f cluster/infrastructure/traefik/values-v3.yaml --wait --timeout 5m
```

The chart also ships fifteen `hub.traefik.io` CRDs. They are the Hub API gateway, need a licence and
gate nothing here: apply only the `traefik.io_*` files.

Measured that day: the pod spec was identical except the image, `seccompProfile: RuntimeDefault`
(added), `--global.checknewversion` (gone with v3) and five chart-42 defaults — `log.level=INFO`,
`allowEmptyServices=true` on both providers, and `requestAcceptGraceTimeout=5s` on `web` and
`websecure`. The `Recreate` strategy, the `streaming` pin, the claim, the probes, the scrape
annotations, the dashboard route and the six Service ports with their `nodePort` values all held.
`/data/acme.json` kept `f074495fb3b8ad9f…`.

**Then retire the 2023 CRDs.** They hold no objects any more, and the Helm release that owned them
lost its chart years ago:

```bash
kubectl -n kube-system delete secret sh.helm.release.v1.traefik-crd.v1   # forgets, deletes nothing
helm -n kube-system list                                                 # only `traefik`
kubectl delete crd ingressroutes.traefik.containo.us ingressroutetcps.traefik.containo.us \
  ingressrouteudps.traefik.containo.us middlewares.traefik.containo.us \
  middlewaretcps.traefik.containo.us serverstransports.traefik.containo.us \
  tlsoptions.traefik.containo.us tlsstores.traefik.containo.us traefikservices.traefik.containo.us
```

Rollback is `helm -n kube-system rollback traefik 10` — 2.11.63 under chart `27.0.2`, which runs
against the v3 CRDs. It is **not** safe once the `containo.us` CRDs are deleted to go back to chart
`25.0.3`: every object has been on `traefik.io` since the rename, and the older chart expects its
own group. The metrics change with v3 as well: 111 series instead of 173, `traefik_open_connections`
loses its `router_` prefix, and `traefik_config_reloads_failure_total` no longer exists — two
Traefik dashboard panels read those names.

## The ownership cutover, as it was done

This is history, not a repeatable step: run it only if the addon ever has to be removed again, for
example on a rebuilt server. **The order matters.**

**Re-enabling the addon is not a rollback any more.** The `traefik.containo.us` CRDs were deleted on
2026-11-21 and every route has been on `traefik.io` since; the addon's chart `25.0.3` reads only the
old group, so switching it back on would leave it routing nothing while the release's own router is
removed — every hostname dark. What this section is kept for is a rebuilt node or a rebuilt cluster;
the way out of ArgoCD is the section above.

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
curl -s --cacert ~/.config/secrets/kub-local/local-ca.pem \
  -o /dev/null -w 'dashboard https %{http_code}\n' --resolve traefik.kub.local:443:192.168.1.46 https://traefik.kub.local/dashboard/
curl -s --cacert ~/.config/secrets/kub-local/local-ca.pem \
  -o /dev/null -w 'dashboard with password %{http_code}\n' \
  -u 'vronteix:<password>' --resolve traefik.kub.local:443:192.168.1.46 https://traefik.kub.local/dashboard/
timeout 5 bash -c '</dev/tcp/192.168.1.46/22000' && echo "syncthing tcp open"
```

Expected since 2026-11-21: `plex` `401` (Plex asking for a sign-in), `ashudev.com` and `ashuvidz.com`
`200`, `dashy` `302` to authentik, the dashboard **`301` on http, `401` on https without credentials
and `200` with them** over the internal CA, and Syncthing's port open. The dashboard answered `401`
straight on `web` until that day; the redirect comes from `redirect-to-https`, whose
`redirectScheme.permanent: true` makes Traefik answer `301`, and `404` means the redirect twin is
missing.

**`401` on its own proves nothing.** A `basicAuth` middleware whose Secret has no `users` key also
answers `401`, to every password — that was the live state from 2023 until 2026-11-22, when the
Secret's hash was under `auth` instead. Only the `-u` line above distinguishes "protected" from
"unusable": it must answer `200`. The Secret and its key are in [`../secrets.md`](../secrets.md).

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

## The 3.7 configuration audit (2026-11-22, revision 12)

The v3 move renamed the keys; it did not review what they say. This pass read every option the
release still inherits from the 2.x file, or from the chart's stock values, and settled the startup
warnings. Revision 12 carries the result, and `helm -n kube-system rollback traefik 11` is the way
back to the unaudited configuration.

| What | Decision | Why |
|---|---|---|
| `providers.kubernetesCRD.safeNaming` | `false` | On, every generated router and service is renamed (`kube-system_lan-only@kubernetescrd`), and **85** middleware references in the chart templates are written in the legacy `namespace-name@kubernetescrd` form. A reference in the wrong form does not resolve, so the first sync would drop every gate at once. The collision it prevents needs two namespace/name pairs that concatenate to the same string; this cluster has none. `false` keeps the names and silences the warning. |
| `global.aliasHeadersStrategy` | `delete` (v3.7.17+) | A header spelled `X_Auth_User` or `X.Auth.User` is read as `X-Auth-User` by any backend that derives variable names from header names, which lets a client spoof one Traefik manages. authentik is Python WSGI and trusts the headers it receives. `delete` drops the alias; `reject` would answer `400` to any client that sends such a header, innocuous private ones included. |
| `ports.web.forwardedHeaders.insecure` and `ports.websecure.forwardedHeaders.insecure` | removed | The router in front is an Orange Livebox doing plain NAT, and it sets no `X-Forwarded-*` header. Trusting them only trusted the client, which could rewrite the host and scheme its backends see. With the flag gone Traefik writes the values itself. |
| `ports.web.proxyProtocol.insecure` and `ports.websecure.proxyProtocol.insecure` | removed | **Measured hole, 2026-11-22.** A request to `calibre.kub.local` carrying `PROXY TCP4 192.168.1.99 …` was answered `301` like a LAN client; the same request claiming `8.8.8.8` was answered `403`. The header let the caller choose its own source address, and the Livebox forwards 80 and 443 while Traefik chooses the route from the `Host` header alone, so the `lan-only` gate could be walked through from the internet. Nothing on this network sends the header. The same request now answers `400`, and normal traffic is unchanged. |
| `ports.websecure.transport.respondingTimeouts.readTimeout` | `1h` | v3 raised this default from `0` to `60s`, a cap on the whole request including its body. FileBrowser is public, and a 2 GB upload on a 20 Mbit/s line needs about 800 s, so the v3 default would cut it. `1h` leaves every real upload inside and still bounds a slow connection. |
| `writeTimeout`, `idleTimeout` | chart defaults: `0` and `180s` | `writeTimeout: 0` puts no cap on a response, which is what a Plex stream needs. `180s` is the keep-alive idle limit the 2.x file also ran; a paused stream reconnects. |
| `serversTransport.maxIdleConnsPerHost` | `100` via `additionalArguments` | The chart does not expose it, and its `0` means Go's default of two idle connections per host: Plex and qBittorrent reopen a connection for every request beyond two. 100 is per host, not a global pool. |
| `serversTransport.forwardingTimeouts` | chart defaults: dial `30s`, response headers `0`, idle `90s` | A response-header timeout is the one that would cut Plex while it starts a transcode, so `0` stays. A backend that never answers holds one connection, which is the smaller cost. |
| `metrics.prometheus.addEntryPointsLabels` | `true` | Without it `traefik_entrypoint_requests_total` does not exist and no per-entry-point traffic panel is possible. Measured after the change: five entry points carried 66 series at startup, and it grows with the status codes and methods actually seen, not with configuration. The dashboard gained "Requests per second by entry point". |
| access log | on since 2026-11-22 (revision 13; revision 14 kept it) | JSON, no timed flush (`bufferingSize: 100` is a writer channel). `RequestPath` is dropped, and with it `queryParameters` as a second lock (v3.7.3+): the path alone carries FileBrowser's `/share/<hash>` capability links, and Loki, gated since 2026-11-22, would still hand them to every session for 30 days. `ClientAddr`, `ClientHost`, `ClientPort` and `ClientUsername` are dropped too; headers stay at the chart's `drop`. Everything else stays, including the hostname and the backend pod address. Volume measured over twelve minutes at night at about 15 MB/day. Two panels on Homelab · Traefik read it ([observability-conventions.md](../observability-conventions.md#what-the-router-logs)). |
| `experimental`, HTTP/3 | off | `http3.enabled` needs the experimental feature and a second, UDP Service on 443; the Livebox forwards no UDP/443. Nothing asks for QUIC here. |
| encoded characters | accepted at the v3.6.7+ defaults (all `true`) | The warning says the option has a default now and asks the operator to choose. Rejecting encoded `%2F`, `%25`, `%3B` and the rest would break FileBrowser and Calibre paths that carry them in file names. Every route here matches on host and, at most, a path prefix, so a decoded path cannot move a request to another service. |
| cross-namespace warning | expected | `providers.kubernetesCRD.allowCrossNamespace: true` is deliberate: every gate is referenced as `kube-system-<name>@kubernetescrd`, and without it they all vanish. Traefik warns because the option widens what a route may reference. |

After the change the startup log carries the last two warnings only, both accepted above. The
`SafeNaming` and `aliasHeadersStrategy` warnings are gone. `global.sendAnonymousUsage` also moved to
`false`: the collection endpoint is not an EU provider under the sovereign-provider rule.

**The rename sweep.** Chart 42 validates its values with `additionalProperties: false`, so a stale key
fails the render rather than being ignored. Every key in `values-v3.yaml` — the wrapper's `values.yaml`
since 2026-11-23 — exists in chart `42.4.1`'s
own `values.yaml`; `helm lint` and `helm template` both pass. The silent-ignore risk left is a key
the chart accepts but never reads, which `helm template` shows in the rendered arguments — the audit
compared those against the table above.

## Moving to chart `43.0.0` (Traefik 3.8.0), 2026-11-25

A Renovate pull request moved the wrapper's pin from `42.4.1` to `43.0.0`, and the image from
`v3.7.19` to `v3.8.0`, in one change. Two steps were checked before the Application was synced, and
one thing broke after it.

**The CRDs first.** Step 2 of *Upgrading the release* is a manual CRD apply, so the two chart tarballs
were compared file by file: all ten `traefik.io_*` files under `43.0.0`'s `crds/` directory are
byte-identical to `42.4.1`'s. The apply had nothing to do, and that is a checked fact rather than a
skipped step.

**The render.** With the wrapper rebuilt against chart 43, `helm template` produced the same documents
as `42.4.1` once the `helm.sh/chart` label was normalised: no object, argument, volume or port moved.

**The sync.** The `argocd` CLI could not reach the server — its stored refresh token had already been
claimed, and every call dies on `oauth2: "invalid_grant"`. The Application does not need the CLI: its
sync is an operation written onto the object.

```bash
REV=$(git rev-parse origin/develop)
kubectl -n argocd patch application traefik --type=merge \
  -p "{\"operation\":{\"sync\":{\"revision\":\"$REV\"}}}"
kubectl -n argocd get application traefik \
  -o jsonpath='{.status.operationState.phase} {.status.sync.status} {.status.health.status}'
```

The phase went `Succeeded` in under a minute. The Deployment became `traefik-43.0.0` on
`docker.io/traefik:v3.8.0`, the pod was replaced with zero restarts, `traefik version` answers `3.8.0`,
and the Application sits `Synced`/`Healthy` at revision `3640e9fb`.

**What 3.8 does to the log.** `dnsChallenge.delayBeforeCheck` is deprecated: the same 60 s wait is
`dnsChallenge.propagation.delayBeforeChecks` now, which the wrapper's values carry as of this change
and which renders as one argument in place of the old one. The encoded-characters advice and the
cross-namespace warning are unchanged from 3.7 and still accepted (the audit table above says why).

**What broke: the certificate store's owner.** Right after the sync, every public host answered with
the internal certificate (`CN = kub.local`) and `dashy.ashudev.com` failed the handshake outright with
`tlsv1 unrecognized name`. The chart was not at fault. `/data/acme.json` on the NAS was owned by uid
`1100` while Traefik runs as `65532`, so it could not read its ACME account and started with
`The ACME resolve is skipped from the resolvers list … permission denied`. With no resolver the
routers still match on the name, Traefik serves its default certificate for every host, and the sites
keep answering over a certificate a browser rejects.

A `chown` from inside the cluster cannot fix it: the export on `nas` is `root_squash` with
`anonuid=1100`, so root is mapped away and the call answers `Operation not permitted`. The fix is on
the NAS, against the directory it exports:

```bash
chown 65532:65532 /mnt/storage/traefik/acme.json
chmod 600 /mnt/storage/traefik/acme.json
```

After a pod restart the store loads, the resolver registers, and the certificate on the wire is
Let's Encrypt's. Check the name a browser would use, not the address:

```bash
echo | openssl s_client -connect 192.168.1.46:443 -servername ashuvidz.com 2>/dev/null \
  | openssl x509 -noout -subject -issuer -enddate
```

`issuer=… Let's Encrypt …` is the pass. `ashulab internal CA` means the store was skipped, and the
line to look for in the pod log is `The ACME resolve is skipped`.

## Rolling back

- **Values**: `helm -n kube-system get history traefik` lists the revisions. `helm rollback` to the
  revision before the change restores that revision's stored values, which is safe only if those
  values were the current file. If in doubt, upgrade again with the matching file:
  `helm upgrade traefik /tmp/traefik-25.0.3/traefik -n kube-system --reset-values -f cluster/infrastructure/traefik/values-25.0.3.yaml`
  for the 2.10 line, or the snapshot
  `cluster/infrastructure/traefik/snapshot/2026-11-21/release-values.yaml` for the values as they
  were adopted. From revision 10 the one-liner is `helm -n kube-system rollback traefik 9`. For the
  audit, `helm -n kube-system rollback traefik 11` returns to the v3 values before it.
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
| a CRD apply written as a hand-list fails and applies nothing | `traefik.containo.us` carries nine CRDs and `traefik.io` ten; `serverstransporttcps` exists only in `traefik.io`. Use the per-group globs |
| the upgrade succeeds but the image stays on the previous line | `--reset-values` was missing, so Helm merged with the release's stored values |
| a Middleware edit to `ipAllowList` disappears | the installed CRD predates the field; apply chart 27's CRDs first, or the schema prunes it and an empty allowlist lets everyone in |
| `helm uninstall traefik-crd` looks harmless and deletes every route | that release's chart carried the nine CRDs in `templates/`; uninstalling it removes them. The release is gone since 2026-11-21 — never recreate it |
| the v3 CRD apply pulls in fifteen `hub.traefik.io` resources | chart 42 ships the Hub API gateway too; they need a licence and gate nothing. Apply only `traefik.io_*` |
| a Traefik dashboard panel goes empty after v3 | v3 renamed `traefik_router_open_connections` to `traefik_open_connections` and dropped `traefik_config_reloads_failure_total`; fix the query, not the scrape |
| a `forwardAuth` route logs `maxResponseBodySize is not configured` | v3 warns once per router; the auth server is internal, but set `maxResponseBodySize` on the middleware to silence it and cap the body |
| a request carrying a `PROXY` line is treated as coming from the address it names | `proxyProtocol.insecure: true` on the entry point, with no proxy in front. It is a `lan-only` bypass from the internet; remove the block |
| an upload dies at exactly 60 seconds | v3's default `readTimeout` on the entry point; raise it for the one that carries uploads |
| `helm get values traefik` does not match the values file | it re-serialises the YAML and prints a header; compare the parsed documents, not the text |
| every `@kubernetescrd` reference stops resolving after a naming change | `safeNaming: true` renames generated objects and the reference form becomes `namespace_name@kubernetescrd`; rewrite all 85 template references first, or leave it `false` |
| a `HelmChart` deletion hangs, or runs an uninstall | `wrangler.cattle.io/on-helm-chart-remove`; strip it before anything else deletes the object |
| the release moves but the dashboard route appears twice | chart 25 renders it on `traefik.io`; the 2023 `traefik.containo.us` copy has to be deleted by hand, once |
| an object renamed to `traefik.io` leaves its twin behind | an Argo CD sync without `prune` creates the new object and keeps the old one. Delete the `containo.us` copy by hand, after the routes and gates answer on the new one |
| Syncthing stops between devices after an upgrade | the `sttcp`, `studp` and `stdiscovery` entry points are missing from the values |
| every gate stops resolving, or routes are rejected | `providers.kubernetesCRD.allowCrossNamespace` is off; every gate is referenced as `kube-system-<name>@kubernetescrd` |
| a K3s node does not come back after the config edit | a second top-level `disable:` key; K3s refuses the file. Restore `/root/config.yaml.bak-*` |
| `minecraft-minecraft-server-public … subset not found` | the minecraft Deployment is scaled to `0/0`; the error predates any upgrade |
| the dashboard answers `404` on `http://traefik.kub.local` | the chart route lives on `websecure` only; `cluster/infrastructure/traefik/dashboard/redirect.yaml` is the `web` twin that sends the browser to TLS with a `301` |
| the dashboard answers `401` on plain HTTP, no TLS | before 2026-11-21 the route was on `web` with basic auth, so the password crossed the LAN in clear; move it to `websecure` with `tls.options: internal-tls` |
| `https://traefik.kub.local` fails the certificate | the route has no `tls.options: internal-tls`, so it gets no certificate from `kube-system/local-cert` |
| `acme.json` grows with names nothing serves | certificates stay in the store after their route is deleted, and Traefik renews everything it holds; the orphans are listed in `docs/audit/traefik-2026-11-21.md` |
| every public host serves `CN = kub.local`, and one name fails the handshake with `tlsv1 unrecognized name` | `/data/acme.json` is owned by a uid other than `65532`, so Traefik cannot read its ACME account, skips the resolver and falls back to the default certificate. Fix the owner on `nas`; see *Moving to chart `43.0.0`* |
| the log says `Unable to create access logger … permission denied`, and no line reaches Loki | hostPath `/var/log/traefik` on `streaming` is root-owned `0755`, so Traefik (`65532`) cannot create the file. Run the `traefik_logs` Ansible role on that host: it sets the directory `2770` `65532:65532` with the setgid bit and installs the rotation rule |
| metrics reachable on `192.168.1.46:9101` | `ports.metrics.expose` was `true`; Prometheus scrapes the pod's 9100 through the annotation and needs no Service port |
