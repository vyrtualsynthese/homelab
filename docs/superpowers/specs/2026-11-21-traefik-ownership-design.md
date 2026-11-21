# Traefik leaves the K3s addon — design

Date: 2026-11-21 · Branch: `feature/traefik-ownership` · Status: awaiting approval

## Intent

Move the running Traefik release out of K3s's ownership and into this repository's, at the version
that serves traffic today (`chart 25.0.3`, Traefik `2.10.7`), after capturing every piece of
configuration that was made by hand. The version bump is a later session.

## Why now

- The release `traefik` in `kube-system` is still the K3s addon's. `HelmChart traefik` declares chart
  `25.0.3+up25.0.0` and its own base values; the release was moved to that chart by hand on
  2026-11-21 only because K3s's helm-controller runs no job at all (task 530).
- K3s keeps those objects in place from `/var/lib/rancher/k3s/server/manifests/traefik.yaml`, and a
  hand-placed `traefik-config.yaml` in the same directory keeps re-applying the `HelmChartConfig`.
- So the cluster serves ingress from a release whose declared values are not the ones running. If
  the controller wakes, or a K3s upgrade lands (W5 task 14 and later), K3s re-renders from its own
  values: a DaemonSet on four nodes instead of one `Deployment`, no `Recreate`, no `nodeSelector`.
  That is the two-writers-on-one-`acme.json` hazard of task 482, whose cost is a re-issue storm
  against Let's Encrypt's five-duplicates-a-week limit.
- Ownership is the precondition for the 2.11 cutover (card 11), the v3 upgrade (card 13), ArgoCD
  ownership of the release (card 466) and every K3s upgrade.

## Goals

1. The running release is installed from a chart and a values file this repository owns, at chart
   `25.0.3` / Traefik `2.10.7` — no version change.
2. Every hand-made part of today's Traefik configuration is in git, or proven covered by the backup
   system, before anything changes.
3. K3s no longer declares, applies, upgrades or can uninstall the release: the addon is disabled in
   the node config and its objects are gone.
4. The release's stored values equal the committed file, and rendering the file reproduces the live
   manifest object for object.
5. Behaviour is unchanged, with one deliberate exception: `manualRouting` goes to `false` so
   `/metrics` serves and the `ScrapeTargetDown` alert clears (card 531).

## Non-goals

- The 2.11 bump (chart `27.0.2`) — next session.
- Traefik v3 and the `traefik.containo.us` → `traefik.io` rename — W5 tasks 7 and 8.
- ArgoCD ownership of the Traefik release (card 466).
- A wrapper chart or a vendored chart: the values file plus a documented chart source is the unit,
  as W5 task 4 specifies.
- Any K3s upgrade.
- Copying `acme.json` into git.

## Anti-goals — a result that fails even if it works

- Any certificate is re-issued (the `acme.json` digest changes). That burns one of five weekly
  duplicate slots and is the one irreversible cost.
- Any route disappears from the live list, even for the length of the change.
- `helm uninstall traefik-crd` runs. That release's chart carries all nine `traefik.containo.us`
  CRDs in `templates/`, so uninstalling it deletes the CRDs and, by cascade, all 68 `IngressRoute`
  and 27 `Middleware` objects.
- The K3s finalizer runs its uninstall and removes the release.
- A node stops being `Ready` because a config key its command does not accept was added.

## The traps that shape the procedure

| Fact | Consequence |
|---|---|
| Both `HelmChart` objects carry `wrangler.cattle.io/on-helm-chart-remove`, whose handler runs `helm uninstall` | Remove the finalizers before anything can delete those objects, or a deleted CRD chart takes every route with it |
| The addon controller is alive (it bumps generations) while the helm-controller is inert | Disabling the addon *will* delete the `HelmChart` objects; nothing will uninstall them today, but the finalizer must not be left to chance |
| Addon `traefik-config` is applied from `/var/lib/rancher/k3s/server/manifests/traefik-config.yaml` | Deleting the `HelmChartConfig` object alone is not enough; the node file must go too |
| `disable` is a `k3s server` flag; `nas` runs `k3s agent` | Add it to the three servers only; `nas` gets no change |
| The operator environment has no SSH to the nodes (key refused as `vronteix`) | The four node steps are handed over as commands, with the checks after each one |
| Chart `25.0.3` is no longer served by the upstream index, which starts at 42.x | Take it once from the K3s static path (`/static/charts/traefik-25.0.3+up25.0.0.tgz`) and record the provenance; the next session uses the index for `27.0.2` |
| Chart `25.0.3` rendered with the release's own values equals the live manifest — 7 objects, zero differences | Adoption is a no-op upgrade, so it costs no pod restart |

## Approaches considered

1. **Adopt the running release (chosen).** One `helm upgrade` with the committed values file makes
   that file the release's stored values. Proven identical to what runs, so nothing restarts.
2. **Uninstall, then install under a new release name.** Immune to a stray `helm uninstall traefik`,
   but it deletes the `Service`, `Deployment`, `IngressClass` and RBAC, drops ingress for the length
   of the reinstall, and allocates a new set of node ports — for the same chart, same values, same
   pod. The immunity is bought with a real outage and buys nothing while the addon is disabled.
3. **Leave ownership with K3s and only commit a values file.** Cheapest today, and it fails the
   point: the next K3s restart or upgrade re-renders from K3s's values.

## Design

### 1. Snapshot, committed

`cluster/infrastructure/traefik/snapshot/2026-11-21/`:

| File | Content |
|---|---|
| `release-values.yaml` | `helm get values traefik` — user-supplied values |
| `release-computed-values.yaml` | `helm get values traefik --all` — every effective value |
| `release-manifest.yaml` | `helm get manifest traefik` — the 7 objects the release owns |
| `release-history.txt` | `helm history traefik` |
| `k3s-helmchart-traefik.yaml`, `k3s-helmchart-traefik-crd.yaml`, `k3s-helmchartconfig-traefik.yaml` | The live K3s objects, with their finalizers and status |
| `crds.yaml` | All 18 Traefik CRDs in both API groups (434 KB) |
| `objects.yaml` | Every live route, middleware, TLSOption, TLSStore, TCP/UDP route and TraefikService (187 KB) |
| `workload.yaml` | The live `Deployment`, `Service`, `IngressClass`, PV and PVC |
| `acme.txt` | `acme.json` sha256, size, owner and mode, and the certificate count — not the file |
| `README.md` | What each file is, and what is deliberately absent and why |

Absent on purpose: `acme.json` (TLS private keys) — it lives on the Retain NFS volume
`pv-traefik-nfs` and is already a row of the backup matrix (`/mnt/storage/traefik`), and its digest
is the check the cutover must not change. Also absent: any Secret and any kubeconfig.

### 2. Values file

`cluster/infrastructure/traefik/values.yaml`, derived from `release-values.yaml`, then pruned and
pinned:

- keep: `deployment.kind: Deployment`, `replicas: 1`, `updateStrategy: Recreate` with
  `rollingUpdate: null`, `nodeSelector: streaming`, the eight entry points with their ports and
  protocols, `certResolvers.myresolver` (`storage: /data/acme.json`, `tlsChallenge`),
  `persistence.existingClaim: pvc-traefik-nfs`, `providers.kubernetesCRD.allowCrossNamespace`,
  `providers.kubernetesIngress.publishedService`, `service.single`/`type`/
  `spec.externalTrafficPolicy: Local`, `priorityClassName`, the control-plane tolerations, the
  dashboard `IngressRoute` with both middlewares.
- change: `metrics.prometheus.manualRouting: false` (card 531).
- pin: `image.repository: rancher/mirrored-library-traefik`, `image.tag: 2.10.7`.
- drop: `deployment.podAnnotations` (`prometheus.io/port: "8082"` renders nothing; the chart itself
  annotates the pod `9100`). Each drop is proven by a render diff, not by reading the chart — which is
  how `service.ipFamilyPolicy` stayed in: the chart renders that key only when it is set, so dropping
  it would let the API server default the Service to `SingleStack`.

`cluster/infrastructure/traefik/traefik-config.yaml` is deleted: the `HelmChartConfig` it declares
belongs to K3s, and keeping the file invites re-applying it.

### 3. Ownership cutover, in order

1. Operator: etcd snapshot, and a copy of `acme.json` to a path off the cluster. Record its digest.
2. Me: remove `wrangler.cattle.io/on-helm-chart-remove` from both `HelmChart` objects. This is the
   act that makes the K3s delete path harmless: without it, deleting either object can run
   `helm uninstall`, and for the CRD chart that deletes every route.
3. Operator, three servers in the order `nuc0`, `rbp2`, `streaming`, one at a time: add
   `disable: [traefik]` to `/etc/rancher/k3s/config.yaml`, remove
   `/var/lib/rancher/k3s/server/manifests/traefik-config.yaml`, restart `k3s`, confirm
   `Ready`, and report back before the next node.
4. Me: confirm the two `HelmChart` objects are gone, no `helm-*` Job appeared, the release still
   lists, the pod still runs, and the K3s objects did not come back.
5. Me: `helm upgrade traefik <chart-25.0.3> -n kube-system -f values.yaml --reset-values`, preceded
   by a server-side dry run whose rendered manifest is diffed against the live one.
6. Verify: image `2.10.7`, one pod on `streaming`, the release's 7 objects unchanged, `acme.json`
   digest identical to step 1, 68 `traefik.containo.us` routes plus the one `traefik.io` dashboard
   route, every host answering with the same status as the pre-flight capture, and `/metrics`
   serving `traefik_*` on 9100.
7. Delete any leftover `HelmChartConfig` object.

### 4. What changes in the repository

- `cluster/infrastructure/traefik/values.yaml` — new.
- `cluster/infrastructure/traefik/traefik-config.yaml` — deleted.
- `cluster/infrastructure/traefik/snapshot/2026-11-21/` — new.
- `docs/runbooks/traefik-release.md` — rewritten around the owned release: the chart source, the
  upgrade procedure, the ownership steps and their checks, and a corrected rollback (the K3s path
  is no longer available).
- `AGENTS.md` — the paragraphs that describe Traefik as a K3s `HelmChartConfig` (section 2, section
  7 item 9) and the release procedure.
- `docs/superpowers/plans/2026-11-11-w5-platform.md` — tasks 4 and 6 rewritten to the sequence
  actually taken, so the next session's 2.11 step starts from the truth.

## Rollback

- Before the node change: nothing to undo.
- After the node change, before adoption: remove `disable: [traefik]` from the three servers, restore
  the `traefik-config.yaml` drop-in from the snapshot, restart — K3s recreates its objects and the
  addon serves again.
- After adoption: `helm upgrade` back to chart `25.0.3` with `release-values.yaml` from the snapshot.
  The certificate store is never touched by any step, so no rollback re-issues anything.
- If a node fails to start after its config edit, the `disable` key is the suspect: remove it,
  restart, and the addon returns.

## Operator steps, in one place

```bash
# 1. before anything (on a server, e.g. nuc0)
sudo k3s etcd-snapshot save --name pre-traefik-ownership
sudo ls -l /var/lib/rancher/k3s/server/db/snapshots/ | tail -3
# copy the snapshot off the node

# 2. copy the certificate store off the cluster
kubectl -n kube-system cp "$(kubectl -n kube-system get pod -l app.kubernetes.io/name=traefik \
  -o jsonpath='{.items[0].metadata.name}')":/data/acme.json ./acme-2026-11-21.json
sha256sum ./acme-2026-11-21.json    # expected f074495f… (recorded in the snapshot)

# 3. per server, in order nuc0, rbp2, streaming — wait for Ready between each
sudo cp /etc/rancher/k3s/config.yaml /root/config.yaml.bak-$(date +%F)
sudo tee -a /etc/rancher/k3s/config.yaml >/dev/null <<'EOF'
disable:
  - traefik
EOF
sudo rm -f /var/lib/rancher/k3s/server/manifests/traefik-config.yaml
sudo systemctl restart k3s
# then, from the operator's machine:
kubectl get nodes -w
kubectl -n kube-system get helmcharts,jobs
helm -n kube-system list
kubectl -n kube-system get pods -l app.kubernetes.io/name=traefik
```

Caution: `disable:` must be merged into the existing `config.yaml`, not appended as a second
top-level `disable:` key — a duplicate key makes K3s refuse the file. If the existing file already
has a `disable:` list, add `- traefik` to it.

## Board, at the end

- `#469` (hand edits vs upgrade), `#464` (committed config vs running), `#315` (dashboard host and
  folded claim), `#6` (pre-flight capture) → `Validation` with the branch, the PR and the
  verification output.
- `#531` (`/metrics` 404) → `Validation` if the fix verifies.
- `#9` (one values file per chart version) → stays in `Backlog`, rewritten: `values.yaml` is done,
  `values-2.11.yaml` and `values-v3.yaml` remain.
- `#11` (production cutover) → stays, rewritten as the 2.11 bump on top of an owned release.
- `#466` (ArgoCD ownership) → stays, now unblocked.
- `#530` (dead helm-controller) → comment: the Traefik addon no longer depends on it; the other
  addons still do.
- `#467`, `#476` → unchanged; both belong to the v3 session.

## What is left to the builder

The exact `kubectl patch` and `helm` invocations, the render-diff guard for each pruned key, and
the wording of the runbook and the AGENTS.md edits.
