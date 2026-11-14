# Authentik phase 1 — repave the instance

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Authentik runs on chart `2026.8.5` with an empty, dedicated database, serves both
hostnames with valid certificates, applies its configuration from blueprints in git, and the
operator can sign in as the local administrator.

**Architecture:** The existing wrapper chart (pattern D) is repaved: dependency and image both
`2026.8.5`, placement on `nuc0`, the chart's plain Ingress replaced by two Traefik
`IngressRoute`s (internal and public names), one ConfigMap of blueprints mounted into the
worker, and a PostSync hook that restarts the worker so a committed blueprint applies during the
sync. Secrets stay in `authentik-env`, created by hand.

**Tech Stack:** K3s, ArgoCD, Helm 4, Traefik v2 CRDs, PostgreSQL 18.7 (namespace `databases`),
authentik 2026.8.5.

**Spec:** `docs/superpowers/specs/2026-11-14-authentik-sso-design.md`

## Global Constraints

- Chart dependency and server image are both exactly `2026.8.5`; nothing floats.
- Namespace `authentik`; hostnames `authentik.kub.home.ashudev.com` (internal) and
  `authentik.ashudev.com` (public).
- Database host `postgres.databases.svc.cluster.local`, role and database `authentik`.
- Workload pinned with `kubernetes.io/hostname: nuc0`; no `node-type` label anywhere.
- Ingress is Traefik v2 CRDs only (`traefik.containo.us/v1alpha1`); no `networking.k8s.io`.
- Live middlewares to reference: `kube-system-redirect-https@kubernetescrd` and
  `kube-system-security@kubernetescrd`. Nothing else in `kube-system` is assumed.
- Every container sets memory requests and limits; CPU requests 10m.
- No secret value is written to git, printed in full, or placed in a commit message.
- `cluster/argocd/**` is CRLF; do not touch it. Everything in this plan is LF.
- Commit messages use an emoji prefix; never commit to `main` or `develop`.

## Review Focus

1. **Stale environment key beats the values file.** `envFrom` sources are applied after the
   chart's own Secret, so an old `AUTHENTIK_POSTGRESQL__HOST` in `authentik-env` silently wins
   over the host set in values, and the server logs a connection timeout. Task 2 inspects the
   secret's key names and sets or removes every `AUTHENTIK_POSTGRESQL__*` key.
2. **Public router before its DNS record.** A router whose name does not resolve fails ACME
   validation and spends Let's Encrypt's shared failure budget. Task 3 gates the sync on
   `dig +short authentik.ashudev.com @1.1.1.1` returning `80.14.57.209`; the internal router has
   its own certificate.
3. **Blueprint silently not applied.** A wrong ConfigMap name or mount path leaves no `users`
   group and no error the login cares about. Task 1 asserts the mount in the render; Task 4
   asserts the worker log shows the apply and the group exists.
4. **Stale plain Ingress survives the sync.** Removing the chart's Ingress does not delete the
   live object while pruning is off, so the old object keeps routing. Task 3 deletes
   `ingress/authentik-server` explicitly and Task 4 asserts none remains.
5. **Cookie domain wrong or missing.** Without `AUTHENTIK_COOKIE_DOMAIN=.ashudev.com` a sign-in
   on one hostname is anonymous on the other and the two names loop. Task 4 signs in on the
   internal name and opens the public name in the same browser.

---

### Task 1: Repave the chart, both hostnames, blueprints, worker hook

**Files:**
- Modify: `cluster/helm/authentik/Chart.yaml`
- Modify: `cluster/helm/authentik/values.yaml`
- Create: `cluster/helm/authentik/templates/ingressroute.yaml`
- Create: `cluster/helm/authentik/templates/tls-option.yaml`
- Create: `cluster/helm/authentik/templates/blueprints-configmap.yaml`
- Create: `cluster/helm/authentik/templates/worker-reload-hook.yaml`
- Create: `cluster/helm/authentik/files/users.yaml`
- Modify: `.gitignore`
- Modify: `.github/renovate.json`

**Interfaces:**
- Produces: Deployment `authentik-server` and `authentik-worker` (release name `authentik`),
  Service `authentik-server` port name `http`, ConfigMap `authentik-blueprints`, TLSOption
  `authentik-tlsoption`, hook objects `authentik-worker-reload` (ServiceAccount, Role,
  RoleBinding, Job).
- Consumes: nothing from earlier tasks.

- [ ] **Step 1: Pin the dependency**

`Chart.yaml`: dependency `version: 2026.8.5`; bump the wrapper's own `version` to `0.2.0`.

- [ ] **Step 2: Correct the values**

In `values.yaml`, keep the email block, `error_reporting`, `postgresql.enabled: false`,
and no `redis` block at all: 2026.8 dropped Redis. Change or add exactly:

```yaml
authentik:
  authentik:
    postgresql:
      host: "postgres.databases.svc.cluster.local"
      name: "authentik"
      user: "authentik"
  global:
    image:
      tag: "2026.8.5"
    nodeSelector:
      kubernetes.io/hostname: nuc0
    env:
      - name: AUTHENTIK_COOKIE_DOMAIN
        value: ".ashudev.com"
      - name: AUTHENTIK_WEB__BASE_URL
        value: "https://authentik.kub.home.ashudev.com"
  server:
    ingress:
      enabled: false
    resources:
      requests: {cpu: 10m, memory: 512Mi}
      limits: {memory: 1Gi}
  worker:
    resources:
      requests: {cpu: 10m, memory: 512Mi}
      limits: {memory: 1Gi}
  blueprints:
    configMaps:
      - authentik-blueprints
  serviceAccount:
    create: false
```

Delete the whole `global.affinity` block (it targets the retired `node-type` label and the
chart's current affinity shape does not read it). Keep `global.envFrom` with `authentik-env`.
Leave the chart's default probes unchanged: they already probe `/-/health/live/` and
`/-/health/ready/` on the right ports.

- [ ] **Step 3: Replace the chart Ingress with two IngressRoutes**

`templates/ingressroute.yaml` renders four objects: for each hostname one `websecure` router and
one `web` router that redirects to it. Exact shape:

```yaml
apiVersion: traefik.containo.us/v1alpha1
kind: IngressRoute
metadata:
  name: authentik-kub-home
  namespace: {{ .Release.Namespace }}
spec:
  entryPoints: [websecure]
  routes:
    - match: Host(`authentik.kub.home.ashudev.com`)
      kind: Rule
      services:
        - name: authentik-server
          port: http
      middlewares:
        - name: kube-system-security@kubernetescrd
  tls:
    certResolver: myresolver
    options:
      name: authentik-tlsoption
```

The public object is identical with name `authentik-public` and host `authentik.ashudev.com`.
Each redirect object uses `entryPoints: [web]`, the same host and service, and
`middlewares: [{name: kube-system-redirect-https@kubernetescrd}]`.

`templates/tls-option.yaml` copies `cluster/helm/syncthing/templates/tls-option.yaml` verbatim,
renamed `authentik-tlsoption`.

- [ ] **Step 4: Mount the blueprints**

`templates/blueprints-configmap.yaml`:

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: authentik-blueprints
  namespace: {{ .Release.Namespace }}
data:
{{- range $path, $_ := .Files.Glob "files/*.yaml" }}
  {{ base $path }}: |-
{{ $.Files.Get $path | indent 4 }}
{{- end }}
```

`files/users.yaml`:

```yaml
version: 1
metadata:
  name: ashulab-users
entries:
  - model: authentik_core.group
    id: users
    identifiers:
      name: users
    attrs:
      name: users
      is_superuser: false
```

- [ ] **Step 5: Add the worker reload hook**

`templates/worker-reload-hook.yaml` renders a ServiceAccount, a Role and RoleBinding granting
`list` and `delete` on `pods`, and a Job:

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: authentik-worker-reload
  namespace: {{ .Release.Namespace }}
  annotations:
    argocd.argoproj.io/hook: PostSync
    argocd.argoproj.io/hook-delete-policy: BeforeHookCreation
spec:
  backoffLimit: 1
  template:
    spec:
      serviceAccountName: authentik-worker-reload
      restartPolicy: Never
      containers:
        - name: kubectl
          image: registry.k8s.io/kubectl:v1.30.1
          command: ["kubectl", "-n", "{{ .Release.Namespace }}", "delete", "pod",
                    "-l", "app.kubernetes.io/component=worker", "--ignore-not-found"]
          resources:
            requests: {cpu: 10m, memory: 32Mi}
            limits: {memory: 64Mi}
```

- [ ] **Step 6: Keep Renovate from splitting the pair again**

In `.github/renovate.json`, append one entry to `packageRules`:

```json
{
  "groupName": "authentik",
  "matchPackageNames": ["/authentik/"]
}
```

Validate: `jq . .github/renovate.json >/dev/null` exits 0. The rule groups the Helm chart
dependency and `ghcr.io/goauthentik/server`, which is how the current mismatch happened.

- [ ] **Step 7: Keep the built dependency out of git**

Append to `.gitignore`:

```
# Built by ArgoCD at sync time
cluster/helm/authentik/charts/
cluster/helm/authentik/Chart.lock
```

- [ ] **Step 8: Render and assert**

```bash
cd cluster/helm/authentik && helm dependency build && cd -
helm template authentik cluster/helm/authentik -n authentik \
  -f cluster/helm/authentik/values.yaml > /tmp/opencode/authentik-render.yaml
```

Expected: no output from the loop below; each `grep` succeeds.

```bash
R=/tmp/opencode/authentik-render.yaml
test "$(grep -c '^kind: Ingress$' $R)" -eq 0
test "$(grep -c '^kind: IngressRoute$' $R)" -eq 4
test "$(grep -c '^kind: TLSOption$' $R)" -eq 1
test "$(grep -c 'node-type' $R)" -eq 0
grep -q 'kubernetes.io/hostname: nuc0' $R
grep -q 'image: ghcr.io/goauthentik/server:2026.8.5' $R
grep -q 'AUTHENTIK_POSTGRESQL__HOST: "cG9zdGdyZXMuZGF0YWJhc2VzLnN2Yy5jbHVzdGVyLmxvY2Fs"' $R
grep -q 'name: AUTHENTIK_COOKIE_DOMAIN' $R
grep -q 'authentik.kub.home.ashudev.com' $R
grep -q 'authentik.ashudev.com' $R
grep -q 'name: authentik-blueprints' $R
grep -q 'mountPath: /blueprints/mounted/cm-authentik-blueprints' $R
grep -q 'argocd.argoproj.io/hook: PostSync' $R
test "$(grep -c '^kind: ClusterRole' $R)" -eq 0
```

- [ ] **Step 9: Commit**

```bash
git add cluster/helm/authentik .gitignore .github/renovate.json
git commit -m "✨ feat(authentik): repave on 2026.8.5 with two hostnames and blueprints"
```

### Task 2: Database, role and runtime secrets

**Files:**
- Modify: `docs/runbooks/postgres-add-database.md` (log row)
- Modify: `docs/secrets.md` (authentik row)

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: Secret `databases/pg-authentik` key `password`; Secret `authentik/authentik-env`
  keys `AUTHENTIK_POSTGRESQL__PASSWORD`, `AUTHENTIK_POSTGRESQL__HOST`, `AUTHENTIK_SECRET_KEY`,
  `AUTHENTIK_BOOTSTRAP_PASSWORD`; role and database `authentik`.

- [ ] **Step 1: Confirm preconditions**

```bash
kubectl -n databases rollout status statefulset/postgres --timeout=120s
grep -n 'authentik' cluster/helm/databases/postgres/values.yaml   # must appear under consumerNamespaces
```

- [ ] **Step 2: Inspect the secret's key names (never its values)**

```bash
kubectl -n authentik get secret authentik-env -o json \
  | jq -r '.data | keys[]' | sort
```

Note every `AUTHENTIK_POSTGRESQL__*` key. Any such key overrides the values file and must be
rewritten in Step 4; `AUTHENTIK_SECRET_KEY` is expected and is rotated there.

- [ ] **Step 3: Create the database secret and the role**

Run as one block in a single shell (values live only in this session):

```bash
PW=$(openssl rand -hex 24)
kubectl -n databases create secret generic pg-authentik --from-literal=password="$PW"
kubectl -n databases exec -i statefulset/postgres -c postgres -- \
  psql -U postgres -d postgres -v ON_ERROR_STOP=1 <<SQL
CREATE ROLE authentik LOGIN PASSWORD '$PW' NOSUPERUSER NOCREATEROLE NOCREATEDB NOREPLICATION;
CREATE DATABASE authentik OWNER authentik;
REVOKE ALL ON DATABASE authentik FROM PUBLIC;
GRANT CONNECT ON DATABASE authentik TO authentik;
GRANT CONNECT ON DATABASE authentik TO backup;
SQL
```

Expected: `secret/pg-authentik created`, then `CREATE ROLE`, `CREATE DATABASE`, `REVOKE`,
`GRANT`, `GRANT`.

The password is not needed again: Step 4 copies it from the Secret. Do not leave it in shell
history.

- [ ] **Step 4: Rewrite the application secret**

The operator records the two generated values in the password manager after this block; they are
not recoverable from the cluster in clear text.

```bash
PW=$(kubectl -n databases get secret pg-authentik -o jsonpath='{.data.password}' | base64 -d)
SK=$(openssl rand -hex 32)
BP=$(openssl rand -base64 24 | tr '/+' '_-')
printf 'AUTHENTIK_SECRET_KEY=%s\nAUTHENTIK_BOOTSTRAP_PASSWORD=%s\n' "$SK" "$BP"
kubectl -n authentik patch secret authentik-env --type merge -p "$(jq -n \
  --arg pw "$PW" --arg sk "$SK" --arg bp "$BP" \
  '{stringData: {"AUTHENTIK_POSTGRESQL__PASSWORD": $pw,
                 "AUTHENTIK_POSTGRESQL__HOST": "postgres.databases.svc.cluster.local",
                 "AUTHENTIK_SECRET_KEY": $sk,
                 "AUTHENTIK_BOOTSTRAP_PASSWORD": $bp}}')"
```

Then clear the terminal scrollback. If Step 2 listed `AUTHENTIK_POSTGRESQL__NAME` or
`AUTHENTIK_POSTGRESQL__USER` with another value, add them to the same patch with `authentik`.
If it listed any `AUTHENTIK_REDIS__*` key, remove it: 2026.8 has no Redis and a stale host only
confuses the next reader.

```bash
kubectl -n authentik patch secret authentik-env --type=json \
  -p '[{"op":"remove","path":"/data/AUTHENTIK_REDIS__HOST"}]'   # repeat per stale key
```

- [ ] **Step 5: Prove the connection from the authentik namespace**

Adapt the runbook's check pod with `APP=authentik`, `APP_NS=authentik`,
`APP_SECRET=authentik-env`, `APP_SECRET_KEY=AUTHENTIK_POSTGRESQL__PASSWORD`. Expected log:

```
CREATE TABLE
INSERT 0 1
 count
-------
     1
(1 row)

DROP TABLE
```

Delete `pod/pg-check` afterwards.

- [ ] **Step 6: Record it**

Append a row to the log in `docs/runbooks/postgres-add-database.md` with today's date,
application `authentik`, role/database `authentik`, namespace `authentik`, secret pair
`databases/pg-authentik` + `authentik/authentik-env`, and a note that the instance started empty.

In `docs/secrets.md`, extend the `authentik-env` row to name the keys this task added:
`AUTHENTIK_SECRET_KEY`, `AUTHENTIK_BOOTSTRAP_PASSWORD`, `AUTHENTIK_POSTGRESQL__PASSWORD`,
`AUTHENTIK_POSTGRESQL__HOST`.

- [ ] **Step 7: Commit**

```bash
git add docs/runbooks/postgres-add-database.md docs/secrets.md
git commit -m "🔐 feat(authentik): create the database, role and runtime secrets"
```

### Task 3: Publish, sync and remove the stale Ingress

**Files:** none (git operations and cluster convergence).

**Interfaces:**
- Consumes: Task 1's chart, Task 2's database and secrets.
- Produces: a synced `authentik` Application with the new resources.

- [ ] **Step 1: Gate on the public DNS record**

```bash
dig +short authentik.ashudev.com @1.1.1.1
```

Expected: `80.14.57.209`. If empty, the operator adds the record at the registrar before this
task continues; the internal router does not depend on it, but the public one will spend the
shared ACME failure budget.

- [ ] **Step 2: Rebase and push**

```bash
git fetch origin
git rebase origin/develop
```

Re-run Task 1 Step 8's render assertions after the rebase, then:

```bash
git push -u origin feature/authentik-sso
```

Open the pull request against `develop` with the render assertions in the body. The operator
reviews and merges; do not merge without their word.

- [ ] **Step 3: Sync**

After the merge, sync the `authentik` Application from the ArgoCD UI (automation lives outside
git by design). Wait for `Synced`/`Healthy`.

- [ ] **Step 4: Remove the object the chart no longer manages**

Pruning is off, so the old plain Ingress survives the sync:

```bash
kubectl -n authentik get ingress
kubectl -n authentik delete ingress authentik-server --ignore-not-found
kubectl -n authentik get ingress   # expected: No resources found
```

- [ ] **Step 5: Delete the orphaned Redis**

2026.8 no longer ships Redis. If the sync did not prune it:

```bash
kubectl -n authentik get statefulset,svc | grep redis
kubectl -n authentik delete statefulset authentik-redis-master --ignore-not-found
kubectl -n authentik delete svc authentik-redis-master authentik-redis-headless --ignore-not-found
```

Expected: no `redis` object remains in the namespace.

- [ ] **Step 6: Confirm the rollouts and the hook**

```bash
kubectl -n authentik rollout status deploy/authentik-server --timeout=300s
kubectl -n authentik rollout status deploy/authentik-worker --timeout=300s
kubectl -n authentik get pods
kubectl -n authentik get jobs
```

Expected: server and worker `1/1 Running`, the PostSync Job `Complete` (or already deleted by its
policy), no `CreateContainerConfigError`.

- [ ] **Step 7: Commit nothing; report**

No git change in this task. Record the sync outcome in the session report.

### Task 4: Verify sign-in and close the phase

**Files:**
- Modify: `docs/status.md`

**Interfaces:**
- Consumes: the running instance from Task 3.
- Produces: evidence in the report and an updated status page.

- [ ] **Step 1: Check both certificates**

```bash
for h in authentik.kub.home.ashudev.com authentik.ashudev.com; do
  echo | openssl s_client -connect 192.168.1.46:443 -servername "$h" 2>/dev/null \
    | openssl x509 -noout -subject -issuer -dates
done
```

Expected for each: `subject=CN = <host>`, `issuer ... Let's Encrypt`, dates in the future.

- [ ] **Step 2: Check both login pages and the health endpoints**

```bash
for h in authentik.kub.home.ashudev.com authentik.ashudev.com; do
  curl -s -o /dev/null -w "$h %{http_code}\n" "https://$h/"
  curl -s -o /dev/null -w "$h ready %{http_code}\n" "https://$h/-/health/ready/"
done
```

Expected: `200` (or `302` to the flow) on `/`, `204` on the readiness path.

- [ ] **Step 3: Check name resolution from inside the cluster**

```bash
kubectl -n authentik run dnscheck --rm -i --restart=Never --image=busybox:1.35 -- \
  getent hosts authentik.kub.home.ashudev.com
```

Expected: two addresses, the first `192.168.1.46` (the LAN answer). This is the issuer every
application backend will call in phase 3; if it does not resolve, phase 3 cannot start.

- [ ] **Step 4: Check the blueprint applied**

```bash
kubectl -n authentik logs deploy/authentik-worker --tail=200 | grep -iE 'blueprint|traceback|error'
```

Expected: a line showing `ashulab-users` was applied; no traceback. Then in the admin UI,
**Directory → Groups** lists `users`.

- [ ] **Step 5: Sign in on both names with one session**

In one browser profile, sign in at `https://authentik.kub.home.ashudev.com` as `akadmin` with
the recorded bootstrap password, then open `https://authentik.ashudev.com` in the same browser.
Expected: already signed in, no second password prompt. Sign out and repeat starting from the
public name.

- [ ] **Step 6: Update the status page**

In `docs/status.md`, remove `authentik` from **Parked** and from the drift list, and add it to
the healthy list with the chart version`2026.8.5`, the two hostnames and "blueprints in git".
Keep the wording of the surrounding entries.

- [ ] **Step 7: Commit and open a docs pull request**

```bash
git add docs/status.md
git commit -m "📝 docs(status): authentik is rebuilt on 2026.8.5"
git push -u origin feature/authentik-sso
```

Open a pull request against `develop`. The operator merges when they are satisfied.

## Success, in one place

- `helm template` renders two IngressRoutes per hostname pair, no Ingress, the blueprint
  ConfigMap mounted in the worker and the PostSync hook.
- The authentik Application is `Synced`/`Healthy`; server and worker are `1/1`.
- Both hostnames serve a valid Let's Encrypt certificate and the login page.
- `users` exists because the blueprint applied, not because somebody clicked.
- One sign-in covers both hostnames in one browser session.
- The local `akadmin` password works and is recorded by the operator.
- No plain Ingress and no `node-type` affinity remain.

## Failure handling

- **Server logs a database connection timeout.** A stale `AUTHENTIK_POSTGRESQL__*` key survived
  in `authentik-env`. Re-read Task 2 Step 2 and rewrite that key; sync again.
- **Certificate for one hostname fails.** Confirm its DNS record and that only that router is
  unhealthy; the other hostname's certificate is independent. Do not re-add the failed router
  until DNS answers.
- **Worker log shows a blueprint error.** Fix the file named in the log; a blueprint is applied
  as one transaction, so nothing from that file took effect.
- **Old Ingress still answers.** It was not deleted; run Task 3 Step 4.
- **Sign-in on the second hostname asks again.** The cookie domain is missing or has no leading
  dot; check `AUTHENTIK_COOKIE_DOMAIN` in the rendered server Deployment.
