# Media stack security — Phase 0 implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop every non-public route in the cluster from answering the internet, and make a future route that forgets the gate fail a repository check.

**Architecture:** One Traefik `Middleware` (`kube-system/lan-only`) holding the LAN and WireGuard ranges, owned by a small chart and ArgoCD Application. Every non-public `IngressRoute` and core `Ingress` references it. A checker renders every chart, parses the routes, and fails when a non-public route lacks the gate. Verification happens from the public address, because the LAN is not the vantage point that matters.

**Tech Stack:** Traefik 2.9.4 CRDs (`traefik.containo.us/v1alpha1`, `ipWhiteList`), Helm 3, ArgoCD, Python 3 with PyYAML, `helm template`.

**Spec:** [`docs/superpowers/specs/2026-11-14-media-stack-security-design.md`](../specs/2026-11-14-media-stack-security-design.md)

This plan covers **Phase 0 only**. Phases 1, 1B and 2-6 get their own plans; do not pull their work in.

## Global Constraints

- Public hosts, exactly these five: `plex.kub.home.ashudev.com`, `seerr.kub.home.ashudev.com`, `ashuvidz.com`, `forum.ashuvidz.com`, `wiki.ashuvidz.com`. Everything else is gated.
- `cluster/argocd/**` and `cluster/infrastructure/traefik/traefik-config.yaml` use **CRLF**. Every write there must preserve it; the checker fails on an LF file in those paths.
- Traefik 2.9.4 and the installed CRD understand `ipWhiteList`, **not** `ipAllowList`. A field the CRD does not know is silently pruned, which leaves the allowlist empty and allows everyone. Never introduce `ipAllowList` before W5.
- The gate's cross-namespace reference form is `kube-system-lan-only@kubernetescrd`. A bare `lan-only` from another namespace does nothing.
- Never `kubectl apply` a GitOps-managed object to "fix" it. The two exceptions in this phase are the ArgoCD Application sync and the Traefik `HelmChartConfig`, both operator steps spelled out below.
- Commit messages use the repo's emoji prefixes (`🚀` feature, `🔧` chore, `📝` docs, `🐛` fix).
- Every step that says "operator" is run by the human, not the agent; the agent pastes the expected output and waits.

## Review Focus

| Failure mode the spec implies but no test names | Pinned by |
|---|---|
| Two routers share one host (`plex` has `plex-public` and `plex-media-public`); gating one and not the other leaves the door open | `hack/fixtures/duplicate-host.yaml` |
| A chart rename or a copy-paste leaves a middleware named `lan-only` with no namespace, which Traefik ignores | `hack/fixtures/bare-name.yaml` |
| The v3 field slips in early and the CRD prunes it, silently allowing everyone | `hack/fixtures/ipallowlist.yaml` |
| A core `Ingress` (FileBrowser) is gated in values but the annotation is dropped by the chart's own template | `hack/fixtures/ingress-annotation.yaml` |
| A shared `app.ingress.annotations` value also covers a **public** host, so "gating the annotation" takes `wiki.ashuvidz.com` offline | `hack/fixtures/public-gated.yaml` |
| An edit rewrites a CRLF file as LF, so ArgoCD sees a whole-file diff | checker's CRLF assertion |
| A chart renders no route at all, so it passes vacuously | checker reports a per-chart route count; the PR records the total and compares with the 63 live routes |

---

### Task 1: The gate chart and its Application

**Files:**
- Create: `cluster/helm/platform/traefik-gate/Chart.yaml`
- Create: `cluster/helm/platform/traefik-gate/values.yaml`
- Create: `cluster/helm/platform/traefik-gate/templates/middleware.yaml`
- Create: `cluster/helm/platform/traefik-gate/README.md`
- Create: `cluster/argocd/projects/platform/traefik-gate.yaml` (CRLF)

**Interfaces:**
- Produces: the `Middleware` `kube-system/lan-only`, with `spec.ipWhiteList.sourceRange` holding exactly two entries. Every later task references it as `kube-system-lan-only@kubernetescrd`.
- Produces: the values keys `lanOnly.lanRange` (default `192.168.1.0/24`) and `lanOnly.wireguardRange` (required, supplied by the operator).

- [ ] **Step 1: Operator — read the WireGuard address range**

Run on the edge Pi, or read the address in the wg-easy UI:

```bash
ssh rbp0 'sudo docker exec wg-easy sh -c "grep -i address /etc/wireguard/wg0.conf"'
```

Expected output is one line such as `Address = 10.8.0.1/24`. The range is that address with the host
bits cleared: `10.8.0.1/24` means `10.8.0.0/24`. If the container or interface is named differently,
read the range from the UI instead. Do not continue with a guess.

- [ ] **Step 2: Write the chart with the range deliberately unset**

`Chart.yaml`:

```yaml
apiVersion: v2
name: traefik-gate
description: Shared Traefik middlewares that keep non-public routes on the LAN.
type: application
version: 0.1.0
appVersion: "2.9.4"
```

`values.yaml`:

```yaml
traefik:
  apiVersion: traefik.containo.us/v1alpha1

lanOnly:
  # -- LAN range. The nodes are 192.168.1.27/.41/.46/.48.
  lanRange: 192.168.1.0/24
  # -- WireGuard range, read from the wg-easy instance (plan Task 1 Step 1).
  wireguardRange: ""
```

`templates/middleware.yaml`:

```yaml
{{- $wg := required "lanOnly.wireguardRange must be set from the wg-easy instance (design §8)" .Values.lanOnly.wireguardRange }}
apiVersion: {{ .Values.traefik.apiVersion }}
kind: Middleware
metadata:
  name: lan-only
  namespace: kube-system
  labels:
    app.kubernetes.io/name: traefik-gate
    app.kubernetes.io/instance: {{ .Release.Name }}
spec:
  # ipWhiteList, not ipAllowList: the installed CRD only declares the pre-2.5
  # name and prunes the v3 one, which leaves this object empty and allows
  # everyone. Rename both together with the CRDs in W5.
  ipWhiteList:
    sourceRange:
      - {{ .Values.lanOnly.lanRange }}
      - {{ $wg }}
```

- [ ] **Step 3: Run the render to verify it refuses an empty range**

```bash
helm template test cluster/helm/platform/traefik-gate -f cluster/helm/platform/traefik-gate/values.yaml
```

Expected: FAIL with `Error: execution error ... lanOnly.wireguardRange must be set`.

- [ ] **Step 4: Set the confirmed range and render again**

Set `wireguardRange` to the value from Step 1, then:

```bash
helm template test cluster/helm/platform/traefik-gate -f cluster/helm/platform/traefik-gate/values.yaml \
  | yq 'select(.kind=="Middleware") | .spec.ipWhiteList.sourceRange | length'
```

Expected: `2`.

- [ ] **Step 5: Write the README**

`README.md` must state: what the middleware is for, the two ranges and where the WireGuard one comes
from, that every non-public route must reference it, that `hack/check-exposure.sh` enforces that, and
the `ipWhiteList`/CRD-pruning trap with the W5 rename.

- [ ] **Step 6: Create the Application, preserving CRLF**

Body (same shape as the other Applications):

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: traefik-gate
  namespace: argocd
spec:
  project: default
  destination:
    server: "https://kubernetes.default.svc"
    namespace: kube-system
  syncPolicy:
    syncOptions:
      - CreateNamespace=true
  source:
    repoURL: "https://github.com/vyrtualsynthese/homelab.git"
    targetRevision: develop
    path: cluster/helm/platform/traefik-gate
```

Then convert to CRLF and verify:

```bash
perl -pi -e 's/\r?\n/\r\n/' cluster/argocd/projects/platform/traefik-gate.yaml
file cluster/argocd/projects/platform/traefik-gate.yaml
```

Expected: `ASCII text, with CRLF line terminators`.

- [ ] **Step 7: Commit**

```bash
git add cluster/helm/platform/traefik-gate cluster/argocd/projects/platform/traefik-gate.yaml
git commit -m "🚀 feat(gate): add the lan-only middleware for non-public routes"
```

- [ ] **Step 8: Operator — sync and verify the live object**

After the branch reaches `develop`, sync `traefik-gate` in ArgoCD, then read it through the read-only
MCP. Expected: `spec.ipWhiteList.sourceRange` has two entries, one `192.168.1.0/24` and one
`10.8.0.0/24` (or the confirmed range). An empty `spec` means the CRD pruned the field — stop and fix
before any route references it.

---

### Task 2: The checker and its fixtures

**Files:**
- Create: `hack/check-exposure.sh`
- Create: `hack/check_exposure.py`
- Create: `hack/fixtures/gated.yaml`
- Create: `hack/fixtures/ungated.yaml`
- Create: `hack/fixtures/duplicate-host.yaml`
- Create: `hack/fixtures/bare-name.yaml`
- Create: `hack/fixtures/ipallowlist.yaml`
- Create: `hack/fixtures/ingress-annotation.yaml`
- Create: `hack/fixtures/public-gated.yaml`
- Create: `hack/fixtures/helmchartconfig.yaml`
- Create: `hack/fixtures/helmchartconfig-malformed.yaml`
- Create: `hack/fixtures/mixed-hosts.yaml`
- Create: `hack/fixtures/empty-gate.yaml`
- Create: `hack/tests/test_check_exposure.py`

**Interfaces:**
- Produces: `hack/check-exposure.sh [--self-test] [--only <path-prefix>]`. Exit `0` clean, `1` violations, `2` charts skipped (incomplete coverage). Prints one line per violation: `<file or chart>: <host>: missing lan-only`.
- Consumes: the public host list from Global Constraints, and the reference name `kube-system-lan-only@kubernetescrd`.

- [ ] **Step 1: Write the fixtures first**

Each fixture is a minimal YAML document. `gated.yaml` is one `IngressRoute` whose single route carries
`- name: kube-system-lan-only@kubernetescrd`, and the checker must report it clean. `ungated.yaml` is
the same without the middleware, and must be reported. `duplicate-host.yaml` is one document with two
routes on the same host, one gated and one not, and the ungated route must be reported.
`bare-name.yaml` uses `- name: lan-only` with no namespace and must be reported (Traefik ignores it
from another namespace). `ipallowlist.yaml` uses `spec.ipAllowList` and must be reported with the
CRD-pruning message. `public-gated.yaml` is a route on a public host that carries the gate, and must
be reported: gating `plex` or `seerr` breaks the app. `ingress-annotation.yaml` is a core `Ingress`; one variant carries
`traefik.ingress.kubernetes.io/router.middlewares: "kube-system-lan-only@kubernetescrd"` and passes,
the other has no annotation and is reported.

- [ ] **Step 2: Write the checker's expectations into `--self-test` and run it**

```bash
hack/check-exposure.sh --self-test
```

Expected: FAIL because the script does not exist yet.

- [ ] **Step 3: Implement the parser, `hack/check_exposure.py`**

Rules, in order:

1. Load every document with `yaml.safe_load_all`. A malformed document is a violation, not a skip.
2. `IngressRoute` with an apiVersion starting `traefik.`: collect every host from every
   `routes[].match` with the regex ``Host\(`([^`]+)`\)``. For each route whose hosts are all outside
   the public list, that route must carry a middleware that is either the exact string
   `kube-system-lan-only@kubernetescrd` or an object with `name: lan-only` and `namespace: kube-system`.
   A route whose hosts include a public host must **not** carry it; report `public host gated`,
   because the gate would make that app unreachable.
3. `Ingress` with apiVersion `networking.k8s.io/v1`: every rule host outside the public list must have
   the annotation key `traefik.ingress.kubernetes.io/router.middlewares` whose comma-split list
   contains `kube-system-lan-only@kubernetescrd`.
4. Any document containing the key `ipAllowList` anywhere is a violation that names the CRD-pruning
   trap.
5. `HelmChartConfig` docs: parse `spec.valuesContent` as YAML and require `lan-only` in
   `ingressRoute.dashboard.middlewares`. The live `traefik-config.yaml` carries a mis-indented key that
   PyYAML rejects, so on a parse failure fall back to requiring the text `name: lan-only` in the
   values block.

- [ ] **Step 4: Implement `hack/check-exposure.sh`**

Behaviour:

1. `--self-test` runs the parser against `hack/fixtures/*` and compares each result with the expected
   verdict for that file; exit `0` only if all six match.
2. Otherwise, for every `cluster/helm/**/Chart.yaml` (filtered by `--only` when given):
   - if `Chart.yaml` declares `dependencies` and `charts/` is absent, copy the directory to
     `$(mktemp -d)`, run `helm dependency build` there, and render from the copy; a failed build is a
     skip (`exit 2`), named on stderr;
   - otherwise `helm template test <dir> -f <dir>/values.yaml`;
   - parse the rendered documents with the parser, then also parse every chart's `values.yaml` for
     `ipAllowList` (a value the template may not render is still a trap);
   - print the chart name and its route count so a missing render is visible.
3. After the charts, parse `cluster/infrastructure/traefik/traefik-config.yaml`.
4. Assert CRLF for every file under `cluster/argocd/` and for `traefik-config.yaml`.
5. Exit with the highest severity seen (2 over 1 over 0).

- [ ] **Step 5: Run the self-test until it passes**

```bash
hack/check-exposure.sh --self-test
```

Expected: `self-test: 12/12`, exit `0`.

- [ ] **Step 6: Run it against the repository and confirm it fails**

```bash
hack/check-exposure.sh | tail -30
```

Expected: exit `1`, with one line per non-public route lacking the gate. That list is the work for
Tasks 3-8. Save the count in your notes; the final task compares against it.

- [ ] **Step 7: Commit**

```bash
git add hack/check-exposure.sh hack/check_exposure.py hack/fixtures
git commit -m "🔧 chore(security): add the exposure checker for non-public routes"
```

---

### Task 3: Gate the media-stack charts that use values

**Files (modify one line in each):**
- `cluster/helm/media-stack/sonarr/values.yaml:88`
- `cluster/helm/media-stack/sonarr-anime/values.yaml:88`
- `cluster/helm/media-stack/radarr/values.yaml:69`
- `cluster/helm/media-stack/radarr-anime/values.yaml:69`
- `cluster/helm/media-stack/lidarr/values.yaml:91`
- `cluster/helm/media-stack/bazarr/values.yaml:89`
- `cluster/helm/media-stack/bazarr-anime/values.yaml:89`
- `cluster/helm/media-stack/prowlarr/values.yaml:87`
- `cluster/helm/media-stack/autobrr/values.yaml:93`
- `cluster/helm/media-stack/tautulli/values.yaml:95`
- `cluster/helm/media-stack/maintainerr/values.yaml:98`
- `cluster/helm/media-stack/kavita/values.yaml:104`

**Interfaces:**
- Consumes: `kube-system/lan-only` (Task 1).
- Produces: those twelve charts render `kube-system-lan-only@kubernetescrd` on their only route. `plex` and `seerr` are public and stay untouched.

- [ ] **Step 1: Replace `middlewares: []` with the gate in each file**

Every file above has exactly one `middlewares: []` under its ingress block. Replace it with:

```yaml
  middlewares:
    - name: lan-only
      namespace: kube-system
```

- [ ] **Step 2: Gate kavita's redirect router too**

`cluster/helm/media-stack/kavita/templates/ingressroute.yaml` renders a second router on `web` whose
`middlewares:` list holds only `<fullname>-redirect-https`. Add the gate as the **first** entry in
that list, so an outside request is rejected before the redirect is issued:

```yaml
      middlewares:
        - name: kube-system-lan-only@kubernetescrd
        - name: {{ $fullName }}-redirect-https
```

- [ ] **Step 3: Verify one chart renders the reference**

```bash
helm template test cluster/helm/media-stack/sonarr -f cluster/helm/media-stack/sonarr/values.yaml \
  | grep -A1 'middlewares:' | head -4
```

Expected: `- name: kube-system-lan-only@kubernetescrd`.

- [ ] **Step 4: Run the checker on the area**

```bash
hack/check-exposure.sh --only cluster/helm/media-stack
```

Expected: the twelve charts are clean; only charts handled by Task 4 still fail.

- [ ] **Step 5: Commit**

```bash
git add cluster/helm/media-stack/*/values.yaml
git commit -m "🔒 feat(media-stack): gate the values-driven routes to the LAN"
```

---

### Task 4: Gate the media-stack exceptions

**Files:**
- Modify: `cluster/helm/media-stack/qbittorrent/templates/ingress.yaml`
- Modify: `cluster/helm/media-stack/calibre-web-automated/templates/ingressroute.yaml`
- Modify: `cluster/helm/media-stack/calibre-web-automated/values.yaml:229-245`
- Delete: `cluster/helm/media-stack/calibre-web-automated/templates/middleware-allowlist.yaml`

**Interfaces:**
- Consumes: `kube-system-lan-only@kubernetescrd` (Task 1).
- Produces: `qbittorrent` and `calibre` render the shared gate. `calibre` no longer renders a private allowlist, so the LAN range has one home.

- [ ] **Step 1: Gate the qBittorrent route**

In `templates/ingress.yaml`, the route block currently reads `match`, `kind: Rule`, `services`. Insert
between `kind` and `services`:

```yaml
      middlewares:
        - name: kube-system-lan-only@kubernetescrd
```

- [ ] **Step 2: Replace calibre's private allowlist with the shared gate**

In `templates/ingressroute.yaml`, delete the four template lines that build `$middlewares` from
`.Values.ingressRoute.sourceRanges` (the `$middlewares := list`, the `if`, the `append`, the `end`),
keep the `range .Values.ingressRoute.middlewares` block, and in `values.yaml` delete the
`sourceRanges:` key and its two comment lines. Add the gate to the values file:

```yaml
  middlewares:
    - name: lan-only
      namespace: kube-system
```

Delete `templates/middleware-allowlist.yaml`.

- [ ] **Step 3: Render both and confirm**

```bash
helm template test cluster/helm/media-stack/qbittorrent -f cluster/helm/media-stack/qbittorrent/values.yaml | grep -c 'kube-system-lan-only@kubernetescrd'
helm template test cluster/helm/media-stack/calibre-web-automated -f cluster/helm/media-stack/calibre-web-automated/values.yaml | grep -c 'lan-only'
```

Expected: `1` and `1`, and no rendered `Middleware` named `*-allowlist`.

- [ ] **Step 4: Run the checker on the area**

```bash
hack/check-exposure.sh --only cluster/helm/media-stack
```

Expected: exit `0` for this scope, with `plex` and `seerr` reported as public exempt.

- [ ] **Step 5: Commit**

```bash
git add cluster/helm/media-stack/qbittorrent cluster/helm/media-stack/calibre-web-automated
git commit -m "🔒 feat(media-stack): gate qBittorrent and fold calibre onto the shared allowlist"
```

---

### Task 5: Gate the monitoring routes

**Files:**
- Modify: `cluster/helm/monitoring/prometheus/templates/ingressroute.yaml`
- Modify: `cluster/helm/monitoring/loki/templates/ingressroute.yaml`
- Modify: `cluster/helm/monitoring/grafana/templates/ingressroute.yaml`

**Interfaces:**
- Consumes: `kube-system-lan-only@kubernetescrd` (Task 1).
- Produces: Prometheus, Loki and Grafana stop answering the internet. These are the routes that currently serve metrics and logs unauthenticated.

- [ ] **Step 1: Add the gate to each route**

In each template, insert between `kind: Rule` and `services:`:

```yaml
      middlewares:
        - name: kube-system-lan-only@kubernetescrd
```

Add a one-line comment above it naming the reason: these three serve data with no application login.

- [ ] **Step 2: Render each and confirm**

```bash
for c in prometheus loki grafana; do
  helm template test cluster/helm/monitoring/$c -f cluster/helm/monitoring/$c/values.yaml | grep -c 'kube-system-lan-only@kubernetescrd'
done
```

Expected: `1` three times.

- [ ] **Step 3: Run the checker on the area**

```bash
hack/check-exposure.sh --only cluster/helm/monitoring
```

Expected: exit `0`.

- [ ] **Step 4: Commit**

```bash
git add cluster/helm/monitoring
git commit -m "🔒 feat(monitoring): keep Prometheus, Loki and Grafana on the LAN"
```

---

### Task 6: Gate the legacy `ingress-public` charts

**Files (both route templates in each chart):**
- `cluster/helm/ghostfolio/templates/ingress-public.yaml` and `ingress-public-redirect.yaml`
- `cluster/helm/guacamole/templates/ingress-public.yaml` and `ingress-public-redirect.yaml`
- `cluster/helm/koillection/templates/ingress-public.yaml` and `ingress-public-redirect.yaml`
- `cluster/helm/linkstack/templates/ingress-public.yaml` and `ingress-public-redirect.yaml`
- `cluster/helm/monitorss/templates/ingress-public.yaml` and `ingress-public-redirect.yaml`
- `cluster/helm/n8n/templates/ingress-public.yaml` and `ingress-public-redirect.yaml`
- `cluster/helm/news/freshrss/templates/ingress-public.yaml` and `ingress-public-redirect.yaml`
- `cluster/helm/syncthing/templates/ingress-public.yaml` and `ingress-public-redirect.yaml`
- `cluster/helm/thelounge/templates/ingress-public.yaml` and `ingress-public-redirect.yaml`
- `cluster/helm/wiki/templates/ingress-public.yaml` and `ingress-public-redirect.yaml`
- `cluster/helm/wiki/templates/ingress-old.yaml` and `ingress-old-secure.yaml`

**Interfaces:**
- Consumes: `kube-system-lan-only@kubernetescrd` (Task 1).
- Produces: ten `*.kub.home.ashudev.com` routes that currently have no middleware at all now carry the gate, **and** their `web` redirect siblings answer `403` from outside instead of `301`. A redirect that answers proves the route exists; the design's acceptance test is `403` for every non-public host on both entry points.

- [ ] **Step 1: Confirm the shape of each pair**

```bash
grep -L 'middlewares' cluster/helm/{ghostfolio,guacamole,koillection,linkstack,monitorss,n8n,news/freshrss,syncthing,thelounge,wiki}/templates/ingress-public.yaml
```

Expected: all ten files listed. A file that is not listed already has a middlewares block — add the
gate to that block instead of creating a second one. The `-redirect.yaml` siblings already have a
`middlewares:` list (after `services:`), holding `<fullname>-redirect-https`.

- [ ] **Step 2: Add the gate to the secure template**

Insert between `kind: Rule` and `services:`:

```yaml
      middlewares:
        - name: kube-system-lan-only@kubernetescrd
```

- [ ] **Step 3: Add the gate first in the redirect template**

In each `ingress-public-redirect.yaml`, put the gate **before** the redirect middleware, so an
outside request is rejected instead of redirected:

```yaml
      middlewares:
        - name: kube-system-lan-only@kubernetescrd
        - name: <fullname>-redirect-https
```

- [ ] **Step 4: Render each and confirm two references**

```bash
for c in ghostfolio guacamole koillection linkstack monitorss n8n news/freshrss syncthing thelounge wiki; do
  printf '%-14s ' "$c"
  helm template test cluster/helm/$c -f cluster/helm/$c/values.yaml | grep -c 'kube-system-lan-only@kubernetescrd'
done
```

Expected: `2` for each chart. A chart that fails to render is a failure, not a skip — read the error.
`syncthing` also renders TCP/UDP routes; those are layer-4 and cannot take an HTTP middleware. Leave
them to Phase 6's router closure and say so in the commit body.

- [ ] **Step 5: Wiki — gate only the `-old` templates**

`wiki.ashuvidz.com` is public and its route reads `app.ingress.annotations`; the internal
`wiki.kub.home.ashudev.com` routes are separate templates with their own `middlewares:` lists. Adding
the gate to the shared annotation would take the public wiki offline. Add it only to
`ingress-old.yaml` and `ingress-old-secure.yaml`, as the **first** entry:

```yaml
      middlewares:
        - name: kube-system-lan-only@kubernetescrd
        - name: wiki-app-redirect-old
```

- [ ] **Step 6: Run the checker on each and commit**

```bash
hack/check-exposure.sh --only cluster/helm/ghostfolio
```

Repeat per chart until each exits `0`, then:

```bash
git add cluster/helm/ghostfolio cluster/helm/guacamole cluster/helm/koillection \
  cluster/helm/linkstack cluster/helm/monitorss cluster/helm/n8n cluster/helm/news \
  cluster/helm/syncthing cluster/helm/thelounge cluster/helm/wiki
git commit -m "🔒 feat(edge): gate the legacy public hostnames to the LAN"
```

---

### Task 7: Gate the remaining routes

**Files:**
- Modify: `cluster/helm/authentik/templates/ingressroute.yaml`
- Modify: `cluster/helm/databases/pgadmin/templates/ingressroute.yaml`
- Delete: `cluster/helm/databases/pgadmin/templates/middleware.yaml`
- Modify: `cluster/helm/databases/pgadmin/values.yaml:4-7`
- Modify: `cluster/helm/filebrowser/values.yaml` — the route moved out of `media-stack` and is now rendered by that chart's own `templates/ingressroute.yaml`
- Modify: `cluster/helm/postiz/values.yaml:33`
- Modify: `cluster/helm/firefly/values.yaml:49`
- Modify: `cluster/helm/firefly/templates/ingress-public-importer.yaml`
- Modify: `cluster/helm/firefly/templates/ingress-public-redirect-importer.yaml`
- Modify: `cluster/helm/discourse/templates/ingress-old.yaml`
- Modify: `cluster/helm/discourse/templates/ingress-old-secure.yaml`
- Modify: `cluster/helm/dashy/templates/ingress.yaml`
- Modify: `cluster/helm/dashy/templates/ingress-redirect.yaml`
- Modify: `cluster/helm/dashy/templates/ingress-old.yaml`
- Modify: `cluster/helm/dashy/templates/ingress-old-secure.yaml`

**Interfaces:**
- Consumes: `kube-system-lan-only@kubernetescrd` (Task 1).
- Produces: `authentik`, `pgadmin`, `filebrowser`, `dashy`, `postiz` and the firefly importer all carry the gate. `pgadmin` stops rendering a duplicate private allowlist.

- [ ] **Step 1: Authentik**

Add the gate to the existing `middlewares:` list of both documents in
`templates/ingressroute.yaml` (`authentik-kub-home` and its redirect):

```yaml
        - name: kube-system-lan-only@kubernetescrd
```

- [ ] **Step 2: pgAdmin**

In `templates/ingressroute.yaml`, replace `- name: {{ .Chart.Name }}-lan-only` with
`- name: kube-system-lan-only@kubernetescrd`. Delete `templates/middleware.yaml`. Delete the
`lanCidr` block and its comment from `values.yaml`.

- [ ] **Step 3: Dashy — four hand-written routers**

Dashy's templates do not read `ingress.annotations`; the value is unused by them. Add the gate as the
first entry of the `middlewares:` list in all four: `ingress.yaml` (`dashy.ashudev.com`, websecure),
`ingress-redirect.yaml` (`dashy.ashudev.com`, web), `ingress-old.yaml` (`kub.home.ashudev.com`, web)
and `ingress-old-secure.yaml` (`kub.home.ashudev.com`, websecure):

```yaml
      middlewares:
        - name: lan-only
          namespace: kube-system
```

- [ ] **Step 4: Firefly — one core Ingress and two hand-written routers**

`values.yaml:49` covers `firefly.kub.home.ashudev.com` (core Ingress). The importer host is a
hand-written `IngressRoute` pair with a hardcoded list; prepend the gate in both:

```yaml
      middlewares:
        - name: kube-system-lan-only@kubernetescrd
        - name: importer-auth
```

and in `ingress-public-redirect-importer.yaml`, the same first entry before
`firefly-importer-redirect-https`.

- [ ] **Step 5: Discourse — the internal host only**

`forum.ashuvidz.com` is public and stays ungated. The `discourse.kub.home.ashudev.com` pair in
`templates/ingress-old.yaml` and `ingress-old-secure.yaml` has its own `middlewares:` list; prepend
the gate there, as for wiki. Do not touch the public route's annotations.

- [ ] **Step 6: Filebrowser and Postiz — core Ingress annotations**

Prepend the gate to each router.middlewares value, keeping the existing entries in their current
order:

- `filebrowser`: its chart's `traefik.middlewares` list, `lan-only` before `security`
- `postiz`: `"kube-system-lan-only@kubernetescrd,kube-system-redirect-https@kubernetescrd"`

- [ ] **Step 7: Render and run the checker per chart**

```bash
for c in authentik discourse dashy databases/pgadmin filebrowser postiz firefly; do
  printf '%-24s ' "$c"; hack/check-exposure.sh --only cluster/helm/$c >/dev/null && echo pass || echo FAIL
done
```

Expected: `pass` seven times. Charts with dependencies are copied and built by the checker; the first
run is slow.

- [ ] **Step 8: Commit**

```bash
git add cluster/helm/authentik cluster/helm/databases/pgadmin cluster/helm/dashy \
  cluster/helm/postiz cluster/helm/firefly cluster/helm/discourse cluster/helm/filebrowser
git commit -m "🔒 feat(edge): gate authentik, pgAdmin and the remaining internal hostnames"
```

---

### Task 8: Gate the Traefik dashboard

**Files:**
- Modify: `cluster/infrastructure/traefik/traefik-config.yaml` (CRLF)

**Interfaces:**
- Consumes: `kube-system/lan-only` (Task 1).
- Produces: the dashboard route, which is rendered from a hand-applied `HelmChartConfig` and therefore invisible to the chart checker until this task, carries the gate. The basic auth stays.

- [ ] **Step 1: Add the middleware to the dashboard route**

In `valuesContent`, under `ingressRoute.dashboard.middlewares`, add a second entry:

```yaml
          - name: lan-only
            namespace: kube-system
```

- [ ] **Step 2: Verify CRLF is intact**

```bash
file cluster/infrastructure/traefik/traefik-config.yaml
```

Expected: `ASCII text, with CRLF line terminators`.

- [ ] **Step 3: Commit**

```bash
git add cluster/infrastructure/traefik/traefik-config.yaml
git commit -m "🔒 feat(traefik): keep the dashboard on the LAN"
```

- [ ] **Step 4: Operator — re-apply the HelmChartConfig**

This file is not under ArgoCD. Apply it the way the cluster was built (the K3s manifest is applied
with `kubectl apply`), then confirm the dashboard answers `403` from the public address and `401` (not
`403`) from the LAN.

---

### Task 9: Record the rule where a builder will see it

**Files:**
- Modify: `docs/charts/README.md`
- Modify: `AGENTS.md`
- Modify: `docs/superpowers/specs/2026-11-14-media-stack-security-design.md` (status line)

**Interfaces:**
- Produces: a new chart cannot be written without meeting the gate rule, and the design says Phase 0 is implemented.

- [ ] **Step 1: Add the contract requirement**

In `docs/charts/README.md`, under **Required**, add:

> | 19 | A non-public route references `kube-system-lan-only@kubernetescrd` | The internet reaches every route by hostname; the allowlist is the only thing that keeps internal apps internal |

And under **Checking a chart against this list**, add `hack/check-exposure.sh` to the commands.

- [ ] **Step 2: Add the agent rule**

In `AGENTS.md` §4, add one bullet: every non-public `IngressRoute` or core `Ingress` must carry
`kube-system-lan-only@kubernetescrd`, the public list is the five hosts in the design, and
`hack/check-exposure.sh` must pass before a PR.

- [ ] **Step 3: Update the design status**

Change the design's status line to `Status: approved and implemented (Phase 0), 2026-11-14.`

- [ ] **Step 4: Commit**

```bash
git add docs/charts/README.md AGENTS.md docs/superpowers/specs/2026-11-14-media-stack-security-design.md
git commit -m "📝 docs(security): make the LAN gate part of the chart contract"
```

---

### Task 10: Verify from outside, then hand over

**Files:** none.

**Interfaces:**
- Consumes: every earlier task.
- Produces: evidence that the door is closed and legitimate use still works, pasted into the PR.

- [ ] **Step 1: Full checker run**

```bash
hack/check-exposure.sh
```

Expected: exit `0`, no skips (`exit 2`), and a route count that matches the 63 live `IngressRoute`
objects plus the core `Ingress` objects. If the count differs, find out why before continuing.
The count covers HTTP routes in git only: it excludes the layer-4 (TCP/UDP) routes and the
unmanaged `readarr` route, so it is not by itself proof that the door is shut.

- [ ] **Step 2: Operator — public probes**

```bash
for h in sonarr radarr lidarr prowlarr bazarr bazarr-anime autobrr qbittorrent maintainerr tautulli \
         kavita prometheus loki grafana pgadmin calibre filebrowser authentik; do
  printf '%-14s ' "$h"
  curl -s -o /dev/null -w '%{http_code}\n' -H "Host: $h.kub.local" http://80.14.57.209/
done
```

Expected: `403` for every line. Anything else is a missed route, not a pass.

Repeat by name for every TLS host, because they resolve publicly and need no forged header. The list
is the internal set: `kavita.`, `filebrowser.`, `authentik.`, `n8n.`, `syncthing.`, `thelounge.`,
`freshrss.`, `ghostfolio.`, `guacamole.`, `koillection.`, `linkstack.`, `monitorss.`, `postiz.`,
`firefly.`, `firefly-importer.`, `wiki.` and `discourse.` under `kub.home.ashudev.com`, plus
`dashy.ashudev.com` and the bare `kub.home.ashudev.com`. Use `curl --resolve <host>:443:80.14.57.209
https://<host>/`. Expected: `403` for every one, including the `http://` redirect variants.

- [ ] **Step 3: Operator — public probes that must still work**

```bash
curl -s -o /dev/null -w 'plex %{http_code}\n' https://plex.kub.home.ashudev.com/ --resolve plex.kub.home.ashudev.com:443:80.14.57.209
curl -s -o /dev/null -w 'seerr %{http_code}\n' https://seerr.kub.home.ashudev.com/ --resolve seerr.kub.home.ashudev.com:443:80.14.57.209
curl -s -o /dev/null -w 'ashuvidz %{http_code}\n' https://ashuvidz.com/
```

Expected: `200` or `302` — not `403`.

- [ ] **Step 4: Operator — LAN probes**

From a machine on `192.168.1.0/24`, open `http://sonarr.kub.local`, `http://qbittorrent.kub.local`,
`http://grafana.kub.local` and `https://filebrowser.kub.home.ashudev.com`. Expected: all reachable as
before. A `403` here means the source address is not what Traefik sees — stop, and check
`externalTrafficPolicy` and any upstream NAT before going further.

- [ ] **Step 5: Operator — over WireGuard**

Connect the VPN client and open `http://sonarr.kub.local`. Expected: reachable. A timeout means the
WireGuard range in Task 1 is wrong.

- [ ] **Step 6: Operator — close the two non-HTTP ports**

At the router, remove the forwards for `25565` and `22000`. Then, from outside:

```bash
for p in 25565 22000; do timeout 3 bash -c "</dev/tcp/80.14.57.209/$p" 2>/dev/null && echo "$p STILL OPEN" || echo "$p closed"; done
```

Expected: `closed` twice.

- [ ] **Step 7: Handle the unmanaged `readarr` route**

The live cluster has a `readarr` `IngressRoute` with no chart behind it, so the checker cannot see
it. Probe it:

```bash
curl -s -o /dev/null -w '%{http_code}\n' -H 'Host: readarr.kub.local' http://80.14.57.209/
```

Expected after Phase 0: `403`. If it answers anything else, the operator either deletes the leftover
Deployment, Service and `IngressRoute` or brings them under a chart with the gate. Record the decision
in the PR.

- [ ] **Step 8: Handover**

Rebase on `develop`, run the checker once more on the rebased tree, push, and open the PR with the
probe results as evidence:

```bash
git fetch origin
git rebase origin/develop
hack/check-exposure.sh
git push origin feature/media-stack-security
```

PR body: the five public hosts, the list of gated areas, the before/after probe table, the route
count, the `readarr` decision, the duplicate `plex-media-public` router recorded for Phase 2, and the
note that Phases 1-6 remain open.
