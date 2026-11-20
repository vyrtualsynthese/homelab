# The Lounge LDAP Sign-In Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the forward-auth gate on The Lounge with an authentik LDAP provider and a small LDAP outpost, so a person signs in with their own authentik username and password.

**Architecture:** Three deployments: a blueprint creates the LDAP provider, its application and an LDAP outpost object; the authentik wrapper chart renders the outpost pod and Service plus the one Secret reference; The Lounge's `config.js` binds directly against the outpost at `cn=<username>,ou=users,dc=ldap,dc=goauthentik,dc=io`, and the route drops the gate. The old application and proxy provider are removed only after a verified browser login.

**Tech Stack:** Helm 4, ArgoCD, authentik 2026.8.5 blueprints and REST v3, Traefik 2 CRDs, The Lounge 4.5.2.

**Spec:** `docs/superpowers/specs/2026-11-20-thelounge-ldap-design.md`

## Global Constraints

- Never commit a secret. The outpost token lives in Secret `authentik-ldap`, key `AUTHENTIK_TOKEN`, created by the operator.
- Every workload is pinned with `nodeSelector: kubernetes.io/hostname: nuc0`; every container sets a memory request and limit.
- Traefik CRDs stay `traefik.containo.us/v1alpha1`.
- Every non-public route keeps `kube-system-lan-only@kubernetescrd` first. `hack/check-exposure.sh` must pass.
- The authentik image tag is a single value: `2026.8.5` comes from `.Values.authentik.global.image.tag`; do not write the tag twice.
- Commit messages carry an emoji prefix (`🚀`, `🐛`, `📝`, `🔧`).
- Do not push `develop`; one feature branch `feature/thelounge-ldap`, already checked out, one PR per task group.
- `kubectl` is denied to agents; cluster reads go through the `kubernetes` MCP, cluster writes and the Secret are the operator's.

## Review Focus

- **A missing Secret leaves the outpost pod in `CreateContainerConfigError`.** Expected until the operator creates it; the Deployment is not at fault.
- **A blueprint `!Find` on an object that does not exist rolls back the whole file, silently.** The outpost names the provider in the same file, and PR 3 deletes the provider only after the outpost list has synced.
- **A leftover `searchDN` in `config.js` switches The Lounge to the advanced path** and makes it bind with the default `cn=thelounge,ou=system-users,...` and password `1234`. The `ldap` block must carry `baseDN` and no `searchDN`.
- **`public: true` disables authentication entirely.** It is `false` today; it must stay `false`.
- **Losing `lan-only` exposes the host to the internet** whatever the DNS says. The rendered route must carry it first after the gate is removed.

---

### Task 1: LDAP provider, application and outpost blueprint

**Files:**
- Create: `cluster/helm/authentik/files/app-thelounge-ldap.yaml`
- Test: `hack/tests/test_authentik_blueprints.py` (existing, no edit)

**Interfaces:**
- Consumes: `ashulab-groups` blueprint (`files/groups.yaml`); authentik defaults `default-authentication-flow`, `default-provider-invalidation-flow`, group `authentik Admins`.
- Produces: LDAP provider and application both named/slugged `thelounge-ldap`; outpost `ashulab-ldap`.

- [ ] **Step 1: Run the blueprint test to see the baseline pass**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_authentik_blueprints.py`
Expected: PASS (3 tests). This test reads git only.

- [ ] **Step 2: Create the blueprint file**

Exact content — the order inside the file is load-bearing (the outpost's `!Find` must see the provider):

```yaml
version: 1
metadata:
  name: ashulab-app-thelounge-ldap
entries:
  - model: authentik_blueprints.metaapplyblueprint
    attrs:
      identifiers:
        name: ashulab-groups
      required: true

  # LDAP, not the proxy gate: The Lounge's own identity path is an LDAP bind.
  # A bind needs an authentication flow (it validates the password), not the
  # implicit-consent flow the proxy providers use. The flow's MFA stage skips
  # when no device is enrolled, which is the case today; `mfa_support` stays
  # false until somebody enrols, because it would split a password at a `;`.
  - model: authentik_providers_ldap.ldapprovider
    id: thelounge-ldap-provider
    identifiers:
      name: thelounge-ldap
    attrs:
      name: thelounge-ldap
      authorization_flow: !Find [authentik_flows.flow, [slug, default-authentication-flow]]
      invalidation_flow: !Find [authentik_flows.flow, [slug, default-provider-invalidation-flow]]
      base_dn: dc=ldap,dc=goauthentik,dc=io
      # One human, occasional logins: consult the database on every bind, so a
      # changed password or a revoked account takes effect at once.
      bind_mode: direct
      search_mode: direct
      mfa_support: false

  # A second application on purpose: the existing `thelounge` application still
  # points at the proxy provider the gate uses, and switching it here would
  # break the gate before the route stops asking for it. The slug appears in
  # URLs nobody opens; the display name, icon and host do not change.
  - model: authentik_core.application
    id: thelounge-ldap-app
    identifiers:
      slug: thelounge-ldap
    attrs:
      name: The Lounge
      slug: thelounge-ldap
      provider: !KeyOf thelounge-ldap-provider
      meta_launch_url: https://thelounge.kub.home.ashudev.com/
      meta_icon: https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg/thelounge.svg
      policy_engine_mode: any

  # Admins only, the same access the gate has today. Opening it to a non-admin
  # tier is a second binding, not a chart change.
  - model: authentik_policies.policybinding
    identifiers:
      target: !KeyOf thelounge-ldap-app
      order: 0
    attrs:
      group: !Find [authentik_core.group, [name, authentik Admins]]

  # Creating an outpost generates its service account and token; the token is
  # what the pod in Task 2 authenticates with. It must NOT go into
  # outpost-embedded.yaml: that list is the embedded proxy outpost's.
  - model: authentik_outposts.outpost
    identifiers:
      name: ashulab-ldap
    attrs:
      name: ashulab-ldap
      type: ldap
      providers:
        - !Find [authentik_providers_ldap.ldapprovider, [name, thelounge-ldap]]
      config:
        refresh_interval: minutes=5
        log_level: info
```

- [ ] **Step 3: Prove the outpost names a provider declared in the same file**

Run:

```bash
python3 - <<'EOF'
import re, pathlib
t = pathlib.Path("cluster/helm/authentik/files/app-thelounge-ldap.yaml").read_text()
provider = re.search(r"^      name: (thelounge-ldap)$", t, re.M).group(1)
find = re.search(r"!Find \[authentik_providers_ldap\.ldapprovider, \[name, ([^\]]+)\]\]", t).group(1)
app = re.search(r"^      slug: (thelounge-ldap)$", t, re.M).group(1)
assert provider == find == app, (provider, find, app)
print("ok")
EOF
```

Expected: `ok`.

- [ ] **Step 4: Render the chart's ConfigMap and confirm the file is included**

Run:

```bash
helm dependency build cluster/helm/authentik >/dev/null
helm template test cluster/helm/authentik -f cluster/helm/authentik/values.yaml \
  | grep -c 'authentik_providers_ldap.ldapprovider'
```

Expected: `1` (the blueprint is inlined once into the `authentik-blueprints` ConfigMap).

- [ ] **Step 5: Run the blueprint test again**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_authentik_blueprints.py`
Expected: PASS (3 tests). The old `app-thelounge.yaml` is untouched, so its provider line still resolves.

- [ ] **Step 6: Commit**

```bash
git add cluster/helm/authentik/files/app-thelounge-ldap.yaml
git commit -m "🚀 feat(authentik): add the LDAP provider and outpost for The Lounge"
```

---

### Task 2: LDAP outpost pod, Service, chart bump and the Secret runbook

**Files:**
- Create: `cluster/helm/authentik/templates/ldap-outpost.yaml`
- Modify: `cluster/helm/authentik/Chart.yaml` (`version: 0.10.5` → `0.11.0`)
- Modify: `docs/secrets.md` (one row)
- Modify: `docs/runbooks/authentik-apps.md` (one section)

**Interfaces:**
- Consumes: provider/outpost names from Task 1 (`thelounge-ldap`, `ashulab-ldap`).
- Produces: Service `authentik-ldap` in namespace `authentik`, port 3389 named `ldap`; Secret `authentik-ldap`, key `AUTHENTIK_TOKEN`. Task 3 connects to `ldap://authentik-ldap.authentik.svc.cluster.local:3389`.

- [ ] **Step 1: Create the outpost workload**

Exact content of `cluster/helm/authentik/templates/ldap-outpost.yaml`:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: authentik-ldap
  namespace: {{ .Release.Namespace }}
  labels:
    app.kubernetes.io/name: authentik-ldap
    app.kubernetes.io/instance: {{ .Release.Name }}
    app.kubernetes.io/component: ldap-outpost
spec:
  replicas: 1
  strategy:
    type: Recreate
  selector:
    matchLabels:
      app.kubernetes.io/name: authentik-ldap
      app.kubernetes.io/instance: {{ .Release.Name }}
  template:
    metadata:
      labels:
        app.kubernetes.io/name: authentik-ldap
        app.kubernetes.io/instance: {{ .Release.Name }}
        app.kubernetes.io/component: ldap-outpost
    spec:
      securityContext:
        seccompProfile:
          type: RuntimeDefault
      containers:
        - name: ldap
          # Pinned with the server: both are the same authentik release.
          image: "ghcr.io/goauthentik/ldap:{{ .Values.authentik.global.image.tag }}"
          imagePullPolicy: IfNotPresent
          securityContext:
            allowPrivilegeEscalation: false
            capabilities:
              drop:
                - ALL
          env:
            # In-cluster HTTP on the server Service: no TLS, no hairpin NAT
            # through Traefik and no certificate to trust.
            - name: AUTHENTIK_HOST
              value: http://authentik-server.authentik.svc.cluster.local
            - name: AUTHENTIK_TOKEN
              valueFrom:
                secretKeyRef:
                  name: authentik-ldap
                  key: AUTHENTIK_TOKEN
          ports:
            # 3389 only: the TLS listener (6636) has no certificate on the
            # provider, and a Service port with nothing behind it is a lie.
            - name: ldap
              containerPort: 3389
              protocol: TCP
          # The listener opens once the outpost has fetched its configuration,
          # so a TCP connect is the honest signal.
          livenessProbe:
            tcpSocket:
              port: ldap
            initialDelaySeconds: 10
            periodSeconds: 30
            timeoutSeconds: 5
            failureThreshold: 3
          resources:
            requests:
              cpu: 10m
              memory: 64Mi
            limits:
              memory: 128Mi
      nodeSelector:
        kubernetes.io/hostname: nuc0
---
apiVersion: v1
kind: Service
metadata:
  name: authentik-ldap
  namespace: {{ .Release.Namespace }}
  labels:
    app.kubernetes.io/name: authentik-ldap
    app.kubernetes.io/instance: {{ .Release.Name }}
spec:
  type: ClusterIP
  selector:
    app.kubernetes.io/name: authentik-ldap
    app.kubernetes.io/instance: {{ .Release.Name }}
  ports:
    - name: ldap
      port: 3389
      targetPort: ldap
      protocol: TCP
```

- [ ] **Step 2: Bump the chart version**

`cluster/helm/authentik/Chart.yaml`: `version: 0.10.5` → `version: 0.11.0`. Leave `appVersion` and the dependency pin alone.

- [ ] **Step 3: Render and assert the four load-bearing fields**

Run:

```bash
helm dependency build cluster/helm/authentik >/dev/null
helm template test cluster/helm/authentik -f cluster/helm/authentik/values.yaml > /tmp/opencode/authentik.yaml
python3 - <<'EOF'
import yaml
docs = [d for d in yaml.safe_load_all(open("/tmp/opencode/authentik.yaml")) if d]
dep = next(d for d in docs if d["kind"] == "Deployment" and d["metadata"]["name"] == "authentik-ldap")
c = dep["spec"]["template"]["spec"]["containers"][0]
assert c["image"] == "ghcr.io/goauthentik/ldap:2026.8.5", c["image"]
assert c["ports"][0]["containerPort"] == 3389
env = {e["name"]: e for e in c["env"]}
assert env["AUTHENTIK_HOST"]["value"] == "http://authentik-server.authentik.svc.cluster.local"
assert env["AUTHENTIK_TOKEN"]["valueFrom"]["secretKeyRef"] == {"name": "authentik-ldap", "key": "AUTHENTIK_TOKEN"}
svc = next(d for d in docs if d["kind"] == "Service" and d["metadata"]["name"] == "authentik-ldap")
assert svc["spec"]["ports"][0]["port"] == 3389
print("ok")
EOF
```

Expected: `ok`.

- [ ] **Step 4: Record the Secret in the inventory**

Add to the table in `docs/secrets.md`, keeping the existing column order:

| Secret | Namespace | Keys | Read by | Origin |
|---|---|---|---|---|
| `authentik-ldap` | `authentik` | `AUTHENTIK_TOKEN` — the `ashulab-ldap` outpost's token | the LDAP outpost Deployment `authentik-ldap` | created by the operator from the outpost's generated token; the runbook holds the one-liner |

- [ ] **Step 5: Add the runbook section**

Append to `docs/runbooks/authentik-apps.md` a section `## The LDAP outpost (The Lounge)`, stating: the
outpost object is created by `files/app-thelounge-ldap.yaml`; authentik generates its service account
and token when the outpost is created; the token is read once and stored in the Secret. Exact
one-liner:

```bash
set -a; . ~/.config/secrets/homelab.env; set +a
API=https://authentik.kub.home.ashudev.com/api/v3
TOK=$(curl -s -H "Authorization: Bearer $AUTHENTIK_TOKEN" \
  "$API/outposts/instances/?search=ashulab-ldap" \
  | jq -r '.results[] | select(.name=="ashulab-ldap") | .token_identifier')
KEY=$(curl -s -H "Authorization: Bearer $AUTHENTIK_TOKEN" "$API/core/tokens/$TOK/view_key/" | jq -r '.key')
kubectl -n authentik create secret generic authentik-ldap \
  --from-literal=AUTHENTIK_TOKEN="$KEY" --dry-run=client -o yaml | kubectl apply -f -
unset KEY
```

Also state the two checks that mean the outpost is up: `GET /outposts/instances/?search=ashulab-ldap`
lists it with `providers` containing `thelounge-ldap`, and its `health/` endpoint returns a record
whose `version` is `2026.8.5`.

- [ ] **Step 6: Run the suite and the exposure check**

Run:

```bash
uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests
hack/check-exposure.sh --only authentik
```

Expected: pytest PASS; `check-exposure` exit 0 (the LDAP outpost adds no route).

- [ ] **Step 7: Commit**

```bash
git add cluster/helm/authentik/templates/ldap-outpost.yaml cluster/helm/authentik/Chart.yaml \
        docs/secrets.md docs/runbooks/authentik-apps.md
git commit -m "🚀 feat(authentik): run the LDAP outpost and document its token"
```

- [ ] **Step 8: Open PR 1 and stop at the gate**

Push `feature/thelounge-ldap`, open a PR against `develop` titled
`🚀 feat(authentik): LDAP outpost for The Lounge` whose body links the design doc and lists the gate:
the operator merges, creates Secret `authentik-ldap`, and confirms the outpost is healthy. **Do not
start Task 3 until that is confirmed.**

---

### Task 3: The Lounge binds against the outpost; the gate leaves the route

**Files:**
- Modify: `cluster/helm/thelounge/config.js` (the `ldap` block, lines ~368-456)
- Modify: `cluster/helm/thelounge/values.yaml:56-92` (middlewares, `extraEnv` comment)
- Modify: `cluster/helm/thelounge/Chart.yaml` (`version: 0.2.1` → `0.3.0`)
- Modify: `cluster/helm/thelounge/README.md` (the gate section)
- Modify: `docs/reference/sso-applications.md`, `docs/reference/routes.md`, `docs/reference/chart-notes.md`

**Interfaces:**
- Consumes: Service `authentik-ldap.authentik.svc.cluster.local:3389` (Task 2).
- Produces: a route with middlewares `lan-only`, `security` only.

- [ ] **Step 1: Replace the `ldap` block in `config.js`**

Delete the whole commented example from `// ## LDAP support` to the end of the `ldap: { ... }` object
and write:

```js
    // ## LDAP support

    // authentik serves the directory; a login is an LDAP bind as the person, so
    // the password is theirs and the account is keyed by their username. The
    // outpost lives in the cluster, and `baseDN` alone selects the simple path:
    // adding `searchDN` would make The Lounge bind a system account first.
    ldap: {
        enable: true,
        url: "ldap://authentik-ldap.authentik.svc.cluster.local:3389",
        tlsOptions: {},
        // authentik exposes users at cn=<username>,ou=users,<base DN>.
        primaryKey: "cn",
        baseDN: "ou=users,dc=ldap,dc=goauthentik,dc=io",
    },
```

- [ ] **Step 2: Prove the simple path is the one selected, and `public` stays off**

Run:

```bash
python3 - <<'EOF'
import re, pathlib
t = pathlib.Path("cluster/helm/thelounge/config.js").read_text()
block = t[t.index("    ldap: {"):]
block = block[:block.index("\n    },")]
assert "baseDN" in block and "searchDN" not in block.replace("// ", ""), block
assert re.search(r"^\s+public: false,", t, re.M)
assert "url: \"ldap://authentik-ldap.authentik.svc.cluster.local:3389\"" in block
print("ok")
EOF
```

Expected: `ok`.

- [ ] **Step 3: Drop the gate from the middleware list**

In `cluster/helm/thelounge/values.yaml`, the list becomes:

```yaml
  # `lan-only` only: The Lounge now authenticates people itself, with LDAP
  # against the authentik outpost (`config.js`, `ldap` block), so the forward-auth
  # gate would be a second prompt that carries no identity. The hostname answers
  # whether or not it resolves publicly, so `lan-only` is the boundary and the
  # app's own login is the credential.
  middlewares:
    - name: lan-only
      namespace: kube-system
    - name: security
      namespace: kube-system
```

Also replace the `extraEnv` comment ("The Lounge currently needs none: it has no LDAP or OIDC client
of its own.") with: the app speaks LDAP to the outpost, and that connection needs no credential of
its own — the person's own password is the bind, so the list stays empty.

- [ ] **Step 4: Render and assert the route and the config**

Run:

```bash
helm template test cluster/helm/thelounge -f cluster/helm/thelounge/values.yaml > /tmp/opencode/thelounge.yaml
python3 - <<'EOF'
import yaml
docs = [d for d in yaml.safe_load_all(open("/tmp/opencode/thelounge.yaml")) if d]
route = next(d for d in docs if d["kind"] == "IngressRoute"
             and d["metadata"]["name"] == "thelounge-public")
names = [m["name"] for m in route["spec"]["routes"][0]["middlewares"]]
assert names == ["kube-system-lan-only@kubernetescrd", "kube-system-security@kubernetescrd"], names
cm = next(d for d in docs if d["kind"] == "ConfigMap" and d["metadata"]["name"] == "thelounge")
assert 'ldap://authentik-ldap.authentik.svc.cluster.local:3389' in cm["data"]["config.js"]
assert 'authentik-ldap' in cm["data"]["config.js"]
print("ok")
EOF
```

Expected: `ok`.

- [ ] **Step 5: Bump the chart and update its README**

`Chart.yaml`: `version: 0.2.1` → `0.3.0`. In `README.md`, the object table row for `thelounge-public`
becomes `websecure`, `lan-only` then `security`; the "The gate" section becomes "Signing in", stating
the LDAP path, that the previous local passwords no longer work, that MFA must be appended if ever
enrolled, and that the outpost is `authentik-ldap` in namespace `authentik`.

- [ ] **Step 6: Update the reference pages**

- `docs/reference/sso-applications.md`: the The Lounge row's mechanism becomes `LDAP, through the
  authentik outpost`, the state says the gate is gone and the app's own login is the credential, and
  a short paragraph joins the mechanism notes with the three traps: `default-authentication-flow` is
  the bind flow, `mfa_support` must be flipped with the first enrolment, and the bind DN is
  `cn=<username>,ou=users,dc=ldap,dc=goauthentik,dc=io`.
- `docs/reference/routes.md`: the protection column becomes `lan-only` then the app's own LDAP login.
- `docs/reference/chart-notes.md`: in the The Lounge section, replace the gate paragraph; the closing
  "Debt: ... the LDAP path to one sign-in is a follow-up card" line is removed.

- [ ] **Step 7: Run the suite and the exposure check**

Run:

```bash
uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests
hack/check-exposure.sh --only thelounge
```

Expected: pytest PASS; `check-exposure` exit 0, with `lan-only` first on both routes.

- [ ] **Step 8: Commit and open PR 2**

```bash
git add cluster/helm/thelounge docs/reference
git commit -m "🚀 feat(thelounge): sign in through authentik's LDAP directory"
git push origin feature/thelounge-ldap
```

Open the PR against `develop` titled `🚀 feat(thelounge): sign in through LDAP` with the design doc,
the rendered-route assertion and the check output. The body states the gate: the operator signs in
with the authentik username and password and confirms the account is the right one with its networks.
**Do not start Task 4 until the browser check passes.**

---

### Task 4: Retire the gate's application and provider

**Files:**
- Delete: `cluster/helm/authentik/files/app-thelounge.yaml`
- Modify: `cluster/helm/authentik/files/outpost-embedded.yaml` (remove the meta-apply and the `!Find`)
- Modify: `cluster/helm/authentik/Chart.yaml` (`0.11.0` → `0.11.1`)
- Modify: `docs/runbooks/authentik-apps.md` (the deletion procedure)

**Interfaces:**
- Consumes: the old application slug `thelounge` and proxy provider name `thelounge`.
- Produces: one remaining Lounge application (`thelounge-ldap`) and no proxy provider for it.

- [ ] **Step 1: Confirm the old objects are still the ones named**

Run:

```bash
set -a; . ~/.config/secrets/homelab.env; set +a
API=https://authentik.kub.home.ashudev.com/api/v3
curl -s -H "Authorization: Bearer $AUTHENTIK_TOKEN" "$API/core/applications/thelounge/" | jq -r '.slug,.provider'
curl -s -H "Authorization: Bearer $AUTHENTIK_TOKEN" "$API/providers/proxy/?search=thelounge" | jq -r '.results[] | "\(.pk) \(.name)"'
```

Expected: `thelounge` and the provider pk of the proxy (82 as of 2026-11-20), and one provider line
`<pk> thelounge`.

- [ ] **Step 2: Remove the old blueprint file and its outpost lines**

Delete `cluster/helm/authentik/files/app-thelounge.yaml`. In `outpost-embedded.yaml` remove both
blocks that name it:

```yaml
  - model: authentik_blueprints.metaapplyblueprint
    attrs:
      identifiers:
        name: ashulab-app-thelounge
      required: true
```

and

```yaml
        - !Find [authentik_providers_proxy.proxyprovider, [name, thelounge]]
```

- [ ] **Step 3: Prove the pair is consistent**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_authentik_blueprints.py`
Expected: PASS. This is the test that fails when one side of the pair is left behind.

- [ ] **Step 4: Bump the chart and document the deletions**

`Chart.yaml`: `0.11.0` → `0.11.1`. In `docs/runbooks/authentik-apps.md`, under the LDAP outpost
section, add "Retiring the gate's objects": the two API deletions, and why the order is app then
provider and why it happens after the commit has synced (blueprints do not prune; a `!Find` on a
missing provider rolls the file back).

```bash
# After the commit that removes app-thelounge.yaml has synced:
curl -s -o /dev/null -w '%{http_code}\n' -X DELETE \
  -H "Authorization: Bearer $AUTHENTIK_TOKEN" "$API/core/applications/thelounge/"
PK=$(curl -s -H "Authorization: Bearer $AUTHENTIK_TOKEN" "$API/providers/proxy/?search=thelounge" \
  | jq -r '.results[] | select(.name=="thelounge") | .pk')
curl -s -o /dev/null -w '%{http_code}\n' -X DELETE \
  -H "Authorization: Bearer $AUTHENTIK_TOKEN" "$API/providers/proxy/$PK/"
```

Expected: `204` twice.

- [ ] **Step 5: Commit and open PR 3**

```bash
git add cluster/helm/authentik docs/runbooks/authentik-apps.md
git commit -m "♻️ refactor(authentik): retire The Lounge's forward-auth provider"
git push origin feature/thelounge-ldap
```

Open the PR against `develop`; its body says the browser login passed and lists the two deletions to
run after the merge syncs.

- [ ] **Step 6: After the merge and the sync, delete the objects and verify the end state**

Run the two `DELETE` calls from the runbook; then:

```bash
curl -s -H "Authorization: Bearer $AUTHENTIK_TOKEN" "$API/core/applications/?search=lounge" | jq -r '.results[] | "\(.slug) \(.name)"'
curl -s -H "Authorization: Bearer $AUTHENTIK_TOKEN" "$API/providers/ldap/?search=thelounge" | jq -r '.results[] | "\(.name) \(.base_dn)"'
curl -s -H "Authorization: Bearer $AUTHENTIK_TOKEN" "$API/outposts/instances/?search=ashulab-ldap" | jq -r '.results[] | "\(.name) \(.providers)"'
```

Expected: one application (`thelounge-ldap The Lounge`), one provider (`thelounge-ldap dc=ldap,...`),
one outpost whose `providers` holds that provider.

- [ ] **Step 7: Close the card**

Move task 424 to `Validation` with a comment carrying the branch, the three PR links, and the
verification output (pytest, check-exposure, the outpost health, the browser login).
