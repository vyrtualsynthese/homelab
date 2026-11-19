# Syncthing behind the authentik gate — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put `syncthing.kub.home.ashudev.com` behind the `authentik-forwardauth` gate, bound to the `authentik Admins` group, so the operator can then delete Syncthing's own GUI login and have authentik be the only prompt.

**Architecture:** The chart's TLS router gains a values-driven `middlewares` list in the qBittorrent shape: `lan-only` first, then `authentik-forwardauth`, both rendered as `kube-system-<name>@kubernetescrd`. The authentik side is one blueprint file under `cluster/helm/authentik/files/`, which the existing `files/*.yaml` glob already turns into the `authentik-blueprints` ConfigMap. The plain-HTTP router keeps `lan-only` and its redirect, ungated, so a plain-HTTP visit costs no forward-auth round trip before the redirect.

**Tech Stack:** Helm 3, Traefik 2.11 CRDs (`traefik.containo.us/v1alpha1`), authentik blueprints, the repo's shell/python checks in `hack/`.

**Spec:** `docs/superpowers/specs/2026-11-19-syncthing-authentik-gate-design.md`

## Global Constraints

- Ingress is Traefik v2 CRDs only: `traefik.containo.us/v1alpha1`, never `networking.k8s.io/v1`.
- Middleware order on a gated route is `lan-only`, then `authentik-forwardauth`. Every non-public route carries `kube-system-lan-only@kubernetescrd`, and `hack/check-exposure.sh` must pass.
- `syncthing.kub.home.ashudev.com` is **not** in `PUBLIC_HOSTS` in `hack/check_exposure.py`, and this change does not add it.
- The route keeps its current certificate: `tls.certResolver: myresolver` on `websecure`.
- The sync ports (22000 TCP/UDP, 21027 UDP) are not touched by any task.
- The gate binds to the group named exactly `authentik Admins`.
- `external_host` must be `https://syncthing.kub.home.ashudev.com` — the scheme must match the router.
- Never commit a secret. The blueprint carries no client secret: `forward_single` needs none.
- Chart version bumps: `syncthing` 0.2.1 → 0.2.2.

## Review Focus

- **A signed-out browser reaching the WebUI.** The gate only exists if the *rendered* `websecure` route lists both middlewares; a typo in a name is a 500 from Traefik, and a missing second entry is an open page. Pinned in Task 1, step 1 and step 5.
- **`lan-only` lost or reordered while adding the gate.** The internet reaches this route by `Host` header alone. Pinned by `hack/check-exposure.sh` in Task 1, step 6.
- **The plain-HTTP router accidentally gated.** A forward-auth check before a 301 is a round trip for nothing, and a redirect that lands on a login page mid-flow reads as a broken route. Pinned in Task 1, step 5 (the redirect route must still carry exactly `lan-only` + `<fullname>-redirect-https`).
- **The blueprint not reaching authentik.** The ConfigMap is a `files/*.yaml` glob, so a wrong directory or extension produces a valid sync that does nothing, and the gate has no provider to ask. Pinned in Task 2, step 3.
- **A signed-in non-admin being let in.** The gate is only as narrow as its policy binding. Pinned in Task 2, step 1 (group assertion) and Task 4, step 3 (live 403).

---

### Task 1: The gate on the chart's TLS router

**Files:**
- Modify: `cluster/helm/syncthing/values.yaml` (`app.ingress` block, around line 47)
- Modify: `cluster/helm/syncthing/templates/ingress-public.yaml:25-26`
- Modify: `cluster/helm/syncthing/Chart.yaml:21`
- Modify: `cluster/helm/syncthing/README.md` (new section after "The parts that are not obvious")
- Modify: `cluster/helm/syncthing/templates/NOTES.txt`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: the values key `app.ingress.middlewares`, a list of `{name, namespace}` maps. Task 2's blueprint provides the provider the second entry names; Task 3 documents the resulting route.

- [ ] **Step 1: Write the check that fails today**

Run it before touching anything; it must fail, because the route lists only `lan-only`:

```bash
helm template test cluster/helm/syncthing --namespace syncthing \
  | grep -c 'kube-system-authentik-forwardauth@kubernetescrd'
```

Expected now: `0`.

- [ ] **Step 2: Add the middleware list to `cluster/helm/syncthing/values.yaml`**

Inside the existing `app.ingress` block, directly after `annotations: {}`, add:

```yaml
    # Middlewares for the TLS router, rendered as `<namespace>-<name>@kubernetescrd`
    # in this order.
    #
    # `lan-only` is the network floor every non-public route carries, and it runs
    # first: an outside request is refused before the forward-auth round trip.
    #
    # `authentik-forwardauth` is the gate. Its authentik side is
    # `cluster/helm/authentik/files/app-syncthing.yaml`: a `forward_single` proxy
    # provider for this host, bound to the `authentik Admins` group. The WebUI keeps
    # its own login as break-glass until the operator removes it in Settings → GUI
    # (and turns on Insecure Admin Access) once the gate is proven.
    #
    # The plain-HTTP router in `ingress-public-redirect.yaml` reads none of this: it
    # keeps `lan-only` and the redirect, so a plain-HTTP visit costs no round trip
    # before the 301.
    middlewares:
      - name: lan-only
        namespace: kube-system
      - name: authentik-forwardauth
        namespace: kube-system
```

- [ ] **Step 3: Render that list in `cluster/helm/syncthing/templates/ingress-public.yaml`**

Replace the hardcoded block

```yaml
      middlewares:
        - name: kube-system-lan-only@kubernetescrd
```

with

```yaml
      {{- with .Values.app.ingress.middlewares }}
      middlewares:
        {{- range . }}
        - name: {{ printf "%s-%s@kubernetescrd" .namespace .name }}
        {{- end }}
      {{- end }}
```

- [ ] **Step 4: Bump the chart version**

In `cluster/helm/syncthing/Chart.yaml`, `version: 0.2.1` → `version: 0.2.2`.

- [ ] **Step 5: Verify the render**

```bash
helm lint cluster/helm/syncthing --namespace syncthing | tail -1
helm template test cluster/helm/syncthing --namespace syncthing \
  | grep -A4 'kind: IngressRoute' | grep -E 'name: |match: '
```

Expected: lint reports `0 chart(s) failed`; the `syncthing-public` route matches ``Host(`syncthing.kub.home.ashudev.com`)`` and lists the two middlewares in order — `kube-system-lan-only@kubernetescrd`, then `kube-system-authentik-forwardauth@kubernetescrd`; the `syncthing-public-redirect` route lists `kube-system-lan-only@kubernetescrd` and `syncthing-redirect-https` only.

- [ ] **Step 6: Confirm the exposure checker still passes**

```bash
./hack/check-exposure.sh --only syncthing
```

Expected: `checked: 1 routes` (or the chart's number) and `result: OK`.

- [ ] **Step 7: Say in the README what the gate is and what breaks without it**

In `cluster/helm/syncthing/README.md`, add a section before `## Backup`:

```markdown
## The WebUI gate

`syncthing.kub.home.ashudev.com` is gated by `kube-system-authentik-forwardauth`, after
`kube-system-lan-only`: both middlewares are the `app.ingress.middlewares` list in `values.yaml`, and
the authentik side is `cluster/helm/authentik/files/app-syncthing.yaml`, bound to `authentik Admins`.
Syncthing has no native single sign-on, so this is the gate or nothing.

The WebUI's own login is separate and lives on the `syncthing-config` claim. Clear it in
**Settings → GUI** and tick **Insecure Admin Access** — without that tick Syncthing refuses the GUI to
anything that is not localhost, which includes every browser arriving through Traefik. Do that
**only after** the gate is proven in a browser; with no login and no gate, the WebUI is open to the
LAN.

**If authentik is down** the WebUI is unreachable; sync itself keeps running, since the sync and
discovery ports do not cross Traefik. The way back is `kubectl -n syncthing port-forward
svc/syncthing 8384:8384`, which reaches the GUI with the local login — that is the break-glass, and
`kubectl` access is cluster-admin anyway.
```

- [ ] **Step 8: Carry the same two sentences into `templates/NOTES.txt`**

Append to the existing numbered list, as a final line:

```
5. Once the browser shows authentik before the WebUI, remove Syncthing's own login: Settings → GUI,
   clear the user and password, tick Insecure Admin Access, save. Do it in that order — the gate is
   the only thing left in front of the WebUI afterwards.
```

Then add, before that list, one line pointing at the restart trigger: `The WebUI is now behind the authentik gate; the route change restarts nothing, so reload the page once ArgoCD has synced.`

- [ ] **Step 9: Commit**

```bash
git add cluster/helm/syncthing
git commit -m "🚀 feat(syncthing): put the WebUI behind the authentik forward-auth gate

The TLS router now takes its middlewares from values: lan-only, then
authentik-forwardauth. The plain-HTTP router keeps lan-only and the redirect.
Chart 0.2.2."
```

---

### Task 2: The authentik blueprint

**Files:**
- Create: `cluster/helm/authentik/files/app-syncthing.yaml`

**Interfaces:**
- Consumes: the host and the middleware name from Task 1 (`syncthing.kub.home.ashudev.com`, provider referenced by Traefik as `kube-system-authentik-forwardauth`).
- Produces: the authentik objects behind that middleware — provider `syncthing`, application slug `syncthing`, group binding `authentik Admins`.

- [ ] **Step 1: Create the file, copying `app-qbittorrent.yaml`'s shape**

`cluster/helm/authentik/files/app-syncthing.yaml`:

```yaml
version: 1
metadata:
  name: ashulab-app-syncthing
entries:
  - model: authentik_blueprints.metaapplyblueprint
    attrs:
      identifiers:
        name: ashulab-groups
      required: true

  # Forward auth: no internal host and no client secret. Syncthing has no native
  # single sign-on, so this gate is the only user check in front of the WebUI once
  # its own login is removed. The application is bound to `authentik Admins` alone:
  # this is an administration surface, not a shared tool.
  - model: authentik_providers_proxy.proxyprovider
    id: syncthing-provider
    identifiers:
      name: syncthing
    attrs:
      name: syncthing
      mode: forward_single
      # Scheme must match the browser: the route is on `websecure`, and the outpost
      # takes the session cookie's Secure flag from this scheme.
      external_host: https://syncthing.kub.home.ashudev.com
      authorization_flow: !Find [authentik_flows.flow, [slug, default-provider-authorization-implicit-consent]]
      invalidation_flow: !Find [authentik_flows.flow, [slug, default-provider-invalidation-flow]]

  - model: authentik_core.application
    id: syncthing-app
    identifiers:
      slug: syncthing
    attrs:
      name: Syncthing
      slug: syncthing
      provider: !KeyOf syncthing-provider
      meta_launch_url: https://syncthing.kub.home.ashudev.com/
      meta_icon: https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/png/syncthing.png
      policy_engine_mode: any

  - model: authentik_policies.policybinding
    identifiers:
      target: !KeyOf syncthing-app
      order: 0
    attrs:
      group: !Find [authentik_core.group, [name, authentik Admins]]
```

- [ ] **Step 2: Check it parses the way the glob expects**

```bash
test -f cluster/helm/authentik/files/app-syncthing.yaml && echo present
grep -c '^  - model: ' cluster/helm/authentik/files/app-syncthing.yaml
grep -n 'external_host:\|^      group:\|^      slug:' cluster/helm/authentik/files/app-syncthing.yaml
```

Expected: `present`; `4`; one line each for `external_host: https://syncthing.kub.home.ashudev.com`, `group: !Find [authentik_core.group, [name, authentik Admins]]`, and `slug: syncthing` twice (application identifiers and attrs).

- [ ] **Step 3: Prove the chart picks the file up**

The authentik chart is a wrapper, so build its dependency first:

```bash
helm dependency build cluster/helm/authentik >/dev/null
helm template test cluster/helm/authentik --namespace authentik \
  | grep -c 'app-syncthing.yaml:'
```

Expected: `1` — the file is a key in the `authentik-blueprints` ConfigMap. `0` means the extension or the directory is wrong, and the sync would look healthy while creating nothing.

- [ ] **Step 4: Commit**

```bash
git add cluster/helm/authentik/files/app-syncthing.yaml
git commit -m "🚀 feat(authentik): add the syncthing forward-auth provider, Admins only"
```

---

### Task 3: The two reference pages

**Files:**
- Modify: `docs/reference/routes.md:69` and the "Two routes carry no `lan-only` yet" paragraph
- Modify: `docs/reference/sso-applications.md` (matrix, after the `qBittorrent` row)
- Modify: `docs/how-it-works/applications.md:110-116` (the "older charts still select the retired label" section, which counts syncthing and thelounge among the three and is now wrong)

**Interfaces:**
- Consumes: the route shape from Task 1, the provider and group from Task 2.
- Produces: nothing later tasks read.

- [ ] **Step 1: Update the routes table row**

`docs/reference/routes.md:69` becomes:

```
| `syncthing.kub.home.ashudev.com` | Syncthing | `syncthing` | `lan-only` then forward-auth, Admins only | Let's Encrypt | no |
```

- [ ] **Step 2: Fix the paragraph that counted it as ungated**

The paragraph beginning **Two routes carry no `lan-only` yet.** now covers one route, `koillection`, and `syncthing` moves into the sentence that already records `thelounge`:

```
**One route carries no `lan-only` yet.** It is the row whose gate reads **none**,
`koillection`: its chart already emits `kube-system-lan-only@kubernetescrd`, the live object predates
the rule, and a sync closes the gap. `thelounge` was the second until 2026-11-19, when its rebuild
added the LAN gate and the authentik forward-auth in front of it, and `syncthing` followed on the same
day. The six public hosts above carry no `lan-only` by design instead, which is the other case
`hack/check-exposure.sh` allows; it passes on both.
```

- [ ] **Step 3: Add the SSO matrix row**

In `docs/reference/sso-applications.md`, after the `qBittorrent` row:

```
| Syncthing | forward-auth gate | configured 2026-11-19 — blueprint `app-syncthing`, Admins only; it has no single sign-on of its own, so the gate is the only user check. Its own GUI login stays until the operator removes it, then Insecure Admin Access is what lets Traefik in | `syncthing.kub.home.ashudev.com` |
```

- [ ] **Step 4: Correct the retired-label section while you are in the neighbourhood**

`docs/how-it-works/applications.md` says three charts still render a required node affinity on the retired `node-type` label and names `monitoring/pihole-exporter`, `syncthing` and `thelounge`. `syncthing` and `thelounge` pin by hostname after their 2026-11-19 rebuilds; only `pihole-exporter` is left (verified with `grep -rn 'node-type' cluster/helm/`). Replace that paragraph with:

```
Most charts pin their pod with `nodeSelector: kubernetes.io/hostname`. One was written before that
rule and still renders a required node affinity on the retired `node-type` label:
`monitoring/pihole-exporter`. `syncthing` and `thelounge` were the other two until their 2026-11-19
rebuilds moved them onto hostname pins; `koillection`, `mktxp` and `news-freshrss` keep the same text
in comments only. This is what each chart asks for, not a statement about where its pod is: the Node
column shows where the pods actually were. Moving `pihole-exporter` onto a hostname pin is chart work.
```

- [ ] **Step 5: Verify the links and the tables**

```bash
python3 hack/check_docs_links.py | tail -1
python3 -m pytest hack/tests -q 2>&1 | tail -1
grep -n 'syncthing' docs/reference/routes.md | head
```

Expected: `0 broken relative links`; all tests pass; the routes row reads `lan-only` then forward-auth.

- [ ] **Step 5: Commit**

```bash
git add docs/reference/routes.md docs/reference/sso-applications.md docs/how-it-works/applications.md
git commit -m "📝 docs: syncthing is behind the forward-auth gate"
```

---

### Task 4: Prove it live, then remove the app login

Nothing in this task is a repo change. Steps 1–2 are read-only and can be done with the MCP; steps 3–4 are the operator's, in this order.

- [ ] **Step 1: Confirm the sync landed**

```
Application syncthing  → Synced / Healthy, revision = the merge commit
kubectl -n authentik get cm authentik-blueprints -o jsonpath='{.data.app-syncthing\.yaml}' | head -3
Route: kubectl -n syncthing get ingressroute syncthing-public -o jsonpath='{.spec.routes[0].middlewares}'
```

Expected: the ConfigMap holds the blueprint, and the live route lists both middlewares.

- [ ] **Step 2: Confirm authentik created the provider and the application**

```
GET https://authentik.kub.home.ashudev.com/api/v3/providers/proxy/?name=syncthing   → one result, mode forward_single
GET https://authentik.kub.home.ashudev.com/api/v3/core/applications/syncthing/      → 200
```

with `Authorization: Bearer $AUTHENTIK_TOKEN` from `~/.config/secrets/homelab.env`. Record the two status codes in the card.

- [ ] **Step 3: Browser checks, gate only**

In a private window: `https://syncthing.kub.home.ashudev.com` lands on authentik. Signed in as an admin, it reaches the WebUI (which still asks for its own password at this point). A non-admin authentik account gets 403.

- [ ] **Step 4: Only now, remove Syncthing's own login**

Settings → GUI → clear the user and the password, tick **Insecure Admin Access**, save. Reload: the WebUI opens behind authentik with no second prompt. Then confirm the break-glass path — `kubectl -n syncthing port-forward svc/syncthing 8384:8384` reaches the GUI with no login, which is acceptable because that path already requires cluster-admin.

- [ ] **Step 5: Record it**

Comment the card with the live evidence: the route's middleware list, the two authentik API codes, the 403, and the click. The card moves to `Validation` when the gate and the removed login are both confirmed; the spec's goals are then met.
