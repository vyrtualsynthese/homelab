# PostgreSQL W2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the dead Bitnami PostgreSQL and the broken pgAdmin with an owned chart running PostgreSQL 18 on `local-path`, a network policy, nightly dumps, and a LAN-only pgAdmin — with no application database created ahead of need.

**Architecture:** One `StatefulSet` with two `local-path` claims (data and dumps), a one-shot bootstrap `Job` that creates only the read-only `backup` role, a nightly `CronJob` that enumerates and dumps whatever databases exist, and a `NetworkPolicy` that admits only the six consumer namespaces plus the dump job and pgAdmin. pgAdmin is a separate wrapper chart around `runix/pgadmin4`, reachable only from the LAN through a Traefik `IPAllowList`. No consumer chart is touched; each application's database is created by a runbook procedure when that application is rebuilt in W3.

**Tech Stack:** K3s, ArgoCD (GitOps branch `develop`), Helm v3 charts owned in-repo, PostgreSQL 18.7 official image, Traefik v2 CRDs (`traefik.containo.us/v1alpha1`), Prometheus pod annotations.

**Spec:** `docs/superpowers/specs/2026-11-13-postgresql-design.md`

## Global Constraints

- Image: `postgres:18.7`, pinned by digest (`repository@sha256:…`). No other PostgreSQL image. No `pgvector`, no extensions pre-enabled.
- Namespace: `databases`. Node pin: `nodeSelector: kubernetes.io/hostname: nas` for PostgreSQL, `nuc0` for pgAdmin. The `node-type` label is retired — it must not appear in any rendered manifest.
- Storage: `storageClassName: local-path` for every claim. Two claims: `data` 20Gi, `dumps` 20Gi.
- Resources: every container declares memory requests and limits; PostgreSQL requests 100m/512Mi and limits 1/1Gi.
- Passwords come from Secrets only. Never in a values file, ConfigMap, Application or commit.
- No Application records `syncPolicy.automated`. No Bitnami chart or image anywhere.
- Ingress is Traefik CRDs only, never `networking.k8s.io/v1`. `cluster/argocd/**` files keep CRLF.
- No database is created in advance: the instance starts with `postgres`, `template0`, `template1`, plus the `backup` role. Application databases are created by hand from §6.5's procedure.
- `helm template` must render every chart clean before commit. There is no other test suite; the render plus the grep assertions below are the gate.

---

### Task 1: PostgreSQL chart — StatefulSet, Services, storage, configuration

**Files:**
- Delete: `cluster/helm/databases/postgres/values.yaml` (values-only, replaced wholesale)
- Create: `cluster/helm/databases/postgres/Chart.yaml`
- Create: `cluster/helm/databases/postgres/values.yaml`
- Create: `cluster/helm/databases/postgres/templates/_helpers.tpl`
- Create: `cluster/helm/databases/postgres/templates/statefulset.yaml`
- Create: `cluster/helm/databases/postgres/templates/pvc.yaml`
- Create: `cluster/helm/databases/postgres/templates/services.yaml`
- Create: `cluster/helm/databases/postgres/templates/configmap-postgresql.yaml`

**Interfaces:**
- Produces, relied on by Tasks 2–6 and 8: chart name `postgres`; `Service/postgres` (ClusterIP, port 5432, DNS `postgres.databases.svc.cluster.local`); `Service/postgres-headless`; `StatefulSet/postgres` with pod labels `app: postgres`; two explicit claims, `PersistentVolumeClaim/postgres-data` and `PersistentVolumeClaim/postgres-dumps` (explicit objects, **not** `volumeClaimTemplates`, so the dump job can mount the same claim by name); values keys `image.{repository,tag,digest}`, `nodeSelector`, `data.{size,storageClassName}`, `dumps.{size,storageClassName,schedule,retentionDays}`, `resources`, `postgresql.*`, `networkPolicy.consumerNamespaces`, `exporter.{repository,tag}`.

- [ ] **Step 1: Resolve the image digest**

Run: `skopeo inspect docker://docker.io/library/postgres:18.7 --format '{{.Digest}}'`
(alternative: `docker buildx imagetools inspect postgres:18.7`)
Expected: `sha256:…`. Record it; it goes in `values.yaml` as `image.digest`. Do not continue with a bare tag.

- [ ] **Step 2: Write `Chart.yaml`**

```yaml
apiVersion: v2
name: postgres
description: Shared PostgreSQL instance for the homelab applications
type: application
version: 0.1.0
appVersion: "18.7"
```

- [ ] **Step 3: Write `values.yaml`** with exactly these keys and values

```yaml
image:
  repository: postgres
  tag: "18.7"
  digest: "sha256:<resolved in Step 1>"

nodeSelector:
  kubernetes.io/hostname: nas

data:
  size: 20Gi
  storageClassName: local-path

dumps:
  size: 20Gi
  storageClassName: local-path
  schedule: "30 3 * * *"
  retentionDays: 14

resources:
  requests:
    cpu: 100m
    memory: 512Mi
  limits:
    cpu: "1"
    memory: 1Gi

postgresql:
  shared_buffers: 256MB
  effective_cache_size: 768MB
  max_connections: 60
  work_mem: 8MB
  maintenance_work_mem: 64MB
  log_min_duration_statement: 1000

networkPolicy:
  consumerNamespaces:
    - authentik
    - discourse
    - firefly
    - guacamole
    - koillection
    - wiki

exporter:
  repository: prometheuscommunity/postgres-exporter
  tag: "v0.17.1"
```

- [ ] **Step 4: Write `templates/_helpers.tpl`** — `postgres.name` (`postgres`, not release-prefixed, so the DNS name is stable), `postgres.labels`.

- [ ] **Step 5: Write `templates/services.yaml`** — `Service/postgres` type `ClusterIP` on port 5432 named `postgres`, selector `app: postgres`; `Service/postgres-headless` `clusterIP: None`, same selector, referenced by `statefulset.spec.serviceName`.

- [ ] **Step 6: Write `templates/configmap-postgresql.yaml`** — a ConfigMap named `postgres-config` whose `postgresql.conf` contains one `key = value` line per key in `values.postgresql` (`log_min_duration_statement = 1000` in milliseconds). Mounted at `/etc/postgresql/` and included with `-c config_file=/etc/postgresql/postgresql.conf`.

- [ ] **Step 7: Write `templates/pvc.yaml`** — two `PersistentVolumeClaim` objects, `postgres-data` and `postgres-dumps`, `ReadWriteOnce`, sizes and class from `values.data` and `values.dumps`. Explicit objects, not claim templates: the dump `CronJob` mounts `postgres-dumps` by name.

- [ ] **Step 8: Write `templates/statefulset.yaml`** — one replica, `serviceName: postgres-headless`, `podManagementPolicy: OrderedReady`, `terminationGracePeriodSeconds: 120`; container name `postgres`; `nodeSelector` from values; pod security context `runAsUser: 999`, `runAsGroup: 999`, `fsGroup: 999`, `seccompProfile.type: RuntimeDefault`; container security context `allowPrivilegeEscalation: false`, `capabilities.drop: [ALL]`; env `POSTGRES_USER=postgres`, `POSTGRES_PASSWORD` from Secret `postgres-superuser` key `postgres-password`, `PGDATA=/var/lib/postgresql/data/pgdata`, `POSTGRES_INITDB_ARGS=--data-checksums`; args `-c config_file=/etc/postgresql/postgresql.conf`; volumes `data` (`claimName: postgres-data`) at `/var/lib/postgresql/data` and `postgres-config` at `/etc/postgresql`; `exec` probes `pg_isready -U postgres -h 127.0.0.1` (startup 5s×12, readiness 10s, liveness 20s×3); pod annotations `prometheus.io/scrape: "true"` and `prometheus.io/port: "9187"`.

  Second container `exporter`: image `{{ .Values.exporter.repository }}:{{ .Values.exporter.tag }}`, port 9187 named `metrics`, env `DATA_SOURCE_URI=127.0.0.1:5432/postgres?sslmode=disable`, `DATA_SOURCE_USER=postgres_exporter`, `DATA_SOURCE_PASS` from Secret `pg-exporter` key `exporter-password`, resources 10m/32Mi request and 100m/64Mi limit, security context dropping capabilities.

- [ ] **Step 9: Render and check the shape**

```bash
helm template postgres cluster/helm/databases/postgres -n databases > /tmp/pg.yaml
grep -q 'image: "postgres@sha256:' /tmp/pg.yaml
grep -q 'kubernetes.io/hostname: nas' /tmp/pg.yaml
grep -q 'storageClassName: local-path' /tmp/pg.yaml
grep -q 'type: ClusterIP' /tmp/pg.yaml
grep -q 'shared_buffers = 256MB' /tmp/pg.yaml
grep -q 'data-checksums' /tmp/pg.yaml
grep -q 'name: postgres-data' /tmp/pg.yaml
grep -q 'name: postgres-dumps' /tmp/pg.yaml
grep -q 'prometheus.io/port: "9187"' /tmp/pg.yaml
! grep -q 'volumeClaimTemplates' /tmp/pg.yaml
! grep -q 'node-type' /tmp/pg.yaml
```
Expected: every `grep` exits 0. Any non-zero is a fix, not a tolerated warning.

- [ ] **Step 10: Commit**

```bash
git add cluster/helm/databases/postgres
git commit -m "✨ feat(postgres): own chart with a pinned PostgreSQL 18 StatefulSet"
```

---

### Task 2: Bootstrap roles and authentication

**Files:**
- Create: `cluster/helm/databases/postgres/templates/configmap-bootstrap.yaml`
- Create: `cluster/helm/databases/postgres/templates/configmap-pg-hba.yaml`
- Create: `cluster/helm/databases/postgres/templates/job-bootstrap.yaml`
- Modify: `cluster/helm/databases/postgres/templates/statefulset.yaml` (hba mount + `-c hba_file=`)

**Interfaces:**
- Consumes: Task 1's chart, `Service/postgres`, pod label `app: postgres`.
- Produces: role `backup` with `pg_read_all_data`, password from `Secret/pg-backup` key `backup-password`; role `postgres_exporter` with `pg_monitor` and `CONNECT` on `postgres`, password from `Secret/pg-exporter` key `exporter-password`; `Job/postgres-bootstrap` labelled `app: postgres-bootstrap`; `ConfigMap/postgres-bootstrap` with key `bootstrap.sql`.

- [ ] **Step 1: Write the bootstrap SQL** in `templates/configmap-bootstrap.yaml`

```sql
\set ON_ERROR_STOP on
SELECT 'CREATE ROLE backup LOGIN PASSWORD ' || quote_literal(:'backup_password')
 WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'backup')\gexec
GRANT pg_read_all_data TO backup;

SELECT 'CREATE ROLE postgres_exporter LOGIN PASSWORD ' || quote_literal(:'exporter_password')
 WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'postgres_exporter')\gexec
GRANT pg_monitor TO postgres_exporter;
GRANT CONNECT ON DATABASE postgres TO postgres_exporter;

REVOKE CONNECT ON DATABASE postgres FROM PUBLIC;
```

The passwords are passed as `psql` variables (`-v backup_password="$BACKUP_PASSWORD" -v
exporter_password="$EXPORTER_PASSWORD"`) and never interpolated into this file. The `GRANT CONNECT`
on the exporter role is required *after* the `REVOKE`, or the sidecar cannot scrape.

- [ ] **Step 2: Write `templates/job-bootstrap.yaml`**

`Job` with `backoffLimit: 3`, `restartPolicy: Never`, label `app: postgres-bootstrap`; one container
based on the same image, command `bash -c 'psql -h postgres -U postgres -d postgres -v
backup_password="$BACKUP_PASSWORD" -v exporter_password="$EXPORTER_PASSWORD" -f /sql/bootstrap.sql'`;
env `PGPASSWORD` from `postgres-superuser`/`postgres-password`, `BACKUP_PASSWORD` from
`pg-backup`/`backup-password`, `EXPORTER_PASSWORD` from `pg-exporter`/`exporter-password`; the
ConfigMap mounted at `/sql`; resources 10m/32Mi request, 100m/128Mi limit. Annotate the pod template
with `checksum/bootstrap: {{ include (print $.Template.BasePath "/configmap-bootstrap.yaml") . | sha256sum }}`.

- [ ] **Step 3: Write `templates/configmap-pg-hba.yaml`** — `local all postgres peer` first, then
`host all all 0.0.0.0/0 scram-sha-256`, and `host all all ::/0 scram-sha-256`. No `trust` line.
Mount it at `/etc/postgresql-hba/` and add `-c hba_file=/etc/postgresql-hba/pg_hba.conf` to the
StatefulSet args.

- [ ] **Step 4: Render and check**

```bash
helm template postgres cluster/helm/databases/postgres -n databases > /tmp/pg.yaml
grep -q 'pg_read_all_data TO backup' /tmp/pg.yaml
grep -q 'pg_monitor TO postgres_exporter' /tmp/pg.yaml
grep -q 'REVOKE CONNECT ON DATABASE postgres FROM PUBLIC' /tmp/pg.yaml
grep -q 'hba_file=/etc/postgresql-hba/pg_hba.conf' /tmp/pg.yaml
grep -q 'name: pg-backup' /tmp/pg.yaml
grep -q 'name: pg-exporter' /tmp/pg.yaml
! grep -q 'trust' /tmp/pg.yaml
```
Expected: all pass. The `! grep trust` check is the one that matters — a `trust` line makes every
other control pointless.

- [ ] **Step 5: Commit**

```bash
git add cluster/helm/databases/postgres
git commit -m "✨ feat(postgres): bootstrap the read-only backup role and explicit pg_hba"
```

---

### Task 3: Network policy and non-exposure

**Files:**
- Create: `cluster/helm/databases/postgres/templates/networkpolicy.yaml`

**Interfaces:**
- Consumes: values key `networkPolicy.consumerNamespaces` (Task 1); pod labels `app: postgres` (Task 1), `app: postgres-dump` (Task 4), `app: pgadmin` (Task 6).
- Produces: `NetworkPolicy/postgres` with policy type `Ingress` only.

- [ ] **Step 1: Write `templates/networkpolicy.yaml`**

`spec.policyTypes: [Ingress]`, `podSelector.matchLabels: {app: postgres}`, and exactly three ingress
rules: one per consumer namespace via `namespaceSelector.matchLabels: {kubernetes.io/metadata.name:
<ns>}` (rendered from the values list), one `podSelector: {matchLabels: {app: postgres-dump}}` for
the dump CronJob in the same namespace, one `podSelector: {matchLabels: {app: pgadmin}}`. Every rule
restricts `ports: [{protocol: TCP, port: 5432}]`. No egress rule, so egress stays default-allowed.

- [ ] **Step 2: Render and check**

```bash
helm template postgres cluster/helm/databases/postgres -n databases > /tmp/pg.yaml
for ns in authentik discourse firefly guacamole koillection wiki; do
  grep -q "kubernetes.io/metadata.name: $ns" /tmp/pg.yaml || echo "MISSING $ns"
done
grep -q 'app: postgres-dump' /tmp/pg.yaml
grep -q 'app: pgadmin' /tmp/pg.yaml
! grep -q 'kind: Ingress' /tmp/pg.yaml
! grep -Eq 'type: (LoadBalancer|NodePort)' /tmp/pg.yaml
```
Expected: no `MISSING` output, all `grep` succeed.

- [ ] **Step 3: Commit**

```bash
git add cluster/helm/databases/postgres
git commit -m "🔒 feat(postgres): deny by default and allow only the consumer namespaces"
```

---

### Task 4: Nightly per-database dumps

**Files:**
- Create: `cluster/helm/databases/postgres/templates/configmap-dump-script.yaml`
- Create: `cluster/helm/databases/postgres/templates/cronjob-dump.yaml`

**Interfaces:**
- Consumes: `dumps` claim from Task 1's volume claim template; `pg-backup` Secret (Task 2); `backup` role (Task 2).
- Produces: `CronJob/postgres-dump` labelled `app: postgres-dump`, writing `/dumps/<db>-<UTC>.dump` on the `dumps` claim.

- [ ] **Step 1: Write the script** (`dump-all.sh`) into `templates/configmap-dump-script.yaml`

```bash
#!/usr/bin/env bash
set -euo pipefail
export PGPASSWORD="${BACKUP_PASSWORD:?}"
mapfile -t dbs < <(psql -h 127.0.0.1 -U backup -d postgres -At -c \
  "SELECT datname FROM pg_database WHERE datistemplate = false AND datname <> 'postgres' ORDER BY 1")
if [ "${#dbs[@]}" -eq 0 ]; then
  echo "no application databases exist; refusing to report success" >&2
  exit 1
fi
for db in "${dbs[@]}"; do
  out="/dumps/${db}-$(date -u +%Y%m%dT%H%M%SZ).dump"
  pg_dump -h 127.0.0.1 -U backup -d "$db" -Fc -Z6 -f "$out"
  [ -s "$out" ] || { echo "empty dump: $out" >&2; exit 1; }
  pg_restore --list "$out" > /dev/null
done
find /dumps -maxdepth 1 -name '*.dump' -type f -mtime +{{ .Values.dumps.retentionDays }} -delete
```

- [ ] **Step 2: Check the script parses, and that the empty case really fails**

```bash
helm template postgres cluster/helm/databases/postgres -n databases > /tmp/pg.yaml
python3 - <<'PY' > /tmp/dump.sh
import re
src = open('/tmp/pg.yaml').read()
m = re.search(r'dump-all\.sh: \|\n((?:    .*\n)+)', src)
print('\n'.join(line[4:] for line in m.group(1).splitlines()))
PY
bash -n /tmp/dump.sh && echo SYNTAX_OK
grep -q 'no application databases exist' /tmp/dump.sh
grep -q 'exit 1' /tmp/dump.sh
```
Expected: `SYNTAX_OK`, both greps succeed.

- [ ] **Step 3: Write `templates/cronjob-dump.yaml`**

`CronJob` `schedule` from `values.dumps.schedule`, `concurrencyPolicy: Forbid`,
`startingDeadlineSeconds: 3600`, `successfulJobsHistoryLimit: 3`, `failedJobsHistoryLimit: 3`;
successful/failed history kept small. Job template: `restartPolicy: Never`, label `app:
postgres-dump`, the script mounted from the ConfigMap at `/scripts/dump-all.sh`, the `dumps` claim
mounted at `/dumps` (`existingClaim: postgres-dumps`), env `BACKUP_PASSWORD` from `pg-backup`,
command `["/bin/bash", "/scripts/dump-all.sh"]`, resources 50m/64Mi request and 500m/256Mi limit.

- [ ] **Step 4: Render and check**

```bash
helm template postgres cluster/helm/databases/postgres -n databases > /tmp/pg.yaml
grep -q 'schedule: "30 3 \* \* \*"' /tmp/pg.yaml
grep -q 'existingClaim: postgres-dumps' /tmp/pg.yaml
grep -q 'concurrencyPolicy: Forbid' /tmp/pg.yaml
```
Expected: all three pass.

- [ ] **Step 5: Commit**

```bash
git add cluster/helm/databases/postgres
git commit -m "✨ feat(postgres): nightly verified dumps of every database that exists"
```

---

### Task 5: ArgoCD Application, and retiring the Bitnami wiring

**Files:**
- Modify: `cluster/argocd/projects/databases/postgres.yaml` (full replacement, CRLF preserved)
- Delete: the Bitnami multi-source block from that file

**Interfaces:**
- Consumes: chart at `cluster/helm/databases/postgres` (Tasks 1–4).
- Produces: an Application that renders the local chart into namespace `databases`.

- [ ] **Step 1: Replace the file body**

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: postgres
  namespace: argocd
spec:
  project: default
  destination:
    server: 'https://kubernetes.default.svc'
    namespace: databases
  syncPolicy:
    syncOptions:
      - CreateNamespace=true
  source:
    repoURL: 'https://github.com/vyrtualsynthese/homelab.git'
    targetRevision: develop
    path: cluster/helm/databases/postgres
```

- [ ] **Step 2: Confirm CRLF and the absence of the old shape**

```bash
f=cluster/argocd/projects/databases/postgres.yaml
grep -q $'\r' "$f" && echo CRLF_OK
! grep -q 'bitnamicharts' "$f"
! grep -q 'targetRevision: 15' "$f"
! grep -q 'automated' "$f"
grep -q 'path: cluster/helm/databases/postgres' "$f"
```
Expected: `CRLF_OK` and no failures. If the edit stripped CRLF, convert the file back with
`sed -i 's/$/\r/' "$f"`.

- [ ] **Step 3: Commit**

```bash
git add cluster/argocd/projects/databases/postgres.yaml
git commit -m "♻️ refactor(postgres): sync the owned chart instead of the Bitnami one"
```

---

### Task 6: pgAdmin wrapper chart, admin login, LAN-only ingress

**Files:**
- Modify: `cluster/helm/databases/pgadmin/Chart.yaml` (dependency `runix/pgadmin4` 1.50.0)
- Rewrite: `cluster/helm/databases/pgadmin/values.yaml`
- Create: `cluster/helm/databases/pgadmin/templates/ingressroute.yaml`
- Create: `cluster/helm/databases/pgadmin/templates/middleware.yaml`
- Create: `cluster/helm/databases/pgadmin/templates/pvc.yaml`
- Modify: `.gitignore` (scoped to this chart's built dependencies)
- Modify: `cluster/argocd/projects/databases/pgadmin.yaml` (CRLF preserved)

**Interfaces:**
- Consumes: `Service/postgres` (Task 1); `postgres`, `backup` roles (Tasks 1–2); `Secret/pgadmin` key `password`.
- Produces: pgAdmin at `pgadmin.kub.local` on the `web` entrypoint only; pod label `app: pgadmin`.

- [ ] **Step 1: Pin the dependency and read the chart's default image tag**

```bash
helm show chart pgadmin4 --repo https://helm.runix.net --version 1.50.0 | grep -E '^(version|appVersion):'
```
Record `appVersion`; it becomes the explicit `pgadmin.image.tag`, pinned (no `latest`, no reliance on
the chart default).

- [ ] **Step 2: Write `Chart.yaml`**

```yaml
apiVersion: v2
name: pgadmin
description: pgAdmin 4 for the shared PostgreSQL instance
type: application
version: 0.1.0
dependencies:
  - name: pgadmin
    version: 1.50.0
    repository: https://helm.runix.net/
```

- [ ] **Step 3: Write `values.yaml`** under the `pgadmin:` key

`image` repository `dpage/pgadmin4` with the tag from Step 1; `replicaCount: 1`;
`strategy.type: Recreate`; `existingSecret: pgadmin`; `secretKeys.pgadminPasswordKey: password`;
`env.email` set to the operator's login email (plain value, not a secret);
`persistentVolume.enabled: true`, `persistentVolume.size: 10Gi`,
`persistentVolume.storageClass: local-path` (the chart key is `storageClass`, not
`storageClassName`), **and `persistentVolume.existingClaim: pgadmin-config`** — the claim the wrapper
creates in Step 4. The chart's default name would be `pgadmin-pgadmin4`, which exists in the
namespace as a `Terminating` Longhorn claim and cannot be created again; `resources` requests
50m/256Mi, limits 500m/512Mi; `nodeSelector: {kubernetes.io/hostname: nuc0}`; `podLabels: {app:
pgadmin}` (the PostgreSQL network policy selects on it); `networkPolicy.enabled: false` (the
controls are the Traefik allowlist and the PostgreSQL policy); `ingress.enabled: false`; and
`serverDefinitions`:

```yaml
serverDefinitions:
  enabled: true
  resourceType: ConfigMap
  servers:
    postgres-admin:
      Name: "postgres (admin)"
      Group: "Servers"
      Username: "postgres"
      Host: "postgres.databases.svc.cluster.local"
      Port: "5432"
      SSLMode: "prefer"
      MaintenanceDB: "postgres"
    postgres-readonly:
      Name: "postgres (read-only)"
      Group: "Servers"
      Username: "backup"
      Host: "postgres.databases.svc.cluster.local"
      Port: "5432"
      SSLMode: "prefer"
      MaintenanceDB: "postgres"
```

- [ ] **Step 4: Write `templates/pvc.yaml`** — `PersistentVolumeClaim/pgadmin-config`, 10Gi,
`storageClassName: local-path`, `ReadWriteOnce`. Explicit, because the chart default name collides
with a `Terminating` claim.

- [ ] **Step 5: Write `templates/middleware.yaml`** — `Middleware/pgadmin-lan-only`,
`apiVersion: traefik.containo.us/v1alpha1`, `spec.ipAllowList.sourceRange: ["192.168.1.0/24"]`
(confirmed live: nodes `.27`, `.41`, `.46`, `.48`).

- [ ] **Step 6: Write `templates/ingressroute.yaml`** — `IngressRoute/pgadmin` with
`entryPoints: [web]`, one route `match: Host(`pgadmin.kub.local`)`, `kind: Rule`, middleware
`pgadmin-lan-only`, and a service whose name and port are taken from the rendered subchart Service
(default `pgadmin-pgadmin4`, port 80). No `websecure`, no `tls` block, no `certResolver`.

- [ ] **Step 7: Build the dependency and render**

```bash
helm dependency build cluster/helm/databases/pgadmin
helm template pgadmin cluster/helm/databases/pgadmin -n databases > /tmp/pga.yaml
grep -q 'existingSecret: pgadmin' /tmp/pga.yaml || grep -q 'name: pgadmin' /tmp/pga.yaml
grep -q 'postgres.databases.svc.cluster.local' /tmp/pga.yaml
grep -q 'pgadmin.kub.local' /tmp/pga.yaml
grep -q '192.168.1.0/24' /tmp/pga.yaml
grep -q 'entryPoints: \[web\]' /tmp/pga.yaml
! grep -q 'websecure' /tmp/pga.yaml
! grep -q 'node-type' /tmp/pga.yaml
! grep -q 'networking.k8s.io' /tmp/pga.yaml
```
Expected: all pass. Read the rendered Service name and make Step 6's reference match it.

- [ ] **Step 8: Keep the built dependency out of git**

Append to `.gitignore`, in the same style as the monitoring rule:

```
# Built by ArgoCD at sync time (see the PostgreSQL design §6.11)
cluster/helm/databases/pgadmin/charts/
cluster/helm/databases/pgadmin/Chart.lock
```

- [ ] **Step 9: Rewrite the Application** (same shape as Task 5 Step 1, name `pgadmin`, path
`cluster/helm/databases/pgadmin`), then confirm CRLF, `! grep -q automated`, and `! grep -q
networking.k8s.io`.

- [ ] **Step 10: Commit**

```bash
git add .gitignore cluster/helm/databases/pgadmin cluster/argocd/projects/databases/pgadmin.yaml
git commit -m "✨ feat(pgadmin): wrap the upstream chart with an admin login and LAN-only ingress"
```

---

### Task 7: Secrets inventory and the two runbooks

**Files:**
- Create: `docs/secrets.md`
- Create: `docs/runbooks/postgres-add-database.md`
- Create: `docs/runbooks/postgres-restore.md`
- Modify: `docs/runbooks/README.md` (two rows)

**Interfaces:**
- Consumes: the shapes fixed in Tasks 1, 2 and 6.
- Produces: the procedure Task 8 executes; the credentials record a future rebuild reads.

- [ ] **Step 1: Write `docs/secrets.md`** — a table of name, namespace, keys, consumer, and where the
value comes from, covering `postgres-superuser`, `pg-backup`, `pg-exporter`, `pgadmin`, and a note
that every `pg-<app>`/app-namespace pair is added when its database is created. No values.

- [ ] **Step 2: Write `docs/runbooks/postgres-add-database.md`** — first line states it changes the
cluster; then preconditions (superuser password to hand), the four SQL statements from the design
§6.5 **including both `REVOKE ALL ON DATABASE <app> FROM PUBLIC` and `GRANT CONNECT … TO backup`,
then the two Secret creations, the connection test from a pod in the application's namespace, and a
dated log table. Success output and rollback (`DROP DATABASE`, `DROP ROLE`) at the end.

- [ ] **Step 3: Write `docs/runbooks/postgres-restore.md`** — preconditions, how to find the newest
dump (`ls -lt` on the `dumps` claim), starting a scratch instance, `pg_restore -d <db> --no-owner
--role=<role>`, the row-count check, teardown, and a dated drill log. First line: destructive.

- [ ] **Step 4: Check the rules are actually in the runbook**

```bash
f=docs/runbooks/postgres-add-database.md
grep -q 'REVOKE ALL ON DATABASE' "$f"
grep -q 'FROM PUBLIC' "$f"
grep -q 'GRANT CONNECT ON DATABASE' "$f"
grep -q 'NOSUPERUSER NOCREATEROLE NOCREATEDB' "$f"
grep -q 'backup' "$f"
```
Expected: all pass. A missing `REVOKE` line is the difference between a database and an open door.

- [ ] **Step 5: Add the two rows to `docs/runbooks/README.md`**, status `pending — proved on first use`.

- [ ] **Step 6: Commit**

```bash
git add docs/secrets.md docs/runbooks
git commit -m "📝 docs(postgres): secrets inventory and the add-database, restore runbooks"
```

---

### Task 8: Cluster bring-up and acceptance (needs write access)

This task is not repository work. It is the gate the design's §9 describes. The read-only Kubernetes
MCP can confirm state at each step, but creating Secrets, syncing Applications and deleting objects
need the operator's admin kubeconfig.

- [ ] **Step 1: Create the four Secrets by hand** in `databases`: `postgres-superuser` with
`postgres-password`, `pg-backup` with `backup-password`, `pg-exporter` with `exporter-password`,
`pgadmin` with `password`. Generate each with `openssl rand -base64 32`. Record them in
`docs/secrets.md` (name and keys only, never values).

- [ ] **Step 2: Sync both Applications** in ArgoCD (`postgres`, then `pgadmin`; auto-sync off in git —
enable it per Application in the UI, or sync manually).

- [ ] **Step 3: Check the database came up with an empty instance**

```bash
kubectl -n databases get statefulset,pod,job,pvc
kubectl -n databases exec postgres-0 -c postgres -- psql -U postgres -Atc 'SELECT datname FROM pg_database ORDER BY 1'
kubectl -n databases exec postgres-0 -c postgres -- psql -U postgres -Atc '\du'
kubectl -n databases exec postgres-0 -c exporter -- wget -qO- http://127.0.0.1:9187/metrics | head -3
```
Expected: pod `Running` with both containers ready, bootstrap Job `Complete`, databases `postgres`,
`template0`, `template1` only, roles `backup`, `postgres`, `postgres_exporter`, and exporter metrics
returning `pg_up 1`. Claims `postgres-data` and `postgres-dumps` are `Bound` once the pod is
scheduled — `local-path` is `WaitForFirstConsumer`, so a `Pending` claim before the pod exists is
normal. Anything else is a failure to explain, not a bonus.

- [ ] **Step 4: Delete the old Bitnami leftovers, now that the new instance answers**

```bash
kubectl -n databases delete statefulset postgres-postgresql
kubectl -n databases delete svc postgres-postgresql postgres-postgresql-hl postgres-postgresql-metrics
kubectl -n databases delete deployment alpine
```
Expected: the 95-day `Pending` pod, its three Services and the 500-day `alpine` Deployment are gone.
They are not ours to prune: the Application that owned them is being replaced.

- [ ] **Step 5: Clear the `Terminating` pgAdmin claim**

`pvc/pgadmin-pgadmin4` has been `Terminating` on Longhorn since the disk loss. Patch the PV's
finalizers first, then the claim's (`AGENTS.md` §7 item 4):
```bash
kubectl get pv | grep pgadmin
kubectl patch pv <pv-name> -p '{"metadata":{"finalizers":null}}' --type=merge
kubectl -n databases patch pvc pgadmin-pgadmin4 -p '{"metadata":{"finalizers":null}}' --type=merge
```
Expected: the claim disappears. The new chart uses `pgadmin-config`, so this is cleanup, not a
dependency — do it in either order.

- [ ] **Step 6: Walk the add-database runbook once** — create `acceptance` with role `acceptance`,
its two Secrets, and the §6.5 SQL. Confirm `\l` shows it owned by `acceptance`.

- [ ] **Step 7: Prove connectivity and the policy**

```bash
kubectl -n wiki run psql-test --rm -it --image=postgres:18.7 --restart=Never -- \
  psql "postgresql://acceptance:<pw>@postgres.databases.svc.cluster.local:5432/acceptance" -c 'CREATE TABLE t(x int); INSERT INTO t VALUES (1); SELECT * FROM t; DROP TABLE t;'
kubectl -n default run psql-denied --rm -it --image=postgres:18.7 --restart=Never -- \
  psql "postgresql://acceptance:<pw>@postgres.databases.svc.cluster.local:5432/acceptance" -c 'select 1'
```
Expected: the first succeeds; the second times out or is refused. If the second also succeeds, the
network policy is not being enforced — record it and do not call goal 2 met.

- [ ] **Step 8: Wait for the dump, then restore it**

Next morning (or `kubectl -n databases create job --from=cronjob/postgres-dump dump-now`):
```bash
kubectl -n databases logs job/postgres-dump-<timestamp>
```
Expected: one file for `acceptance`, no errors. Then walk `postgres-restore.md`, record the date and
row counts, and drop the acceptance database.

- [ ] **Step 9: pgAdmin check** — from a workstation on the LAN, load `pgadmin.kub.local`, log in with
the admin account, confirm both pre-registered servers are listed, connect as `backup` and browse.
From outside the LAN, confirm it does not load.

- [ ] **Step 10: Add the four alerts in Grafana** (dump file older than 26 hours, database pod not
ready, data volume above 85 %, `pg_up == 0`), and note them in `docs/observability-conventions.md`.

- [ ] **Step 11: Final gate**

```bash
for c in cluster/helm/databases/postgres cluster/helm/databases/pgadmin; do
  helm template test "$c" -f /dev/null > /dev/null && echo "OK $c"
done
grep -rn 'bitnami' cluster/argocd cluster/helm/databases || echo "NO_BITNAMI"
while read -r p; do [ -d "$p" ] || echo "BROKEN PATH $p"; done < <(
  grep -rh '^    path:' cluster/argocd/projects | sed 's/^    path: *//' | tr -d "\"' \r" | grep databases)
while read -r v; do [ -f ".$v" ] || echo "BROKEN VALUES $v"; done < <(
  grep -rh '\$values/' cluster/argocd/projects | sed 's/.*\$values//' | tr -d "\"' \r" | sort -u | grep databases)
```
Expected: `OK` twice, `NO_BITNAMI`, and no `BROKEN` line mentioning a database path. Pre-existing
`nfs` failures are W1's and do not belong to this gate.

---

## Review Focus

1. **The dump job's own connection is denied by the policy.** It is a pod in `databases`, and the
policy denies that namespace by default. Task 3's render check greps for `app: postgres-dump` in the
policy; Task 8's dump step proves it end to end.
2. **A dump night with nothing to dump reports success.** An empty instance or a broken connection
would otherwise look like a healthy backup. Task 4's script exits non-zero, and Step 2 greps for
that branch; Task 8 adds the dump-age alert.
3. **A database created without `REVOKE … FROM PUBLIC` stays connectable by every role.** Task 7's
grep check fails the task if the `REVOKE` line is missing from the runbook.
4. **pgAdmin reachable from the public entrypoint.** Task 6 asserts `entryPoints: [web]` and the
absence of `websecure`; Task 8 checks from an off-LAN client.
5. **`CONNECT` handling on the `postgres` database, in both directions.** Left on for `PUBLIC`, an
application guessing the database name gets in; revoked without a re-grant, the metrics sidecar goes
silent. Task 2's SQL revokes after granting the exporter, the render check greps both statements, and
Task 8 Step 3 requires `pg_up 1` from the exporter.
