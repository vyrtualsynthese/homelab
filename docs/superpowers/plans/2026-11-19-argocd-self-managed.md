# Argo CD under GitOps — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put the cluster's Argo CD under the same GitOps it runs for everything else: a wrapper chart on `argo-cd` 10.4.1, an Application beside the others, the hand-run release retired, and the recovery procedure written down.

**Architecture:** `cluster/helm/argocd/` becomes a wrapper chart (pattern D) whose only dependency is `argo-cd`, pinned to `10.4.1`; the existing values move under the `argo-cd:` key unchanged, so the render matches the chart output of the previous file byte for byte. The first sync is diffed against the cluster before it runs, because nothing in git proves the last hand run applied that file. `cluster/argocd/projects/platform/argocd.yaml` then points at it. Adoption — one UI sync, the Helm release secret's deletion, and the auto-sync toggle — is the operator's, not the agent's.

**Tech Stack:** Helm 4, Argo CD 10.4.1 (app `v3.5.2`), Traefik 2 CRDs, MkDocs nav, Vikunja REST v2.

**Spec:** `docs/superpowers/specs/2026-11-19-argocd-self-managed-design.md`

## Global Constraints

- CRLF on every file under `cluster/argocd/**`; LF everywhere else. New chart files are LF.
- Never write `syncPolicy.automated` into an Application file (AGENTS.md §7.1).
- Values for the dependency live under `argo-cd:` — a top-level key renders the chart's defaults.
- No hand `helm upgrade` of the `argocd` release at any point, including during this work.
- No secret value in git. `argocd-secret` is read through the operator only.
- Docs pages: blockquotes, never `!!!`; every page added to `mkdocs.yml`'s nav in the same edit.
- Branch `feature/argocd-self-managed`, cut from `develop`. Commit prefixes `🚀`, `📝`, `🔧`.

## Review Focus

- **A values key left at the top level**, by a bad indent or a new key added beside `argo-cd:`: renders the chart's defaults, which re-enables `admin`, drops Dex and removes both Traefik routes. Pinned by Task 1's render diff against the pre-change output, which must be empty.
- **`extraObjects` losing the domain expression once nested**, so the routes render with an empty host: the app becomes unreachable or, worse, the routes vanish. Pinned in Task 1 by asserting two `Host(\`argocd.kub.local\`)` matches and `kube-system-lan-only@kubernetescrd` in the rendered routes.
- **The Application pointing at a path that does not exist**: Argo CD shows `Synced`/`Healthy` while deploying nothing (the existing defect in `nfs-qbittorrent-config`). Pinned in Task 2 by resolving `path:` to a directory holding a `Chart.yaml`.
- **CRLF lost on the Application file**: invisible in the editor, shows up as a whole-file diff. Pinned in Task 2 by a byte check for `\r\n`.
- **A document still telling a human to run `helm -n argocd upgrade … -f cluster/helm/argocd/values.yaml`**: with nested values that command renders defaults against the live cluster. Pinned in Task 3 by a grep over `docs/`.

---

### Task 1: The wrapper chart renders what runs

**Files:**
- Create: `cluster/helm/argocd/Chart.yaml`
- Modify: `cluster/helm/argocd/values.yaml`
- Delete: `cluster/helm/argocd/README.MD`
- Modify: `.gitignore`

**Interfaces:**
- Produces: a chart at `cluster/helm/argocd` whose `helm template` output equals the upstream chart's output for the same release name and namespace. Task 2 points an Application at that path.

- [ ] **Step 1: Move the card to `Doing`**

```bash
set -a; . ~/.config/secrets/homelab.env; set +a
curl -s -X PUT -H "Authorization: Bearer $VIKUNJA_API_TOKEN" -H "Content-Type: application/json" \
  -d '{"task_id": 344}' "https://vikunja.kub.local/api/v2/projects/2/views/13/buckets/11/tasks"
```
Expected: `200`. Read back through the view: card 344 sits in `Doing` (bucket 11).

- [ ] **Step 2: Capture the baseline render, before anything changes**

```bash
helm template argocd argo/argo-cd --version 10.4.1 \
  -f cluster/helm/argocd/values.yaml -n argocd | grep -v '^# Source:' > /tmp/opencode/argocd-before.yaml
wc -l /tmp/opencode/argocd-before.yaml
```
Expected: a few thousand lines. This file is the proof for Step 8.

- [ ] **Step 3: Create `cluster/helm/argocd/Chart.yaml`**

```yaml
apiVersion: v2
name: argocd
description: Argo CD - the cluster's GitOps controller
type: application
version: 0.1.0
appVersion: "3.5.2"
dependencies:
  - name: argo-cd
    version: 10.4.1
    repository: https://argoproj.github.io/argo-helm
```

- [ ] **Step 4: Nest `cluster/helm/argocd/values.yaml` under `argo-cd:`**

- Indent every line of the file by two spaces and add `argo-cd:` as the new line 1. The four
  top-level keys are `global`, `configs`, `server`, `extraObjects`; comments, blank lines and the
  route bodies move with them, otherwise untouched.
- Under `global.image`, delete both the `# -- Overrides the global Argo CD image tag whose default
  is the chart appVersion` comment and the `tag: "v3.5.2"` line that follows it, and put this
  comment in their place:

```yaml
      # -- The image tag is the chart's appVersion. Argo CD upgrades itself with
      # the dependency in Chart.yaml; a tag here would be a second version lever
      # that can move apart from the chart (card 344, PR #966).
```

- Add the two pins the render shows the argocd image does not cover, at the `argo-cd:` level:

```yaml
  dex:
    image:
      repository: ghcr.io/dexidp/dex
      tag: v2.45.1
  redis:
    image:
      repository: ecr-public.aws.com/docker/library/redis
      tag: 8.6.4-alpine
```

- [ ] **Step 5: Delete the copied upstream README**

```bash
git rm cluster/helm/argocd/README.MD
```
It is `argoproj/argo-helm`'s README, unedited, and describes nothing about this install.

- [ ] **Step 6: Ignore the built dependency**

Append to `.gitignore`, after the discourse block:

```
# Built by ArgoCD at sync time
cluster/helm/argocd/charts/
cluster/helm/argocd/Chart.lock
```

- [ ] **Step 7: Build the dependency and render**

```bash
helm repo list | grep -q '^argo' || helm repo add argo https://argoproj.github.io/argo-helm
helm dependency build cluster/helm/argocd
helm template argocd cluster/helm/argocd -n argocd | grep -v '^# Source:' > /tmp/opencode/argocd-after.yaml
```

- [ ] **Step 8: Prove the render is unchanged**

```bash
diff /tmp/opencode/argocd-before.yaml /tmp/opencode/argocd-after.yaml && echo IDENTICAL
```
Expected: `IDENTICAL`, no diff lines. Any difference is a values decision to resolve here, before the
Application exists — not later, against the cluster.

- [ ] **Step 9: Prove the images and the routes survived the nesting**

```bash
grep -E '^\s+image:' /tmp/opencode/argocd-after.yaml | sort -u
grep -c 'Host(`argocd.kub.local`)' /tmp/opencode/argocd-after.yaml
grep -c 'kube-system-lan-only@kubernetescrd' /tmp/opencode/argocd-after.yaml
```
Expected: exactly three image lines (`argocd:v3.5.2`, `dex:v2.45.1`, `redis:8.6.4-alpine`), and both
counts at least 2.

- [ ] **Step 10: Commit**

```bash
git add cluster/helm/argocd .gitignore
git commit -m "🚀 feat(argocd): make the chart a wrapper on argo-cd 10.4.1"
```

---

### Task 2: The Application

**Files:**
- Create: `cluster/argocd/projects/platform/argocd.yaml`

**Interfaces:**
- Consumes: the chart path created in Task 1.
- Produces: an Argo CD Application named `argocd` in namespace `argocd`, which the operator syncs in Task 5.

- [ ] **Step 1: Write the Application with CRLF endings**

```bash
mkdir -p cluster/argocd/projects/platform
printf '%s\r\n' \
'apiVersion: argoproj.io/v1alpha1' \
'kind: Application' \
'metadata:' \
'  name: argocd' \
'  namespace: argocd' \
'spec:' \
'  project: default' \
'  destination:' \
'    server: "https://kubernetes.default.svc"' \
'    namespace: argocd' \
'  syncPolicy:' \
'    syncOptions:' \
'      - CreateNamespace=true' \
'  source:' \
'    repoURL: "https://github.com/vyrtualsynthese/homelab.git"' \
'    targetRevision: develop' \
'    path: cluster/helm/argocd' \
  > cluster/argocd/projects/platform/argocd.yaml
```

No `resources-finalizer.argocd.argoproj.io` and no `syncPolicy.automated`, on purpose.

- [ ] **Step 2: Verify endings, parse and target**

```bash
python3 - <<'PY'
import yaml, pathlib
p = pathlib.Path("cluster/argocd/projects/platform/argocd.yaml")
raw = p.read_bytes()
assert raw.count(b"\r\n") >= 17 and b"\n" not in raw.replace(b"\r\n", b""), "not CRLF"
d = yaml.safe_load(raw)
spec = d["spec"]
assert d["metadata"]["name"] == "argocd" and spec["destination"]["namespace"] == "argocd"
assert "automated" not in spec.get("syncPolicy", {})
assert pathlib.Path(spec["source"]["path"], "Chart.yaml").is_file()
print("ok")
PY
```
Expected: `ok`.

- [ ] **Step 3: Run the repo's reference checkers**

```bash
while read -r p; do [ -d "$p" ] || echo "BROKEN PATH $p"; done < <(
  grep -rh '^    path:' cluster/argocd/projects | sed 's/^    path: *//' | tr -d "\"' \r")
```
Expected: no output.

- [ ] **Step 4: Commit**

```bash
git add cluster/argocd/projects/platform/argocd.yaml
git commit -m "🚀 feat(argocd): add the Application for the controller"
```

---

### Task 3: The runbook and the stale SSO section

**Files:**
- Create: `docs/runbooks/argocd.md`
- Modify: `docs/runbooks/README.md` (living procedures table)
- Modify: `docs/runbooks/authentik-apps.md` §5
- Modify: `mkdocs.yml` (nav, after the `authentik-apps.md` entry)

**Interfaces:**
- Produces: the rebuild procedure that Task 4's PR body and the card comment point at.

- [ ] **Step 1: Write `docs/runbooks/argocd.md`**

Sections, in this order; state it is `pending — the rebuild has not been run` in a blockquote under
the title, because a runbook nobody has performed is a guess with a title:

- **What owns what.** root Application (`cluster/argocd/argocd-root.yaml`) → this Application
  (`cluster/argocd/projects/platform/argocd.yaml`) → chart `cluster/helm/argocd` → namespace
  `argocd`. Helm is not an owner: the release record was deleted on adoption.
- **Rebuild from nothing**, the case where the controller is down and cannot sync itself:
  `helm dependency build cluster/helm/argocd`, `helm template argocd cluster/helm/argocd -n argocd`,
  apply the output with `kubectl apply -f -` (CRDs are templates in this chart, so nothing extra),
  then `kubectl -n argocd rollout status deploy/argocd-server --timeout=180s`, then sync the root
  Application. Say why by hand: the thing that restores Argo CD is Argo CD.
- **The one value that comes from outside git.** `argocd-secret`'s `dex.authentik.clientSecret` must
  equal `ARGOCD_CLIENT_SECRET` in the `authentik-sso` secret; carry over the patch command from
  `authentik-apps.md` verbatim, and say what `invalid_client` means.
- **What a deleted Application file does.** Removes the record only; the controller keeps running,
  untracked, and a revert restores the Application. State that no finalizer is set, and why.
- **Break-glass admin.** `admin.enabled: "false"`, so patch `argocd-cm` to `"true"`, roll the server,
  sign in with the password in `argocd-secret`; the same text as the comment in `values.yaml`.
- **Verified output** placeholder: the operator fills the first real rebuild's result, which is what
  moves the status from `pending`.

- [ ] **Step 2: List the page in `docs/runbooks/README.md`**

Add a row to **Living procedures**, after `internal-ca.md`:

```
| [`argocd.md`](argocd.md) | pending — the rebuild has not been run | the controller is down, or Argo CD must be rebuilt from nothing since it became self-managed on 2026-11-19. |
```

Also update the intro counts in the same edit: "Five are procedures you can run today" becomes "Six
are procedures" — one of the six is the pending rebuild above, so "you can run today" stops being
true.

- [ ] **Step 3: Add the nav entry in the same edit as the page**

In `mkdocs.yml`, under the Runbooks list, after `- Wire an application to authentik: runbooks/authentik-apps.md`:

```yaml
      - The GitOps controller: runbooks/argocd.md
```

- [ ] **Step 4: Rewrite `docs/runbooks/authentik-apps.md` §5**

Replace "It is a hand-run Helm release, not an Application in this repo." with a sentence saying it
is an Application since 2026-11-19, owned by `cluster/argocd/projects/platform/argocd.yaml` and the
wrapper chart at `cluster/helm/argocd`, and pointing at [`argocd.md`](argocd.md). Replace the
`Apply the release:` block — the `helm -n argocd upgrade …` pair and its `rollout status` — with the
sentence that a values change reaches the cluster on the next sync, and that a rebuild is in
`argocd.md`. Keep the scheme paragraph, the routes paragraph, the secret patch and the break-glass
paragraph; in the "Order matters on the first cut-over" sentence, "then apply the release" becomes
"then let the `argocd` Application sync".

- [ ] **Step 5: Verify the docs**

```bash
python3 hack/check_docs_links.py
grep -rn "helm -n argocd upgrade" docs/ || echo "no hand upgrade left"
grep -n "runbooks/argocd.md" mkdocs.yml
```
Expected: `0 broken relative links`; `no hand upgrade left`; one nav line.
`docs/superpowers/plans/2026-11-14-sso-access-model.md` is exempt — it is a historical plan, and the
checker skips `docs/superpowers/`.

- [ ] **Step 6: Commit**

```bash
git add docs/runbooks/argocd.md docs/runbooks/README.md docs/runbooks/authentik-apps.md mkdocs.yml
git commit -m "📝 docs(argocd): write the recovery runbook and retire the hand-upgrade step"
```

---

### Task 4: Full verification, PR, and the adoption handover

**Files:** none changed; this is the gate.

- [ ] **Step 1: Render every chart**

```bash
for c in $(find cluster/helm -name Chart.yaml | xargs -n1 dirname); do
  if grep -q '^dependencies:' "$c/Chart.yaml" && [ ! -d "$c/charts" ]; then
    echo "SKIP $c (needs helm dependency build)"; continue
  fi
  helm template test "$c" -f "$c/values.yaml" >/dev/null || echo "FAIL $c"
done
```
Expected: `FAIL` on nothing (only `SKIP` lines).

- [ ] **Step 2: Reference checks**

```bash
while read -r p; do [ -d "$p" ] || echo "BROKEN PATH $p"; done < <(
  grep -rh '^    path:' cluster/argocd/projects | sed 's/^    path: *//' | tr -d "\"' \r")
while read -r v; do [ -f ".$v" ] || echo "BROKEN VALUES $v"; done < <(
  grep -rh '\$values/' cluster/argocd/projects | sed 's/.*\$values//' | tr -d "\"' \r" | sort -u)
hack/check-exposure.sh --only argocd
python3 hack/check_docs_links.py
```
Expected: no `BROKEN` lines; the exposure checker clean; `0 broken relative links`.

- [ ] **Step 3: Rebase and push**

```bash
git fetch origin
git rebase origin/develop
git push origin feature/argocd-self-managed
```
Expected: linear, no merge commit.

- [ ] **Step 4: Open the PR**

Base `develop`, title `🚀 feat(argocd): manage Argo CD from the repository`. Body: the card number,
the render-identity evidence (`IDENTICAL` and the image list), the check outputs from Steps 1–2, and
the operator's adoption checklist verbatim from Task 5.

- [ ] **Step 5: Comment on card 344 and leave it in `Doing`**

```bash
set -a; . ~/.config/secrets/homelab.env; set +a
curl -s -X POST -H "Authorization: Bearer $VIKUNJA_API_TOKEN" -H "Content-Type: application/json" \
  -d '{"comment":"repo: vyrtualsynthese/homelab\nbranch: feature/argocd-self-managed\npr: <url>\n\nVerification: render identity IDENTICAL; every chart renders; check-exposure clean; 0 broken links.\n\nRemaining, operator only: (1) merge; (2) first sync of the argocd Application with prune off; (3) verify server, SSO and both routes; (4) delete sh.helm.release.v1.argocd.v<rev>; (5) enable auto-sync with prune in the UI. The card moves to Validation after (3)."}' \
  "https://vikunja.kub.local/api/v2/tasks/344/comments"
```
Expected: `201`. The card stays in `Doing`: the adoption is the blocker, and it is the operator's.

---

### Task 5: The operator's adoption (not the agent's)

Recorded here so the PR body and the card comment carry it verbatim.

- [ ] Render and diff against the cluster before the first sync:
  `helm template argocd cluster/helm/argocd -n argocd | kubectl diff -f -`. This is the only step that
  compares the file with what runs; the identity proof in Task 1 compared the file with itself.
  Resolve what it shows first — the `*.log.level: warn` block from 606e13ac may never have reached the
  cluster through the old hand run.
- [ ] Merge the PR. `argocd-root` creates the Application; it shows `OutOfSync`, nothing applied.
- [ ] Sync `argocd` from the UI, prune **off**. Pods restart only where the diff showed drift: a roll
  is expected if the log-level keys were never applied, and is not a failure of the adoption.
- [ ] Verify: server loads at `https://argocd.kub.local`, SSO signs in, both routes answer, the child
  Applications still list and compare.
- [ ] `helm -n argocd list` to read the revision, then
  `kubectl -n argocd delete secret sh.helm.release.v1.argocd.v<rev>`.
- [ ] Enable auto-sync with prune for the `argocd` Application in the UI, not in git.
- [ ] Close PR #966 with a comment: the image moves with the chart dependency now, and the
  `v3.6.1` upgrade is a chart bump to `10.15.0` or later, tracked on its own.
- [ ] On Renovate's next run, confirm it lists `argo-cd` under `cluster/helm/argocd/Chart.yaml`, and
  that it proposes the dex and redis pins as well as the chart — not a docker-tag PR for
  `quay.io/argoproj/argocd` from that file, which would undo the single lever. Nothing else in the
  repo changes for this.
- [ ] Move card 344 to `Validation` once the three checks above pass, with the sync's evidence.
