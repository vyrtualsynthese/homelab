# Add an application

> **In one line.** A new application is a chart under `cluster/helm/`, an Application file under
> `cluster/argocd/projects/`, a route with the LAN gate on it, a Secret already in the cluster, and a
> row in the catalogue — in that order, because each step leans on the one before.
>
> **Use it when** you are putting something the cluster does not run yet on it ·
> **Verified** 2026-11-17 · **Kind:** living page

The contract is [charts/README.md](../charts/README.md); this page is the order. Read
`media-stack/autobrr` first; `filebrowser` shows the wrapper shape.

## 1. Decide the shape

- **A, our own chart.** One container, no upstream chart: copy `autobrr`'s shape and build what the image
  needs.
- **D, a wrapper chart.** Ours pins the vendor chart at an exact `version:` and every image in
  `values.yaml` — `filebrowser`, `monitoring/grafana` — and ArgoCD builds the dependency at sync.
- **B and C are not starting points**: a bare values file, or a whole upstream chart edited in place —
  legacy, converted to D when touched.

What each shape costs when you change it is [gitops.md](../how-it-works/gitops.md).

## 2. The chart

New chart at `cluster/helm/<area>/<app>`.

- **Image pinned.** `image.tag` an explicit version, `appVersion` the same number, never `latest`.
- **Node pinned.** `nodeSelector` names one node and the config claim lands there. Never `rbp2`.
- **Recreate and a claim.** An app that keeps config gets `strategy: Recreate` and a
  `<fullname>-config` claim on `local-path`, `128Mi` unless there is a reason.
- **Configuration where it belongs.** Plain values inline in `env`, secrets through
  `secretKeyRef`, a ConfigMap only for a file the app reads.
- **Probes on the app's own endpoints**, liveness and readiness apart when a dependency can fail alone,
  with a startup budget.
- **Resources.** A memory request and limit on every container; a small CPU request, no CPU limit.
- **Hardening.** No privilege escalation, capabilities dropped, default seccomp — minus what the
  image's init needs, and no non-root user on a claim that holds data.
- **Logs at `warning`**, not the image's default; **the exporter on** if the image ships one, listener,
  port and scrape annotations together.

Then sweep the copy: `grep -rn '<copied chart>' .`, and check the claim name is derived.

```bash
cd cluster/helm/<area>/<app>
helm lint . --namespace <ns>
helm template <release> . --namespace <ns> | kubeconform -strict -summary
```

## 3. The Application

One file, `cluster/argocd/projects/<area>/<app>.yaml`, `metadata.name` matching it. It carries
`targetRevision: develop`, a `path:` that exists, the destination namespace and
`CreateNamespace=true`; the root Application finds it on its own.

Two things fail quietly: **CRLF** line endings like the rest of `cluster/argocd/`, and **no
`syncPolicy.automated`** — auto-sync is a switch in the interface, and a file that writes it there
switches automation off at the next sync.

## 4. The route and the gate

A Traefik `IngressRoute`, with its API group in a value — never a `networking.k8s.io/v1` Ingress. Every
route that is not on the public list carries `kube-system-lan-only@kubernetescrd`.

Then pick the sign-in in this order: the app's own OIDC first, as `autobrr` does; proxy auth where the
app is built for it, as `filebrowser` is; `authentik-forwardauth` for an app that only knows a local
login; `lan-only` alone only with the reason written beside the middleware list. A path leaves the gate
only when it demands a key of its own — an API client cannot follow a sign-in redirect.

## 5. The secret

Create it in the cluster before the first sync, never in git; a missing one is a pod in
`CreateContainerConfigError`. The inventory is [secrets.md](../secrets.md).

## 6. Documentation

A row in the catalogue, [applications.md](../how-it-works/applications.md): Application, namespace,
node, chart letter, one line on what it is. What the chart does beyond the contract goes in its
`README.md`.

## 7. Verify

Before the pull request: `hack/check-exposure.sh` from the repo root and the Helm commands above; the
flow is [ship-a-change.md](ship-a-change.md). After the merge:

```bash
kubectl -n argocd get application <name> -o custom-columns=\
'SYNC:.status.sync.status,HEALTH:.status.health.status,REV:.status.sync.revision'
kubectl -n <ns> get pods -o wide
```

`Synced` and `Healthy` at the merge revision, then a pod that is `Running` and stays. A `local-path`
claim binds to the node of the first pod that uses it, so when a chart replaces an app on another
node, scale the old Deployment to zero first.

## Common failures

- **`Pending`** — a claim that cannot bind, one bound to another node, a Secret:
  [fix-a-pending-pod.md](fix-a-pending-pod.md).
- **`CreateContainerConfigError`** — [the Secret or one of its keys is
  missing](fix-a-pending-pod.md#a-missing-secret).
- **`ImagePullBackOff`** — [a tag that does not exist, or a private registry with no pull
  secret](fix-a-pending-pod.md#the-image-will-not-pull).
- **404 from the route** — the `Host` does not match, or the path prefix is wrong.
- **A running pod that serves nothing** — a probe path missing the app's prefix, or a claim the process
  cannot write.
