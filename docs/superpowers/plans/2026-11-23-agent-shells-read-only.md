# Agent shells read-only: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every shell OpenCode spawns reaches the cluster as the read-only ServiceAccount, and the choice is recorded in the repo.

**Architecture:** Enforcement is three lines in the untracked `opencode` wrapper, which is the only place the tool shells inherit env from. Everything else is documentation: a decision record, its index row and nav entry, and one bullet in `AGENTS.md` §9.

**Tech Stack:** POSIX `sh`, `kubectl`, Markdown docs checked by `hack/check-docs.sh`.

**Spec:** `docs/superpowers/specs/2026-11-23-agent-shells-read-only-design.md`

## Global Constraints

- The wrapper line uses `:=`, never `=`: `KUBECONFIG=$HOME/.kube/config opencode` must still start an admin session.
- The line goes after `set +a` and before `REAL=`, so it is not affected by the secrets file.
- `~/.kube/config` and `~/.kube/mcp-readonly.yaml` are not edited. `kubectl/mcp-readonly-rbac.yaml` is not edited.
- No chart, Application or manifest changes.
- Record date is `2026-11-23`; decision and record are the operator's, taken on the options in the spec.
- Pages added under `docs/` carry the five-line banner (`In one line`, `Use it when`, `Verified`, `Kind`, `Sources`); `docs/superpowers/**` is exempt.

## Review Focus

- **A shell started before the wrapper change** still answers `system:admin`, because env is inherited at process start. Expected: the operator restarts OpenCode once; the report says so.
- **A runbook step using `kubectl exec`, `kubectl cp` or `kubectl port-forward`** now answers `403` to an agent. Expected: the agent hands the step to the operator rather than reaching for `--kubeconfig`.
- **An agent that ignores the default and passes `--kubeconfig ~/.kube/config`** still gets admin. Expected and documented; the guard is against accident, not intent.
- **`helm` against the cluster** now fails for writes. Expected: `helm template` and `helm lint` are unaffected because they take no cluster.
- **`argocd`** keeps working, because it authenticates over HTTPS to `argocd.kub.local` and not through a port-forward.

---

### Task 1: Record the decision

**Files:**
- Create: `docs/decisions/2026-11-23-agent-shells-read-only.md`
- Modify: `docs/decisions/README.md` (the records table, one row; and the closing paragraph if it needs the new record named — it does not)
- Modify: `mkdocs.yml` (under `Decisions:`, after `decisions/2026-11-21-sovereign-eu-providers-only.md`)

**Interfaces:**
- Consumes: nothing.
- Produces: the record every later page links to as `docs/decisions/2026-11-23-agent-shells-read-only.md`.

- [ ] **Step 1: Write the record**

Title, banner, then the four headings from `docs/decisions/README.md`. Content:

```markdown
# Agent shells reach the cluster as the read-only ServiceAccount

> **In one line.** Every shell OpenCode spawns authenticates as `kube-system/opencode-mcp`, because the
> wrapper that starts it exports the read-only kubeconfig; the operator's own terminal stays admin.
>
> **Use it when** a command answers `403` in a session, or you wonder why an agent asks you to run a
> `kubectl` write · **Verified** 2026-11-23 · **Kind:** record
>
> **Sources** `~/.local/bin/opencode`, `~/.kube/mcp-readonly.yaml`, `kubectl/mcp-readonly-rbac.yaml`

Date: 2026-11-23
Decided by: operator
Status: active
```

- **The decision**: agent shells default to `~/.kube/mcp-readonly.yaml`; the line lives in the
  `opencode` wrapper and uses `:=` so an admin session stays one env var away. `~/.kube/config` keeps
  the admin context and the operator's terminal is unchanged. Writes reach the cluster through git and
  Argo CD, as every page already says.
- **Why**: the evidence is the card — a session answers `system:admin`, `system:masters` — while the
  read-only ServiceAccount was wired into the MCP server alone. `docs/running-it/rebuild-the-docs-site.md`
  already told the operator "an agent's kubeconfig is read-only", so the docs and the shell disagreed.
  The rejected option (accept cluster-admin) leaves every hand-apply accident with nothing between it
  and the cluster; the rejected third option (a `pods/portforward` grant) is scope the card did not ask
  for.
- **What it costs if it is wrong**: `kubectl exec`, `cp` and `port-forward` answer `403` to an agent,
  so a runbook step that needs one becomes the operator's; a verification that needs one costs a
  scoped Role in `kubectl/mcp-readonly-rbac.yaml` and a card, not a return to admin. The guard stops
  accidents, not intent: `--kubeconfig ~/.kube/config` still works, and the wrapper is an untracked
  file, so a machine rebuild restores the admin default until it is re-applied.

- [ ] **Step 2: Add the index row**

Append to the table in `docs/decisions/README.md`, one row, same shape as the others:

```markdown
| [Agent shells are read-only](2026-11-23-agent-shells-read-only.md) | Every shell OpenCode spawns authenticates as `kube-system/opencode-mcp`; the operator's terminal keeps admin, and a write reaches the cluster only through git and Argo CD | 2026-11-23 |
```

- [ ] **Step 3: Add the nav entry**

In `mkdocs.yml`, under `- Decisions: decisions/README.md`:

```yaml
      - Agent shells are read-only: decisions/2026-11-23-agent-shells-read-only.md
```

- [ ] **Step 4: Verify the docs checks**

Run: `./hack/check-docs.sh --base origin/develop`
Expected: no problems for the new record; the banner, nav and link checks pass.

- [ ] **Step 5: Commit**

```bash
git add docs/decisions/2026-11-23-agent-shells-read-only.md docs/decisions/README.md mkdocs.yml docs/superpowers/specs/2026-11-23-agent-shells-read-only-design.md
git commit -m "📝 docs: record that agent shells are read-only"
```

### Task 2: Say it in AGENTS.md §9

**Files:**
- Modify: `AGENTS.md:379-391` (the cluster-access bullets)

**Interfaces:**
- Consumes: the record from Task 1, linked by path.
- Produces: nothing code reads.

- [ ] **Step 1: Add one bullet after the human-kubeconfig bullet**

```markdown
- **Shells are read-only too.** The `opencode` wrapper exports `KUBECONFIG=~/.kube/mcp-readonly.yaml`
  unless it is already set, so a tool shell, a child process and the `helm` it starts all authenticate
  as `opencode-mcp`; `kubectl exec`, `cp`, `port-forward` and every write answer `403`. An admin session
  is deliberate: `KUBECONFIG=$HOME/.kube/config opencode`. The decision is
  [`docs/decisions/2026-11-23-agent-shells-read-only.md`](docs/decisions/2026-11-23-agent-shells-read-only.md).
```

- [ ] **Step 2: Verify the link resolves and the page passes the checks**

Run: `./hack/check-docs.sh --base origin/develop`
Expected: pass (AGENTS.md is outside `docs/`, so only the cited-path check applies).

- [ ] **Step 3: Commit**

```bash
git add AGENTS.md
git commit -m "📝 docs(agents): state that tool shells are read-only"
```

### Task 3: Make the wrapper read-only

**Files:**
- Modify: `~/.local/bin/opencode` (outside every repository; not committed)

**Interfaces:**
- Consumes: `~/.kube/mcp-readonly.yaml` (context `homelab-readonly`, user `opencode-mcp`).
- Produces: `KUBECONFIG` in the environment of every process OpenCode spawns.

- [ ] **Step 1: Insert the export after the secrets are loaded**

```sh
# Agent shells read the cluster, they do not change it: the read-only
# ServiceAccount, not the admin kubeconfig. An admin session is deliberate —
# `KUBECONFIG=$HOME/.kube/config opencode`.
: "${KUBECONFIG:=$HOME/.kube/mcp-readonly.yaml}"
export KUBECONFIG
```

Placement: after `set +a`, before `REAL=`.

- [ ] **Step 2: Prove the line does what it says, without starting a session**

Run:

```bash
KUBECONFIG= sh -c ': "${KUBECONFIG:=$HOME/.kube/mcp-readonly.yaml}"; export KUBECONFIG; kubectl auth whoami'
```

Expected: `system:serviceaccount:kube-system:opencode-mcp`.

Run:

```bash
KUBECONFIG=$HOME/.kube/config sh -c ': "${KUBECONFIG:=$HOME/.kube/mcp-readonly.yaml}"; export KUBECONFIG; kubectl auth whoami'
```

Expected: `system:admin` — the override survives.

Run:

```bash
KUBECONFIG=$HOME/.kube/mcp-readonly.yaml kubectl auth can-i delete pvc -A
```

Expected: `no`.

- [ ] **Step 3: Confirm reads still work through the read-only identity**

Run: `KUBECONFIG=$HOME/.kube/mcp-readonly.yaml kubectl -n media-stack get pods`
Expected: a pod list, no `Forbidden`.

- [ ] **Step 4: Report, do not commit**

The wrapper is not tracked. Quote the added lines in the report and in the card's Validation
description, and name the restart: sessions started before the change keep admin until OpenCode is
restarted.
