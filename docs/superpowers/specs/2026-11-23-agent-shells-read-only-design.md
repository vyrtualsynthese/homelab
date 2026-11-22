# Point agent shells at the read-only kubeconfig — design

Card: #663 (Vikunja `homelab`, task, `priority/medium`).

## Intent

A shell started by an agent answers `system:admin`, because `~/.kube/config` is the operator's admin
kubeconfig and every process OpenCode spawns inherits it. The read-only ServiceAccount only covers the
MCP server, so the one path with a reviewed, read-only identity is the one an agent is least likely to
use. A mistaken `kubectl apply`, `kubectl delete pvc` or an edit to an upgrade `Plan` has nothing
between it and the cluster — and the whole branch-and-review workflow assumes git is the only way a
change lands.

The goal is a default, not a wall: an agent shell reads the cluster through
`kube-system/opencode-mcp`, and writes go through git and Argo CD as every page already says. A
deliberate `--kubeconfig ~/.kube/config` still works, and the decision record says so.

## Goals

- Every shell OpenCode spawns reaches the cluster as `system:serviceaccount:kube-system:opencode-mcp`.
- The operator's own terminal keeps the admin context untouched, and switching it does not move the
  agent's default.
- The claim `docs/running-it/rebuild-the-docs-site.md` already makes ("an agent's kubeconfig is
  read-only") becomes true without editing that page.
- The choice is recorded in `docs/decisions/`, and `AGENTS.md` §9 names both defaults.

## Non-goals

- Making it impossible. A determined agent can pass `--kubeconfig`, and `~/.kube/config` stays
  readable; the guard is against accident and prompt drift, and the record says that in those words.
- A second ServiceAccount, a scoped Role or any RBAC change. The existing read-only grant is what the
  shell now uses; widening it is a separate card if a verification step turns out to need it.
- Changing the MCP server config, which already points at `~/.kube/mcp-readonly.yaml`.
- Tracking the `opencode` wrapper in a repository. It is a local, untracked file today; that gap is
  named in the record and in the report, not fixed here.

## Constraints

- Shells do not read `~/.zshrc`; the only env they see is the OpenCode process's. The wrapper at
  `~/.local/bin/opencode` already loads the secrets for exactly this reason, so it is the one place
  that reaches every tool shell.
- `KUBECONFIG` must be set with `:=`, so the operator can still start an admin session with
  `KUBECONFIG=$HOME/.kube/config opencode`.
- The wrapper is the operator's file, outside every repository. The edit is made live and quoted in
  the report so it is reconstructible.

## Approaches considered

1. **Read-only by default through the wrapper.** (chosen) Three lines in the wrapper; the operator's
   terminal and the MCP server are untouched; the default is read-only for every tool shell, every
   child process and the `argocd`/`helm` CLIs they start. Cost: `kubectl exec`, `port-forward`, `cp`
   and every write answer `403` to an agent, and `helm` can no longer install or upgrade.
2. **Accept cluster-admin and record the risk.** Cheapest in work and the weakest in fact: every
   hand-apply-by-accident story stays open, and the docs page that says otherwise has to be rewritten
   to say the opposite of what the design wants.
3. **Read-only plus a `pods/portforward` grant, not `exec`.** Kept the verification path open but adds
   a Role and a token to review later; rejected as scope the card did not ask for. If port-forward
   turns out to be needed for a routine verification, it becomes its own card against the RBAC file.

## What the change touches

| Where | What |
|---|---|
| `~/.local/bin/opencode` | `: "${KUBECONFIG:=$HOME/.kube/mcp-readonly.yaml}"; export KUBECONFIG` after the secrets are loaded |
| `docs/decisions/2026-11-23-agent-shells-read-only.md` | the record, four headings, plus what it costs |
| `docs/decisions/README.md` | one row in the records table |
| `AGENTS.md` §9 | a bullet saying shells default to the read-only kubeconfig and how to start an admin one |
| `docs/superpowers/specs/` | this page |

## What it costs if it is wrong

- The casualty is `kubectl exec`, `kubectl port-forward`, `kubectl cp` and cluster writes: an agent
  that was following a runbook with `kubectl port-forward svc/kavita …` now gets `403` and must hand
  that step to the operator. Reading the same state through the MCP tools or the app's own API is the
  intended replacement.
- `argocd app sync` survives: the CLI reaches `argocd.kub.local` over HTTPS with grpc-web, not through
  a port-forward, so ArgoCD's own RBAC still applies to it.
- If a routine verification genuinely needs one of the lost verbs, the cost is a scoped Role in
  `kubectl/mcp-readonly-rbac.yaml` and a card — not a return to admin.

## Left to the builder

The exact wrapper lines and their position (after `set +a`, before the `exec`), the record's wording,
and the verification below.

## Verification

1. Start a new session and run `kubectl auth whoami` in its shell: the answer is
   `system:serviceaccount:kube-system:opencode-mcp`, and `kubectl auth can-i delete pvc -A` is `no`.
2. `kubectl -n media-stack get pods` still answers, and the `kubernetes` MCP server still works.
3. In a plain terminal, `kubectl auth whoami` is still `system:admin`.
4. `KUBECONFIG=$HOME/.kube/config opencode` started deliberately is admin again.
