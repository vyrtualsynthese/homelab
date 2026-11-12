# Tautulli chart refactor — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the Tautulli Helm chart on the repo's contract so the app starts on a working config
claim and is reachable on the LAN only.

**Architecture:** One self-contained chart in `cluster/helm/media-stack/tautulli` shaped like
`autobrr`/`prowlarr`: unprefixed `app.*` helpers, named values for routing, storage, env, probes and
resources, one Traefik `IngressRoute` on the `web` entry point at `tautulli.kub.local`. The chart
creates the config claim; the image's own init chowns it to `PUID`/`PGID` 1100.

**Tech Stack:** Helm 4 (`apiVersion: v2` chart), Traefik v2 CRDs (`traefik.containo.us/v1alpha1`),
Kubernetes Deployment/Service/ServiceAccount/PVC, image `ghcr.io/tautulli/tautulli:v2.19.1`.

**Spec:** `docs/superpowers/specs/2026-11-13-tautulli-chart-refactor-design.md`

## Global Constraints

- Chart contract: `docs/charts/README.md`. `appVersion` must equal `image.tag`; every object name
  comes from `app.*` helpers; no tabs anywhere; `helm lint` and `helm template` clean.
- Ingress is a Traefik `IngressRoute`; the API group is a value. Never `networking.k8s.io/v1`.
- Local-only: `entryPoints: [web]`, host `tautulli.kub.local`, `tls.enabled: false`, no middlewares.
- Image: `ghcr.io/tautulli/tautulli:v2.19.1` (project's own registry; GHCR preferred over Docker Hub).
- `strategy: Recreate`, claim `<fullname>-config` = `tautulli-config`, `local-path`, `128Mi`.
- `nodeSelector: kubernetes.io/hostname: streaming`.
- Container security: `allowPrivilegeEscalation: false`, `capabilities.drop: [ALL]` plus
  `CHOWN`, `SETUID`, `SETGID` (the image's root init needs exactly those); pod
  `seccompProfile: RuntimeDefault`; no `runAsNonRoot`, no `runAsUser`.
- Probes: startup and liveness on `/status`; readiness disabled.
- No Prometheus exporter: none is maintained upstream.

## Review Focus

- **`/status` under auth.** Tautulli can require a login for the UI. If the probe path needed auth it
  would 302/401 and the pod would restart-loop. `/status` has no `requireAuth` decorator in
  `plexpy/webserve.py`, so it answers 200 regardless — the task verifies the rendered probe points at
  exactly that path and not `/`.
- **The config claim being the only writable path.** Every setting lives in `/config`; a missing or
  wrongly-mounted claim shows up as a pod that starts and then fails to save. The task verifies the
  claim is rendered, mounted at `/config`, and that the name is derived from the release.
- **Root init plus dropped capabilities.** Dropping `CHOWN`/`SETUID`/`SETGID` breaks `start.sh`
  silently at container start. The task verifies the rendered `securityContext` keeps exactly those
  three.
- **Port mismatch.** The old chart advertised `32400` on the Service while the app listens on `8181`;
  the Service and the IngressRoute must both resolve to the container's own port.
- **Stale public objects.** Removing the public templates does not delete the live
  `IngressRoute`s, and the host is in a public zone. The plan keeps the deletion as an explicit
  post-sync step rather than implying the sync does it.

---

### Task 1: Rebuild the chart

**Files:**
- Rewrite: `cluster/helm/media-stack/tautulli/Chart.yaml`
- Rewrite: `cluster/helm/media-stack/tautulli/values.yaml`
- Rewrite: `cluster/helm/media-stack/tautulli/templates/_helpers.tpl`
- Rewrite: `cluster/helm/media-stack/tautulli/templates/deployment.yaml`
- Rewrite: `cluster/helm/media-stack/tautulli/templates/service.yaml`
- Create: `cluster/helm/media-stack/tautulli/templates/serviceaccount.yaml`
- Create: `cluster/helm/media-stack/tautulli/templates/persistentvolumeclaim.yaml`
- Create: `cluster/helm/media-stack/tautulli/templates/ingressroute.yaml`
- Create: `cluster/helm/media-stack/tautulli/templates/NOTES.txt`
- Delete: `templates/ingress-public.yaml`, `templates/ingress-public-redirect.yaml`,
  `templates/middleware.yaml`, `templates/middleware-security.yaml`, `templates/tls-option.yaml`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: the values schema above; helpers `app.name`, `app.fullname`, `app.labels`,
  `app.selectorLabels`, `app.serviceAccountName`, `app.configClaimName`, `app.containerPort`, and
  the runtime names `tautulli` (Deployment/Service/SA/IngressRoute) and `tautulli-config` (claim).

- [ ] **Step 1: Write the chart files**

`Chart.yaml`: `version: 1.0.0` (values schema is not backward compatible), `appVersion: "v2.19.1"`,
one-line description, `icon` from `data/interfaces/default/images/logo.png` at the release tag,
`home`, `sources`, `keywords`.

`_helpers.tpl`: copy `cluster/helm/media-stack/autobrr/templates/_helpers.tpl` verbatim.

`values.yaml`: the shape in the spec, with doc comments explaining each non-obvious default (image
choice, local-only route, root init, `/status` probes, readiness off, unmeasured resources).

`deployment.yaml`, `service.yaml`, `serviceaccount.yaml`, `persistentvolumeclaim.yaml`,
`ingressroute.yaml`: copy the corresponding `prowlarr` templates, then remove the exportarr sidecar
and its annotations block, leaving the plain env rendering (`env` map + `extraEnv` + `envFrom`).

`NOTES.txt`: fullname, the `http://tautulli.kub.local/` URL with a note that it resolves internally,
the claim name, and the port-forward alternative when `ingressRoute.enabled` is false.

- [ ] **Step 2: Delete the public-ingress templates and the dead middleware/TLSOption**

```bash
git rm cluster/helm/media-stack/tautulli/templates/ingress-public.yaml \
       cluster/helm/media-stack/tautulli/templates/ingress-public-redirect.yaml \
       cluster/helm/media-stack/tautulli/templates/middleware.yaml \
       cluster/helm/media-stack/tautulli/templates/middleware-security.yaml \
       cluster/helm/media-stack/tautulli/templates/tls-option.yaml
```

- [ ] **Step 3: Verify the render**

Run:
```bash
helm lint cluster/helm/media-stack/tautulli --namespace media-stack
helm template tautulli cluster/helm/media-stack/tautulli --namespace media-stack > /tmp/tautulli.yaml
grep -c '^kind:' /tmp/tautulli.yaml
```
Expected: lint reports `1 chart(s) linted, 0 chart(s) failed`; the template command exits 0; `kind:`
count is 5 (Deployment, Service, ServiceAccount, PVC, IngressRoute).

- [ ] **Step 4: Verify the contract greps**

```bash
cd cluster/helm/media-stack/tautulli
grep -rnP '\t' templates/ || echo "no tabs"
grep -rn 'latest' values.yaml || echo "no floating tags"
grep -rn 'networking.k8s.io\|tautulli-config2\|node-type' templates/ values.yaml || echo "no leftovers"
grep -rn "image.tag\|appVersion" Chart.yaml values.yaml
```
Expected: `no tabs`, `no floating tags`, `no leftovers`; `appVersion` and `image.tag` both `v2.19.1`.

- [ ] **Step 5: Verify the five focus behaviours in the rendered output**

```bash
grep -A3 'kind: IngressRoute' /tmp/tautulli.yaml | head
grep -n 'kub.local\|entryPoints' /tmp/tautulli.yaml
grep -n 'claimName\|mountPath: /config' /tmp/tautulli.yaml
grep -n 'path: /status\|readinessProbe' /tmp/tautulli.yaml
grep -n -A8 'capabilities' /tmp/tautulli.yaml
grep -n 'containerPort\|port: 8181' /tmp/tautulli.yaml
```
Expected: one IngressRoute on `web` at `tautulli.kub.local`; `claimName: tautulli-config` and
`mountPath: /config`; two `/status` probes and no `readinessProbe` block; capabilities drop `ALL` and
add `CHOWN`, `SETUID`, `SETGID`; `containerPort: 8181` and the Service port `8181`.

- [ ] **Step 6: Commit**

```bash
git add cluster/helm/media-stack/tautulli
git commit -m "🔧 chore(tautulli): rebuild the chart on the reference shape, local-only"
```

### Task 2: Chart README

**Files:**
- Create: `cluster/helm/media-stack/tautulli/README.md`

**Interfaces:**
- Consumes: the values schema from Task 1.
- Produces: documentation only.

- [ ] **Step 1: Write the README** with the sections the other charts use: upstream links, how it is
  deployed here (`helm template` command), a values table, the image choice and why GHCR, the
  local-only route, the claim and the UID story, the probes table with the readiness exception, the
  log-level exception, and uninstall behaviour.

- [ ] **Step 2: Check the claims against the chart**

```bash
grep -n 'tautulli.kub.local\|v2.19.1\|tautulli-config' cluster/helm/media-stack/tautulli/README.md
```
Expected: the README's host, tag and claim name match `values.yaml`.

- [ ] **Step 3: Commit**

```bash
git add cluster/helm/media-stack/tautulli/README.md
git commit -m "📝 docs(tautulli): document the chart, its probes and its local-only route"
```

### Task 3: Record Tautulli in the chart contract

**Files:**
- Modify: `docs/charts/README.md` (probes/hardening table, "What good looks like")

**Interfaces:**
- Consumes: the chart and README from Tasks 1–2.
- Produces: documentation only.

- [ ] **Step 1: Add the table row** `| tautulli | yes, on /status | capabilities dropped except CHOWN/SETUID/SETGID for the root init; seccomp; no privilege escalation | no — the image starts as root, then drops to 1100 |`.

- [ ] **Step 2: Add the "what good looks like" paragraph** naming Tautulli's two deliberate
  differences: no public route at all (local-only, `web`, no TLS) and readiness disabled because the
  dependency state is only in a JSON body.

- [ ] **Step 3: Commit**

```bash
git add docs/charts/README.md
git commit -m "📝 docs(charts): record tautulli in the chart contract"
```
