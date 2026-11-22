# Rebuilding Argo CD

> **In one line.** Argo CD is an Application in this repository since 2026-11-19, so a rebuild means
> rendering its chart from git and applying it by hand — the controller cannot restore itself.
>
> **Use it when** the controller is down, its Application is gone, or a sync cannot repair it ·
> **Status:** pending — the rebuild has not been run · **Verified:** never

## What owns what

| Layer | Where |
|---|---|
| root Application | `cluster/argocd/argocd-root.yaml`, applied by hand once; syncs `cluster/argocd/` recursively |
| Argo CD's Application | `cluster/argocd/projects/platform/argocd.yaml` |
| the chart | `cluster/helm/argocd/`, a wrapper on `argo-cd` `10.18.1` |
| namespace | `argocd` |

Helm is **not** an owner. What the hand install left behind, and how it is cleared, is below.

## Owned by the Application since 2026-11-19

The namespace was installed by hand with Helm 3 on 2026-08-28 and has belonged to the `argocd`
Application since 2026-11-19. The live Application carries automated sync with `prune` and
`selfHeal`; git does not record that (AGENTS.md §7.1), so read the object rather than the file before
assuming what a sync will do.

One thing survived the adoption: `helm -n argocd list` reports `argocd`, chart `argo-cd-10.4.1`,
**revision 5, status `superseded`**, and `helm history argocd` ends at revision 5. There is no
revision 6 and no `deployed` revision at all, and nothing has been rendered from it since the
adoption: `argocd-server` runs `quay.io/argoproj/argocd:v3.6.1` and carries
`helm.sh/chart: argo-cd-10.18.1`, the dependency pinned in `cluster/helm/argocd/Chart.yaml`, so the
live objects come from the wrapper and not from that record.

That record is deleted rather than kept. A `superseded` revision with no `deployed` successor reads
as a broken release, and the obvious repair — `helm upgrade` — fights `selfHeal` and is reverted one
object at a time, with no release of its own to show for it:

```bash
kubectl -n argocd delete secret -l owner=helm,name=argocd
```

**Never `helm uninstall argocd`.** It removes the Deployments, the StatefulSet and the ConfigMaps
along with the record, which takes down the controller that would otherwise have restored them; a
rebuild by hand is then the only way back.

Verification: `helm -n argocd list` prints the header row and nothing else (Helm 4; Helm 3 printed
`no deployed releases`), and every pod still reads `1/1`.

```bash
helm -n argocd list; kubectl -n argocd get pods
```

If `argocd` reappears in the list later, something has created a release record again, and a
`helm upgrade` through it would fight Argo CD for the same objects.

**Cleared on 2026-11-20.** The record was still there when this page was written; the command above
was run by hand that day, because clearing it needs write access the agent ServiceAccount does not
have. Since then `helm -n argocd list` prints the header row alone, `helm history argocd` answers
`release: not found`, and all seven pods stayed `1/1`.

## Rebuild from nothing

Preconditions: this repository checked out at the commit you want, Helm 3 or later, and a kubeconfig
with cluster-admin. Nothing here depends on Argo CD being up — that is the point.

```bash
helm repo add argo https://argoproj.github.io/argo-helm   # once per machine
helm dependency build cluster/helm/argocd
helm template argocd cluster/helm/argocd -n argocd | kubectl apply -f -
kubectl -n argocd rollout status deploy/argocd-server --timeout=180s
```

The chart carries its CRDs in `templates/`, so the apply creates them too; no `--include-crds` and no
separate `kubectl apply -k` are needed. Expected: `argocd-server`, `argocd-repo-server`,
`argocd-redis`, `argocd-dex-server`, `argocd-notifications-controller` and
`argocd-applicationset-controller` as Deployments, `argocd-application-controller` as a StatefulSet,
all `1/1` once the rollout finishes. Sign in at `https://argocd.kub.local`.

### The values file only works through the wrapper

`values.yaml` is keyed under `argo-cd:`, the dependency's name, so only the wrapper chart reads it.
Handing the same file to the upstream chart is not an error Helm reports: the render succeeds and
silently keeps every default, which is seven log levels at `info` and no `server.insecure`. Compare
the two before trusting a rebuild:

```bash
helm template argocd argo/argo-cd --version 10.18.1 -f cluster/helm/argocd/values.yaml | grep 'log.level'
helm template argocd cluster/helm/argocd -n argocd | grep 'log.level'
```

The first prints `info` seven times; the second prints `warn` for `controller`, `reposerver`,
`notificationscontroller` and `server`, and `info` for the other three. The rebuild command above
already uses the wrapper, and that is why it must.

On a cluster where the root Application is also gone, recreate it once — nothing else re-creates it,
and without it no application comes back:

```bash
kubectl apply -f cluster/argocd/argocd-root.yaml
```

Then the Applications the root app syncs reappear on their own.

Why by hand: the thing that would restore Argo CD is Argo CD.

## Wrapper charts and their dependency build

Pattern D (`AGENTS.md` §3) keeps `charts/` and `Chart.lock` out of git, so a wrapper chart's
dependency is downloaded at render time instead of committed. That costs one failed render per
wrapper chart per revision, and it is expected:

1. On a new revision Argo CD checks the repository out again and clears untracked files, which
   removes `charts/` — the built dependency — and also `.argocd-helm-dep-up`, the marker that
   records that a build has already run for that chart.
2. The first `helm template` therefore stops with
   `found in Chart.yaml, but missing in charts/ directory: <chart>`.
3. Argo CD recognises that message, runs `helm dependency build`, and renders a second time. The
   second render succeeds, and nothing is logged for it because repo-server logs at `warn`.

Measured on 2026-11-22 over 24 hours: 789 of those `missing in charts/` lines, **zero**
`helm dependency build` failures, and every Application `Synced` at the tip of `develop` — the one
multi-source Application (`local-path`) aside, which reports no single revision.

A build that does fail is the case that needs a person. It appears in the same log as
`error building helm chart dependencies: …`, or as a failing `helm dependency build` command:

```bash
kubectl -n argocd logs deploy/argocd-repo-server --since=30m | grep -i 'helm dependency'
```

Argo CD retries on the next reconciliation, so a transient failure clears itself; a repeating one
means the upstream chart repository is unreachable or the pinned version has moved. Committing
`Chart.lock` without `charts/` fixes none of this — `helm template` needs the dependency's files, so
the build still runs. Only committing `charts/` would remove it, which is what pattern D avoids.

`status.operationState.startedAt` is not a liveness signal. A sync operation runs only when the
rendered output differs from the live objects, so an Application can be `Synced` at the newest
revision with its last operation days old. The fields that answer "is it current" are these:

```bash
kubectl -n argocd get application loki \
  -o jsonpath='{.status.sync.status} {.status.sync.revision} {.status.reconciledAt}{"\n"}'
```

A render that is genuinely failing reads `Unknown` in the first field and carries a
`ComparisonError` condition. `Synced` at the tip means current, whatever the operation history says.

## The one value that comes from outside git

A fresh install generates `argocd-secret` with random keys, but the Dex client secret is not in git
and not generated by the chart. It must equal `ARGOCD_CLIENT_SECRET` in the `authentik-sso` Secret;
the authentik provider's blueprint in `cluster/helm/authentik/files/app-argocd.yaml` is the other
side of the pair.

```bash
A='<the argocd value from authentik-sso>'
kubectl -n argocd patch secret argocd-secret --type merge \
  -p "$(jq -n --arg a "$A" '{stringData:{"dex.authentik.clientSecret":$a}}')'
kubectl -n argocd rollout restart deploy/argocd-dex-server
```

The failure when they differ: the browser completes the authentik login, then Dex fails the token
exchange with `invalid_client`. A wrong `url` in `configs.cm` fails earlier, with
`Invalid redirect URL` — see [`authentik-apps.md`](authentik-apps.md) §5.

## What deleting the Application file does

It removes the Application record and nothing else. No `resources-finalizer.argocd.argoproj.io` is
set on it, on purpose: `argocd-root` prunes from git, so a rename or a move of the file would
otherwise delete the control plane mid-sync. The controller keeps running, untracked, and reverting
the deletion restores the Application with no drift.

## When the alert says the manifests cannot be generated

`ArgoAppRenderFailing` fires on an Application carrying a `ComparisonError`: Argo CD could not
render it, so its sync status reads `Unknown` (see the field check above). It is not the routine
`missing in charts/` line — that one is built and retried inside a single request and leaves no
condition behind.

```bash
kubectl -n argocd get application <name> -o jsonpath='{.status.conditions}' | jq
kubectl -n argocd logs deploy/argocd-repo-server --since=30m | grep -i '<name>'
```

The causes seen here: a `path` that does not exist at that `targetRevision`; a
chart that fails `helm template` (the message quotes the template error); a repository the
repo-server cannot read (credentials, or a `targetRevision` branch that was never pushed); and a
dependency build that keeps failing (see the section above). The rule's `for: 10m` means a retry
that succeeds clears it without a message.

After an Argo CD upgrade, confirm the metric the rule reads still exists:
`count(argocd_app_condition)` must answer (0 is correct on a healthy cluster), and
`controller.metrics.application.conditions` must still be a key the release reads. A renamed metric
turns the rule silent, and `noDataState: OK` renders silence as health.

## Break-glass

There is no local account: `admin.enabled: "false"`, and authentik is the only way in. If SSO is
broken, re-enable the built-in account, roll the server, and use the password in `argocd-secret`:

```bash
kubectl -n argocd patch configmap argocd-cm --type merge -p '{"data":{"admin.enabled":"true"}}'
kubectl -n argocd rollout restart deploy/argocd-server
kubectl -n argocd get secret argocd-secret -o jsonpath='{.data.admin\.password}' | base64 -d
```

Undo it the same way with `"false"` once SSO works, because a local password login is the one path
the identity provider cannot see.

## Status

Pending. The first real rebuild fills in what the commands printed, and that is what moves this page
from pending to proved.
