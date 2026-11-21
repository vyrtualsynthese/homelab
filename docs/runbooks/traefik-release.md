# Moving the Traefik release

Date: 2026-11-21 · **Performed** 2026-11-21, chart `20.3.1` → `25.0.3` (Traefik `2.9.4` →
`2.10.7`) · **Kind:** living runbook

> **In one line.** How to move the cluster's Traefik release when K3s's own controller does not.
>
> **Use it when** Traefik has to move to a newer chart, or the release has to be inspected or
> rolled back · **Verified** 2026-11-21 with the numbers below.

## The shape of the problem

K3s ships Traefik as two `HelmChart` objects in `kube-system` — `traefik-crd` (the custom resource
definitions) and `traefik` (the router itself) — plus a `HelmChartConfig` in this repo that carries
the cluster-specific values. Normally K3s's helm-controller runs a Job to install or upgrade each of
them. **In this cluster it runs nothing** (task 530, `#398`), so the two releases sit at their
install day until someone moves them with `helm` directly. That is what this runbook does.

Two consequences to know before starting:

- `cluster/infrastructure/traefik/traefik-config.yaml` is not applied by Argo CD and, with the
  controller idle, not by K3s either. Applying the file changes a Kubernetes object and nothing more;
  the values only reach the pods through the `helm upgrade` below.
- The chart validates the CRDs before it renders. Chart `25.0.3` refuses to install unless
  `traefik.io/v1alpha1/ServersTransportTCP` exists, and the CRDs here were 2.9-era objects applied by
  hand in 2024 without the ownership labels Helm looks for. The CRD step below applies the CRDs from
  the same chart with `kubectl`, which is also what made the router chart render at all.

## Before

Record what is running, so the after can be compared rather than assumed:

```bash
helm -n kube-system history traefik | tail -3
kubectl -n kube-system get ds traefik -o jsonpath='{.spec.template.spec.containers[0].image}{"\n"}'
POD=$(kubectl -n kube-system get pods -l app.kubernetes.io/name=traefik -o jsonpath='{.items[0].metadata.name}')
kubectl -n kube-system exec "$POD" -- sha256sum /data/acme.json
kubectl -n kube-system exec "$POD" -- sh -c 'grep -o "\"domain\"" /data/acme.json | wc -l'
kubectl get crd -o yaml > /tmp/crds-before.yaml
```

On 2026-11-21 that was release `v5` (2023-06-22), chart `20.3.1+up20.3.0`, image
`rancher/mirrored-library-traefik:2.9.4`, and `/data/acme.json` at
`dfe066c0194fe735cea212fc21911d3db2dacbf8828ff1c99b2f58ecefdcbd3e`, 417 415 bytes, 33 domains.
**That hash is the check that the certificate store survived the move.** It must not change.

## Move the CRDs first

```bash
kubectl get --raw "/static/charts/traefik-crd-25.0.3+up25.0.0.tgz" > /tmp/traefik-crd.tgz
tar xzf /tmp/traefik-crd.tgz -C /tmp
helm template traefik-crd /tmp/traefik-crd -n kube-system > /tmp/crds.yaml
kubectl apply --dry-run=server -f /tmp/crds.yaml     # expect "configured" on 18 and "created" on 1
kubectl apply -f /tmp/crds.yaml
```

The chart keeps its CRDs in `templates/`, not in a `crds/` directory, which is why
`helm upgrade traefik-crd` would work too — but only if the existing objects carried Helm's
ownership annotations, and they do not. `kubectl apply` is the honest path, and it keeps every
stored `IngressRoute` and `Middleware`: a CRD update changes the schema, never the objects.

The one new definition on 2026-11-21 was `serverstransporttcps.traefik.io`. Everything else is an
update, and `ipWhiteList` — which `kube-system/lan-only` uses — is still in the schema at 2.10.

## Then the router

Take the values from the two objects K3s and this repo own, so the release is exactly what git and
the HelmChart declare:

```bash
kubectl -n kube-system get helmchart traefik -o jsonpath='{.spec.valuesContent}' > /tmp/k3s-values.yaml
kubectl -n kube-system get helmchartconfig traefik -o jsonpath='{.spec.valuesContent}' > /tmp/cluster-values.yaml

helm upgrade traefik /tmp/traefik -n kube-system \
  -f /tmp/k3s-values.yaml -f /tmp/cluster-values.yaml \
  --set global.systemDefaultRegistry="" \
  --dry-run=server
```

`/tmp/traefik` is the chart directory unpacked from
`/static/charts/traefik-25.0.3+up25.0.0.tgz` (the same fetch as above, with `traefik` in place of
`traefik-crd`). When the dry run ends with `Traefik Proxy 2.10.7 has been deployed successfully`,
run the same command without `--dry-run=server` and with `--wait --timeout 5m`.

## After

```bash
helm -n kube-system history traefik | tail -2
kubectl -n kube-system rollout status ds/traefik --timeout=2m
kubectl -n kube-system get ds traefik -o jsonpath='{.spec.template.spec.containers[0].image}{"\n"}'
kubectl -n kube-system exec "$POD" -- sha256sum /data/acme.json     # must equal the before hash
```

Then prove the door still opens. The certificate each name serves comes out of `acme.json`, so a
name that answers with a certificate issued by the internal CA means the store was not loaded:

```bash
for h in plex.kub.home.ashudev.com ashudev.com ashuvidz.com dashy.ashudev.com; do
  curl -s -o /dev/null -w "$h %{http_code}\n" --resolve $h:443:192.168.1.46 https://$h/
  echo | openssl s_client -connect 192.168.1.46:443 -servername $h 2>/dev/null |
    openssl x509 -noout -issuer -subject
done
```

Expected on 2026-11-21: `plex` `401` (Plex asking for a sign-in), `ashudev.com` and `ashuvidz.com`
`200`, `dashy` `302` to authentik, and every issuer `Let's Encrypt`. A LAN gate answers too — the
dashboard on `http://traefik.kub.local/dashboard/` returns `401` from its basic-auth middleware —
and Syncthing's port is still open: `timeout 5 bash -c "</dev/tcp/192.168.1.46/22000"`.

Finally check the routers Traefik built from what is now installed:

```bash
kubectl -n kube-system logs -l app.kubernetes.io/name=traefik --since=10m |
  grep -iE "multiple|duplicate|conflict"
```

Chart `25.0.3` renders its dashboard route as `traefik.io/v1alpha1`. Helm creates the new object and
leaves the `traefik.containo.us` one behind, because the two differ by API group, so delete the old
copy once the new one exists:

```bash
kubectl -n kube-system get ingressroutes.traefik.io -A
kubectl -n kube-system delete ingressroute.traefik.containo.us traefik-dashboard
```

Two copies of one rule on one host produce a duplicate-router error and no visible breakage, which
is exactly the kind of thing that waits months to confuse someone.

## Rolling back

`helm rollback traefik 5` is **not** the rollback here. Release `v5` carries the values of 2023 —
claim `traefik` rather than `pvc-traefik-nfs`, no Syncthing entry points — and the DaemonSet has been
hand-edited since. Rolling back to it would bring up pods on a claim that does not exist.

Roll back by upgrading again with the previous chart and today's values:

```bash
helm upgrade traefik /tmp/traefik-20.3.1 -n kube-system \
  -f /tmp/k3s-values.yaml -f /tmp/cluster-values.yaml --set global.systemDefaultRegistry="" --wait
```

The CRDs stay at 2.10; a 2.9 router accepts them. If a CRD update itself has to be undone, apply
`/tmp/crds-before.yaml`. No certificate is re-issued by any of this as long as the claim stays
`pvc-traefik-nfs` and the file keeps its permissions (`65532:65532`, mode `600`).

## Traps met on the way

| Symptom | Cause |
|---|---|
| `Required CRDs are missing. Please install the corresponding CRD chart` | the CRDs are older than the chart and `serversTransportTCP` is absent; do the CRD step first |
| `kubectl apply` of the `HelmChartConfig` changes nothing and no Job appears | the helm-controller is not running (task 530); moving the release by hand is the way |
| the release moves but the dashboard route appears twice | the API group change above; delete the `traefik.containo.us` copy |
| `minecraft-minecraft-server-public … subset not found` in the log | the minecraft Deployment is scaled to `0/0`; the error is older than the upgrade and disappears when it is started |
