# Agent shells reach the cluster as the read-only ServiceAccount

> **In one line.** Every shell OpenCode spawns authenticates as `kube-system/opencode-mcp` by default,
> because the wrapper that starts it exports the read-only kubeconfig; the operator's own terminal
> stays admin.
>
> **Use it when** a command answers `403` in a session, or you wonder why an agent asks you to run a
> `kubectl` write · **Verified** 2026-11-23 · **Kind:** record
>
> **Sources** `~/.local/bin/opencode`, `~/.kube/mcp-readonly.yaml`,
> `kubectl/mcp-readonly-rbac.yaml`, the session that answered `system:admin` on 2026-11-22

Date: 2026-11-23
Decided by: operator
Status: active

## The decision

Every shell an agent starts reaches the cluster as the read-only ServiceAccount
`kube-system/opencode-mcp`, not as the operator. The block that does it lives in the `opencode`
wrapper, which is the only place the tool shells inherit environment from:

```sh
# Agent shells read the cluster, they do not change it: the read-only
# ServiceAccount, not the admin kubeconfig. An admin session is deliberate —
# `KUBECONFIG=$HOME/.kube/config opencode`.
: "${KUBECONFIG:=$HOME/.kube/mcp-readonly.yaml}"
export KUBECONFIG
```

It sits after the block that loads the secrets and before the real binary is resolved, so every child
process inherits it. That is the whole edit — the wrapper is otherwise unchanged.

`:="${…}"`, not `=`, so the default holds and the exception costs one variable.
`KUBECONFIG=$HOME/.kube/config opencode` starts an admin session. `~/.kube/config` keeps the admin
context, the operator's own terminal is unchanged, and switching that context does not move the
agent's default, because the default is set at process start and not read from a shared file.

A change reaches the cluster through git and Argo CD, as `AGENTS.md` and the runbooks already say.
A scoped grant, if one is ever needed, is a Role in `kubectl/mcp-readonly-rbac.yaml` — never a return
to admin.

## Why

A session answers `system:admin` (group `system:masters`) because `~/.kube/config` carries the admin
kubeconfig and every process OpenCode spawns inherits it. The read-only ServiceAccount existed and
was wired into the MCP server alone, so the reviewed, read-only path was the one an agent was least
likely to take: a mistaken `kubectl apply`, a `kubectl delete pvc`, or an edit to an upgrade `Plan`
had nothing between it and the cluster.

The documentation already assumed the opposite of what was true.
`docs/running-it/rebuild-the-docs-site.md` told the operator "an agent's kubeconfig is read-only, so an
agent cannot run this job and will ask you". That page is correct from this date without being edited.

Options rejected:

- **Accept cluster-admin and record the risk.** Cheapest, and it leaves every hand-apply accident
  open, with the docs saying otherwise.
- **Read-only plus a `pods/portforward` grant, not `exec`.** Keeps the verification path open, but adds
  a Role to review for a card that did not ask for one. If a routine check needs it, it becomes its own
  card.

The cost accepted with the choice: `kubectl exec`, `kubectl cp`, `kubectl port-forward` and every
write answer `403` to an agent, and `helm` can no longer install or upgrade against the cluster. Reads
through `helm` fail where the data is a Secret, which `view` excludes by design: `helm list`,
`helm history` and `helm get` answer `403` because Helm keeps its release records in Secrets.
`helm template` and `helm lint` need no cluster and are unaffected. A runbook step that needs one of
those becomes the operator's.

`argocd` is unaffected: the CLI keeps its own session in `~/.config/argocd/config` and talks to
`argocd.kub.local` over HTTPS with grpc-web, so it does not read the kubeconfig for `app list` or
`app sync` and both keep working for an agent.

## What it costs if it is wrong

This is a default, not a boundary. An agent that means to bypass it passes `--kubeconfig
$HOME/.kube/config` and gets `system:admin` back — the guard stops accidents and prompt drift, not
intent, and the admin kubeconfig stays readable because the operator needs it there.

The wrapper is an untracked file at `~/.local/bin/opencode`. Rebuild the machine and the admin default
returns until that block is put back; it is quoted in full above, with its position, for that reason.

If a routine verification genuinely needs `exec` or `port-forward`, the price is a scoped Role in
`kubectl/mcp-readonly-rbac.yaml` and a card, not a return to cluster-admin.
