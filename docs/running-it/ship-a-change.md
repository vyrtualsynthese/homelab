# Ship a change

> **In one line.** Find where the value lives, run the checks, branch from `develop`, open the pull
> request — then watch the Application pick the commit up and prove it with a status and a pod age.
>
> **Use it when** you are changing a chart, a value, a route or a secret and want it on the cluster ·
> **Verified** 2026-11-17 · **Kind:** living page

The commit's journey is [gitops.md](../how-it-works/gitops.md); this page is the doing.

## Before you edit

Find the value first: editing the object in the cluster cannot work, because `selfHeal` reverts it to
git.

| I want to change | Edit |
|---|---|
| an image, a setting or a memory limit | that application's `values.yaml` |
| a hostname, a route or an access gate | the chart's IngressRoute and its middleware list — every non-public route keeps `kube-system-lan-only@kubernetescrd` |
| a secret | nothing in git: create or update the Secret, then restart its pod — but never an app on a borrowed volume, where a restart loses the volume ([status.md](../status.md)); [secrets.md](../secrets.md) |

Four patterns decide where the version lives:

- **A, our own chart** — `image.tag` in its `values.yaml`; the template is ours to fix.
- **B, upstream chart plus our values** — only `values.yaml`, the chart version in the Application.
  Legacy: convert on next touch.
- **C, vendored chart** — upstream's files; change `values.yaml` unless you mean to fork.
- **D, wrapper chart** — pins the upstream `version:` in `Chart.yaml` and the image in `values.yaml`;
  both are yours to bump.

## The local checks

From the chart directory:

```bash
helm lint . --namespace <ns>
helm template <release> . --namespace <ns> | kubeconform -strict -summary
```

`lint` catches a malformed chart, `template` renders it, and `kubeconform -strict` rejects what the
API would. `kubeconform` is not installed here: if it is missing, record the check as not run and
treat `lint` and `template` as the gate, or fetch it from
`github.com/yannh/kubeconform/releases`. A chart with `dependencies:` and no `charts/` needs
`helm dependency build` first.

Then, from the repo root:

```bash
hack/check-exposure.sh          # a non-public route must keep its LAN gate
python3 hack/check_docs_links.py   # when docs change
```

The link checker exits 1 on the known `reference/` forward links until stage 3. The chart contract is
[charts/README.md](../charts/README.md#checking-a-chart-against-this-list).

## Branch, commit, pull request

`develop` is integration, `main` is production. Neither takes a direct commit: branch from `develop`
and let the pull request be the record.

- One logical change per commit, squashed before review, with an emoji prefix: `🚀` feature, `🐛` fix,
  `⬆️` upgrade, `♻️` refactor, `📝` docs.
- `git fetch origin && git rebase origin/develop`, then `git push origin <branch>`. Rebase, never
  merge.
- Open the pull request against `develop`.

The long form — worktrees, handover, review — is [`AGENTS.md`](../../AGENTS.md), §Git workflow.

## Merge and sync

The merge is the new desired state: the root Application watches `cluster/argocd` with
`directory.recurse: true`, so no one has to name a new Application.

The sync itself does not follow: auto-sync is a switch per Application, held in the cluster, never in
git. **No Application file records `syncPolicy.automated`**; the root app is the exception.

- With the switch on, it applies the merge on its own within a few minutes.
- With it off, it stays `OutOfSync` until someone presses **Sync**, or switches automation on, in the
  interface.
- `cluster/infrastructure/` is outside GitOps: a merge changes nothing; it is applied by hand.

## Verify

Read the Application, then its pods:

```bash
kubectl -n argocd get application <name> -o custom-columns=\
'SYNC:.status.sync.status,HEALTH:.status.health.status,REV:.status.sync.revision'

kubectl -n <ns> get pods -o wide
kubectl -n <ns> logs deploy/<name> --tail=50
```

`Synced` and `Healthy` with `REV` at the merge commit is the proof. A pod older than that commit
means no rollout — but only when the change altered the pod template; a route, hostname or gate
change rolls nothing, so open the URL and read the IngressRoute.
[Homelab · Applications](https://grafana.kub.local/d/homelab-applications) shows the app's row over
time — replicas ready, restarts, memory — and
[Homelab · Health](https://grafana.kub.local/d/homelab-health) whether anything fired.

## If the sync stops

- `OutOfSync` with no error and no new revision: the switch is off. Re-enable it in the interface.
- `Sync failed` with a diff: the commit is wrong. Fix it in git; a `kubectl patch` is reverted.
- `Unknown` or `Missing`: the chart path or the values file the Application names does not exist.
  [status.md](../status.md) keeps the ones this repository knows about.

The trap is `syncPolicy.automated` in an Application file. It reads like the honest fix and is the
opposite: the next root-app sync rewrites the Application from git and automation turns off —
quietly, because it stays `Synced`. Remove the field, merge, then set the switch in the interface.
