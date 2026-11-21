# Traefik leaves the K3s addon — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Traefik runs from a release this repository owns, at chart `25.0.3` / Traefik `2.10.7`, with every hand-made piece of its configuration captured in git and K3s unable to touch it.

**Architecture:** Snapshot the live objects, derive one values file from the running release, remove the K3s finalizers that would run `helm uninstall`, have the operator disable the addon in the node config, then adopt the existing release with one `helm upgrade` that is render-proven identical except for the metrics fix.

**Tech Stack:** K3s `v1.30.1+k3s1`, Helm 3, Traefik chart `25.0.3`, `kubectl`, the repo's `hack/` checkers.

**Spec:** `docs/superpowers/specs/2026-11-21-traefik-ownership-design.md`

## Global Constraints

- Chart `25.0.3`, image `rancher/mirrored-library-traefik:2.10.7`. No version change.
- Never run `helm uninstall traefik-crd`: its chart carries all nine `traefik.containo.us` CRDs in `templates/`, so an uninstall deletes them and every `IngressRoute` with them.
- Never write to, move or chmod `/data/acme.json`. Its sha256 is `f074495fb3b8ad9f032a10412e7b5a6cd59f0bedbe45d9433a770150bf9ffbf9` (429905 bytes, mode `600`, owner `65532:65532`) at the time of writing.
- No Secrets, no `acme.json`, no kubeconfig in git.
- No ArgoCD Application for the release in this change.
- LF endings: `cluster/infrastructure/**` and `docs/**` are LF. The CRLF rule covers `cluster/argocd/**` only.
- Every commit is Gitmoji-prefixed, as AGENTS.md section 4 requires.
- The full checker suite must pass before the PR: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests` and `hack/check-exposure.sh`.

## Review Focus

- **The finalizer returns before the node step.** The addon controller re-applies its manifest every few seconds; if `wrangler.cattle.io/on-helm-chart-remove` is back when the addon is disabled, the delete path can run `helm uninstall` — and on the CRD chart that removes every route. Re-read the finalizers immediately before the operator restarts the first server.
- **A duplicate `disable:` key stops K3s.** If `/etc/rancher/k3s/config.yaml` already has `disable:`, appending a second top-level key makes K3s refuse the file and the node does not come back. The operator must merge, not append.
- **`disable` on an agent.** `nas` runs `k3s agent`, which has no `disable` flag; adding it can stop the service. Three servers only.
- **Stale values survive a careless upgrade.** Without `--reset-values`, Helm merges with the release's stored values and the pruned keys come back. Verify the release values after the upgrade, not only the rendered manifest.
- **The metrics flip silently removes series.** `manualRouting: false` must leave `/metrics` serving `traefik_*`; check the series count and the Prometheus target, not just the HTTP code.

---

### Task 1: Snapshot the live configuration

**Files:**
- Create: `cluster/infrastructure/traefik/snapshot/2026-11-21/release-values.yaml`
- Create: `cluster/infrastructure/traefik/snapshot/2026-11-21/release-computed-values.yaml`
- Create: `cluster/infrastructure/traefik/snapshot/2026-11-21/release-manifest.yaml`
- Create: `cluster/infrastructure/traefik/snapshot/2026-11-21/release-history.txt`
- Create: `cluster/infrastructure/traefik/snapshot/2026-11-21/k3s-helmchart-traefik.yaml`
- Create: `cluster/infrastructure/traefik/snapshot/2026-11-21/k3s-helmchart-traefik-crd.yaml`
- Create: `cluster/infrastructure/traefik/snapshot/2026-11-21/k3s-helmchartconfig-traefik.yaml`
- Create: `cluster/infrastructure/traefik/snapshot/2026-11-21/crds.yaml`
- Create: `cluster/infrastructure/traefik/snapshot/2026-11-21/objects.yaml`
- Create: `cluster/infrastructure/traefik/snapshot/2026-11-21/workload.yaml`
- Create: `cluster/infrastructure/traefik/snapshot/2026-11-21/acme.txt`
- Create: `cluster/infrastructure/traefik/snapshot/2026-11-21/README.md`

**Interfaces:**
- Produces: `release-values.yaml`, the source `values.yaml` is derived from in Task 2; `release-manifest.yaml`, the baseline every render diff in Task 2 compares against; `acme.txt`, the digest Task 7 compares with.

- [ ] **Step 1: Capture the release and the K3s objects**

```bash
cd cluster/infrastructure/traefik/snapshot/2026-11-21
helm -n kube-system get values traefik > release-values.yaml
helm -n kube-system get values traefik --all > release-computed-values.yaml
helm -n kube-system get manifest traefik > release-manifest.yaml
helm -n kube-system history traefik > release-history.txt
for n in traefik traefik-crd; do
  kubectl -n kube-system get helmchart $n -o yaml > k3s-helmchart-$n.yaml
done
kubectl -n kube-system get helmchartconfig traefik -o yaml > k3s-helmchartconfig-traefik.yaml
```

- [ ] **Step 2: Capture the CRDs and every live Traefik object**

```bash
kubectl get crd -o name | grep traefik | xargs kubectl get -o yaml > crds.yaml
for g in traefik.containo.us traefik.io; do
  for k in ingressroutes middlewares tlsoptions tlsstores ingressroutetcps ingressrouteudps \
           middlewaretcps serverstransports serverstransporttcps traefikservices; do
    kubectl get "$k.$g" -A -o yaml 2>/dev/null
  done
done > objects.yaml
kubectl -n kube-system get deployment/traefik service/traefik ingressclass/traefik -o yaml > workload.yaml
kubectl -n kube-system get pvc pvc-traefik-nfs -o yaml >> workload.yaml
kubectl get pv pv-traefik-nfs -o yaml >> workload.yaml
```

- [ ] **Step 3: Record the certificate store without copying it**

```bash
POD=$(kubectl -n kube-system get pod -l app.kubernetes.io/name=traefik -o jsonpath='{.items[0].metadata.name}')
{
  echo "# acme.json as it was on 2026-11-21, before the ownership cutover."
  echo "# The file is not committed: it holds TLS private keys. It lives on the Retain"
  echo "# NFS volume pv-traefik-nfs (nas:/mnt/storage/traefik), a row of the backup"
  echo "# matrix in cluster/helm/backup/values.yaml."
  kubectl -n kube-system exec "$POD" -- sha256sum /data/acme.json
  kubectl -n kube-system exec "$POD" -- stat -c 'size=%s mode=%a owner=%u:%g' /data/acme.json
  kubectl -n kube-system exec "$POD" -- sh -c 'grep -o "\"domain\":" /data/acme.json | wc -l'
} > acme.txt
```

Expected: the hash `f074495f…`, `size=429905 mode=600 owner=65532:65532`, and a domain count recorded.

- [ ] **Step 4: Write the README**

`README.md` states, in plain sentences: what each file is; that the release is the K3s addon's at this point; that `acme.json`, Secrets and kubeconfigs are absent on purpose and where the certificate store is backed up; and that the directory is a one-off record, not an input to any sync. No relative Markdown links — `hack/check_docs_links.py` walks this path.

- [ ] **Step 5: Prove the snapshot contains no secret material**

```bash
grep -rlE 'BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY|password:|users:' \
  cluster/infrastructure/traefik/snapshot/2026-11-21/ || echo "no key material"
python3 - <<'EOF'
import glob, yaml
for p in glob.glob('cluster/infrastructure/traefik/snapshot/2026-11-21/*.yaml'):
    list(yaml.safe_load_all(open(p)))
print("all YAML parses")
EOF
wc -c cluster/infrastructure/traefik/snapshot/2026-11-21/*.yaml
```

Expected: `no key material`, `all YAML parses`, and roughly 430 KB of CRDs plus 190 KB of objects.

- [ ] **Step 6: Commit**

```bash
git add cluster/infrastructure/traefik/snapshot
git commit -m "📝 docs(traefik): snapshot the live configuration before ownership moves"
```

---

### Task 2: The owned values file

**Files:**
- Create: `cluster/infrastructure/traefik/values.yaml`
- Delete: `cluster/infrastructure/traefik/traefik-config.yaml`

**Interfaces:**
- Consumes: `snapshot/2026-11-21/release-values.yaml` and `release-manifest.yaml` from Task 1.
- Produces: `values.yaml`, the single input to the adoption upgrade in Task 6.

- [ ] **Step 1: Fetch the chart once and record where it came from**

```bash
cd /tmp && rm -rf traefik-25.0.3 && mkdir traefik-25.0.3 && cd traefik-25.0.3
kubectl get --raw "/static/charts/traefik-25.0.3+up25.0.0.tgz" > chart.tgz
tar xzf chart.tgz && grep -E '^version:|^appVersion:' traefik/Chart.yaml
```

Expected: `version: 25.0.3+up25.0.0` and `appVersion: v2.10.5`. That is the upstream chart `25.0.3` as K3s repackaged it, and the `+up25.0.0` is the version string the release already holds — which is why the upgrade changes no label. The upstream index starts at 42.x, so this static path is the only source for this version; Task 3 writes that down.

- [ ] **Step 2: Prove the snapshot values reproduce the live object**

```bash
helm template traefik /tmp/traefik-25.0.3/traefik -n kube-system \
  -f cluster/infrastructure/traefik/snapshot/2026-11-21/release-values.yaml > /tmp/baseline.yaml
python3 - <<'EOF'
import json, yaml
def docs(p):
    return {(d['kind'], d['metadata']['name']): d
            for d in yaml.safe_load_all(open(p)) if isinstance(d, dict) and d.get('kind')}
a = docs('/tmp/baseline.yaml')
b = docs('cluster/infrastructure/traefik/snapshot/2026-11-21/release-manifest.yaml')
print(sorted(a)); print("differences:", [k for k in set(a) & set(b)
      if json.dumps(a[k], sort_keys=True) != json.dumps(b[k], sort_keys=True)])
EOF
```

Expected: seven objects — `ClusterRole`, `ClusterRoleBinding`, `Deployment`, `IngressClass`, `IngressRoute`, `Service`, `ServiceAccount` — and `differences: []`.

- [ ] **Step 3: Write `values.yaml`**

```yaml
# The running Traefik release. Chart 25.0.3, Traefik 2.10.7 — the version that
# serves traffic, adopted from the K3s addon on 2026-11-21 without a version
# change. The 2.11 bump is W5 task 6; the v3 upgrade is W5 task 8.
#
# Chart source, because the upstream index no longer carries this version:
#   kubectl get --raw "/static/charts/traefik-25.0.3+up25.0.0.tgz"
# The file was derived from the release's own values and proven equal to the
# running object by a render diff; see snapshot/2026-11-21/README.md.

deployment:
  # One pod, not a DaemonSet on every node (task 482). Two processes writing
  # /data/acme.json corrupt it and re-request every name, and Let's Encrypt
  # allows five duplicates a week.
  kind: Deployment
  replicas: 1

# Recreate, not a rolling update: a rolling one runs both pods together for a
# few seconds, which is two certificate owners again.
updateStrategy:
  type: Recreate
  rollingUpdate: null

# The house router forwards 80, 443, 25565, 22000 TCP/UDP and 21027 UDP to
# 192.168.1.46, so this is where every external byte lands.
nodeSelector:
  kubernetes.io/hostname: streaming

image:
  repository: rancher/mirrored-library-traefik
  tag: "2.10.7"

priorityClassName: system-cluster-critical

tolerations:
  - key: CriticalAddonsOnly
    operator: Exists
  - key: node-role.kubernetes.io/control-plane
    operator: Exists
    effect: NoSchedule
  - key: node-role.kubernetes.io/master
    operator: Exists
    effect: NoSchedule

global:
  systemDefaultRegistry: ""

providers:
  kubernetesCRD:
    # Every gate and redirect is referenced as kube-system-<name>@kubernetescrd;
    # without this no cross-namespace reference resolves and every gate vanishes.
    allowCrossNamespace: true
  kubernetesIngress:
    publishedService:
      enabled: true

ingressRoute:
  dashboard:
    enabled: true
    entryPoints: ["web"]
    matchRule: Host(`traefik.kub.local`) && (PathPrefix(`/dashboard`) || PathPrefix(`/api`))
    middlewares:
      - name: lan-only
        namespace: kube-system
      - name: traefik-dashboard
        namespace: kube-system

metrics:
  prometheus:
    entryPoint: metrics
    addEntryPointsLabels: false
    addRoutersLabels: true
    addServicesLabels: false
    buckets: "0.5,1.0,2.5"
    # false restores the default internal router that serves prometheus@internal
    # on the metrics entry point. It was true with no custom router, so /metrics
    # answered 404 and no traefik_* series were stored (task 531).
    manualRouting: false

certResolvers:
  myresolver:
    email: valentin.ronteix@ashudev.com
    tlsChallenge: true
    # Must match the mounted claim; a different path means the store is empty
    # and every name is re-issued.
    storage: /data/acme.json

ports:
  web:
    port: 8000
    expose: true
    exposedPort: 80
    protocol: TCP
    forwardedHeaders:
      insecure: true
    proxyProtocol:
      insecure: true
  websecure:
    port: 8443
    expose: true
    exposedPort: 443
    protocol: TCP
    forwardedHeaders:
      insecure: true
    proxyProtocol:
      insecure: true
  mc:
    port: 25565
    expose: true
    exposedPort: 25565
    protocol: TCP
  # Syncthing: transfer (22000 TCP and UDP) and local discovery (21027 UDP).
  sttcp:
    port: 22000
    expose: true
    exposedPort: 22000
    protocol: TCP
  studp:
    port: 22000
    expose: true
    exposedPort: 22000
    protocol: UDP
  stdiscovery:
    port: 21027
    expose: true
    exposedPort: 21027
    protocol: UDP
  metrics:
    port: 9100
    expose: true
    exposedPort: 9101
    protocol: TCP

persistence:
  enabled: true
  name: data
  # The claim the router already mounts. It holds /data/acme.json.
  existingClaim: pvc-traefik-nfs
  path: /data

service:
  enabled: true
  single: true
  type: LoadBalancer
  # Rendered only when set: without it the API server defaults the Service to
  # SingleStack, which is not the object that serves today.
  ipFamilyPolicy: PreferDualStack
  spec:
    externalTrafficPolicy: Local
```

- [ ] **Step 4: Diff the final file against the baseline — expect exactly one line**

```bash
helm template traefik /tmp/traefik-25.0.3/traefik -n kube-system \
  -f cluster/infrastructure/traefik/values.yaml > /tmp/final.yaml
python3 - <<'EOF'
import json, yaml
def docs(p):
    return {(d['kind'], d['metadata']['name']): d
            for d in yaml.safe_load_all(open(p)) if isinstance(d, dict) and d.get('kind')}
a, b = docs('/tmp/baseline.yaml'), docs('/tmp/final.yaml')
print("same object set:", set(a) == set(b))
for k in set(a) & set(b):
    if json.dumps(a[k], sort_keys=True) != json.dumps(b[k], sort_keys=True):
        print("changed:", k)
        if k[0] == 'Deployment':
            aa = a[k]['spec']['template']['spec']['containers'][0]['args']
            bb = b[k]['spec']['template']['spec']['containers'][0]['args']
            print(" removed:", [x for x in aa if x not in bb])
            print(" added:  ", [x for x in bb if x not in aa])
EOF
```

Expected: `same object set: True`, `changed: ('Deployment', 'traefik')`, `removed: ['--metrics.prometheus.manualrouting=true']`, `added: []`. Anything else is a mistake in the file.

- [ ] **Step 5: Lint and delete the K3s file**

```bash
helm lint /tmp/traefik-25.0.3/traefik -f cluster/infrastructure/traefik/values.yaml
git rm cluster/infrastructure/traefik/traefik-config.yaml
grep -rn "traefik-config.yaml" --include='*.md' --include='*.yaml' --include='*.sh' . | grep -v 'docs/superpowers/'
```

Expected: lint `1 chart(s) linted, 0 chart(s) failed`; the grep lists only the files Task 3 rewrites plus the snapshot, which records the old object on purpose.

- [ ] **Step 6: Commit**

```bash
git add cluster/infrastructure/traefik/values.yaml
git commit -m "🚀 feat(traefik): own the values of the running release"
```

---

### Task 3: The runbook and the repository description

**Files:**
- Modify: `docs/runbooks/traefik-release.md` (whole file)
- Modify: `AGENTS.md` (section 2 and section 7 item 9)
- Modify: `docs/superpowers/plans/2026-11-11-w5-platform.md` (tasks 4 and 6)

**Interfaces:**
- Consumes: `values.yaml` and the snapshot paths from Tasks 1 and 2.
- Produces: the command list the operator runs in Task 5, and the page the next session reads for the 2.11 bump.

- [ ] **Step 1: Rewrite `traefik-release.md` around ownership**

Keep the page's existing shape (one-line summary, "use it when", the problem, before/after, traps, rollback) and change the content:

- the release is this repository's, installed from chart `25.0.3` with `cluster/infrastructure/traefik/values.yaml`; K3s's addon is disabled in `/etc/rancher/k3s/config.yaml` on the three servers, and the packaged objects are gone;
- the chart source is the K3s static path, because the upstream index starts at 42.x — say why and give the command;
- the upgrade procedure is `helm upgrade traefik <chart> -n kube-system --reset-values -f values.yaml`, preceded by a server-side dry run and a render diff;
- the ownership steps and their checks (Task 4 and Task 5 of this plan), with the finalizer warning and the `disable` merge warning;
- the rollback: restore the release from `snapshot/2026-11-21/release-values.yaml`, and if the addon has to come back, remove `disable: [traefik]`, restore the drop-in file, restart the servers one at a time;
- keep the traps table's existing rows that still apply, and add: deleting `HelmChart traefik-crd` before removing its finalizer deletes every route; `manualRouting` must stay `false`.

- [ ] **Step 2: Correct `AGENTS.md`**

- Section 2: `cluster/infrastructure/**` stays hand-applied, and Traefik's release is moved by hand with `helm` from `cluster/infrastructure/traefik/values.yaml`; the K3s addon is disabled.
- Section 7 item 9: record the ownership cutover on 2026-11-21 after the chart move, and that `traefik-config.yaml` is gone.
- Leave every other statement about placement, replicas and the certificate store as it is.

- [ ] **Step 3: Align the W5 plan's Traefik tasks**

In `docs/superpowers/plans/2026-11-11-w5-platform.md`, task 4 ("The official Traefik values file") becomes: the values file exists at `cluster/infrastructure/traefik/values.yaml` for chart `25.0.3`, and the 2.11 file is `values-2.11.yaml`, derived from it. Task 6 ("Production Traefik cutover to 2.11") loses its addon-disable step — that is done — and starts from an owned release at `25.0.3`. Do not renumber the tasks: cards `#9`, `#11` and `#476` cite them by number.

- [ ] **Step 4: Run the documentation checks**

```bash
python3 hack/check_docs_links.py
grep -rn "traefik-config.yaml" docs/ AGENTS.md | grep -v snapshot
```

Expected: `0 broken relative links`, and no live reference to the deleted file outside the snapshot.

- [ ] **Step 5: Commit**

```bash
git add docs/runbooks/traefik-release.md AGENTS.md docs/superpowers/plans/2026-11-11-w5-platform.md
git commit -m "📝 docs(traefik): the release is ours, not K3s's"
```

---

### Task 4: Remove the K3s uninstall finalizers

**Files:** none — cluster state only, recorded in Task 7.

**Interfaces:**
- Produces: `HelmChart` objects with no `wrangler.cattle.io/on-helm-chart-remove`, which makes the addon's delete path harmless for Task 5.

- [ ] **Step 1: Record the before state**

```bash
for n in traefik traefik-crd; do
  kubectl -n kube-system get helmchart $n -o jsonpath='{.metadata.name}{" "}{.metadata.finalizers}{"\n"}'
done
```

Expected: both list `["wrangler.cattle.io/on-helm-chart-remove"]`.

- [ ] **Step 2: Remove the finalizer from both**

```bash
for n in traefik traefik-crd; do
  kubectl -n kube-system patch helmchart $n --type=merge -p '{"metadata":{"finalizers":null}}'
done
```

- [ ] **Step 3: Confirm it stays off for a full reconcile cycle**

```bash
for i in 1 2 3 4 5 6; do
  kubectl -n kube-system get helmchart -o jsonpath='{range .items[*]}{.metadata.name}={.metadata.finalizers}{" "}{end}'; echo
  sleep 10
done
```

Expected: six lines with empty finalizers. A finalizer that comes back within a minute means the addon controller restores it; stop and re-patch immediately before Task 5 instead of leaving a gap.

---

### Task 5: Disable the addon on the three servers

**Files:** none — node configuration, run by the operator. The procedure is in `docs/runbooks/traefik-release.md` from Task 3.

**Interfaces:**
- Consumes: the finalizer-free `HelmChart` objects from Task 4.
- Produces: a cluster where K3s no longer declares Traefik, and the release untouched.

- [ ] **Step 1: Hand the operator the pre-flight**

Post these, and wait for the two results before anything else:

```bash
sudo k3s etcd-snapshot save --name pre-traefik-ownership
sudo ls -l /var/lib/rancher/k3s/server/db/snapshots/ | tail -3
kubectl -n kube-system cp "$(kubectl -n kube-system get pod -l app.kubernetes.io/name=traefik \
  -o jsonpath='{.items[0].metadata.name}')":/data/acme.json ./acme-2026-11-21.json
sha256sum ./acme-2026-11-21.json
```

Expected: a snapshot file listed, and the hash `f074495f…`.

- [ ] **Step 2: Hand the operator the per-node steps, one node at a time**

For `nuc0`, then `rbp2`, then `streaming` — never two control-plane nodes at once:

```bash
sudo cp /etc/rancher/k3s/config.yaml /root/config.yaml.bak-$(date +%F)
sudo grep -n '^disable' /etc/rancher/k3s/config.yaml || true
```

If `disable:` already exists, add `- traefik` under it and change nothing else. If it does not, append:

```yaml
disable:
  - traefik
```

Then, on the same node:

```bash
sudo rm -f /var/lib/rancher/k3s/server/manifests/traefik-config.yaml
sudo systemctl restart k3s
```

- [ ] **Step 3: Check each node before the next one**

```bash
kubectl get node <name> --no-headers
kubectl -n kube-system get helmcharts 2>&1
kubectl -n kube-system get jobs 2>&1
helm -n kube-system list
kubectl -n kube-system get pods -l app.kubernetes.io/name=traefik
```

Expected after every node: `Ready`; the `traefik` and `traefik-crd` `HelmChart` objects gone; `No resources found` for jobs; the release still `deployed` at revision 7; the pod still `Running`. Any `helm-uninstall-*` Job or a vanished release means the controller woke — stop and go to the rollback in the runbook.

- [ ] **Step 4: Confirm the addon did not come back**

After the third node, wait five minutes and re-run:

```bash
kubectl -n kube-system get helmcharts,helmchartconfigs 2>&1
kubectl -n kube-system get addons.k3s.cattle.io | grep traefik
```

Expected: nothing for `helmcharts`/`helmchartconfigs`, and no `traefik` addon row. If a `HelmChartConfig traefik` survives, delete it — the drop-in file is gone, so nothing will restore it.

---

### Task 6: Adopt the release

**Files:** none — cluster state only.

**Interfaces:**
- Consumes: `values.yaml` from Task 2 and the finalizer-free, addon-free cluster from Tasks 4 and 5.
- Produces: a release whose stored values are the committed file.

- [ ] **Step 1: Server-side dry run, then diff it against the live deployment**

```bash
helm upgrade traefik /tmp/traefik-25.0.3/traefik -n kube-system \
  --reset-values -f cluster/infrastructure/traefik/values.yaml --dry-run=server \
  > /tmp/dryrun.yaml
grep -A2 'metrics.prometheus.manualrouting' /tmp/dryrun.yaml || echo "manualrouting arg absent (expected)"
```

Expected: the arg line absent, no error, and the rendered `Deployment` otherwise identical to the live one (Task 2 Step 4 already proved this for the template; this confirms the server accepts it).

- [ ] **Step 2: Upgrade**

```bash
helm upgrade traefik /tmp/traefik-25.0.3/traefik -n kube-system \
  --reset-values -f cluster/infrastructure/traefik/values.yaml --wait --timeout 5m
```

Expected: `STATUS: deployed`, `REVISION: 8`. One pod restarts because the argument list changed; that is the metrics fix, and it is the only restart.

- [ ] **Step 3: Prove the release holds the file, not the old merge**

```bash
helm -n kube-system get values traefik > /tmp/after-values.yaml
python3 - <<'EOF'
import yaml
a = yaml.safe_load(open('/tmp/after-values.yaml'))
b = yaml.safe_load(open('cluster/infrastructure/traefik/values.yaml'))
print("identical:", a == b)
print("dead keys back?", 'podAnnotations' in a.get('deployment', {}),
      'ipFamilyPolicy' in a.get('service', {}))
EOF
```

Expected: `identical: True`, `dead keys back? False False`.

---

### Task 7: Verify end to end and record the result

**Files:**
- Modify: `cluster/infrastructure/traefik/snapshot/2026-11-21/README.md` (the after section)
- Modify: `docs/runbooks/traefik-release.md` (the performed date and the numbers)

- [ ] **Step 1: The certificate did not change**

```bash
POD=$(kubectl -n kube-system get pod -l app.kubernetes.io/name=traefik -o jsonpath='{.items[0].metadata.name}')
kubectl -n kube-system exec "$POD" -- sha256sum /data/acme.json
```

Expected: `f074495f…`, byte-for-byte the Task 1 value.

- [ ] **Step 2: Every route is still there**

```bash
for g in traefik.containo.us traefik.io; do
  for k in ingressroutes ingressroutetcps ingressrouteudps middlewares tlsoptions tlsstores; do
    printf '%-20s %-22s %s\n' "$g" "$k" "$(kubectl get $k.$g -A --no-headers 2>/dev/null | wc -l)"
  done
done
```

Expected: `68` ingress routes and `27` middlewares and `14` tlsoptions and `1` tlsstore on `containo.us`; `1` ingress route on `traefik.io` (the dashboard). Anything lower is a regression.

- [ ] **Step 3: Every host behaves as before**

```bash
for h in plex.kub.home.ashudev.com seerr.kub.home.ashudev.com ashuvidz.com ashudev.com \
         www.ashudev.com dashy.ashudev.com filebrowser.kub.home.ashudev.com \
         authentik.kub.home.ashudev.com; do
  printf '%-40s ' "$h"
  curl -s -o /dev/null -w '%{http_code}\n' --resolve "$h:443:192.168.1.46" "https://$h/"
done
curl -s -o /dev/null -w 'dashboard %{http_code}\n' http://traefik.kub.local/dashboard/
timeout 5 bash -c '</dev/tcp/192.168.1.46/22000' && echo "syncthing tcp open"
```

Expected: the same codes as the pre-flight capture, `dashboard 401`, `syncthing tcp open`.

- [ ] **Step 4: Metrics serve again**

```bash
kubectl -n kube-system exec "$POD" -- wget -qO- http://127.0.0.1:9100/metrics | grep -c '^traefik_'
```

Expected: a non-zero count. Then, once the 15-minute alert window has passed, confirm the target is up and `ScrapeTargetDown` for the Traefik pod has cleared.

- [ ] **Step 5: No duplicate routers**

```bash
kubectl -n kube-system logs -l app.kubernetes.io/name=traefik --since=15m |
  grep -iE "multiple|duplicate|conflict" || echo "no duplicate routers"
```

- [ ] **Step 6: The repository checkers**

```bash
hack/check-exposure.sh
uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests
python3 hack/check_docs_links.py
```

Expected: zero violations, zero failures, `0 broken relative links`.

- [ ] **Step 7: Record and commit**

Add the after numbers — revision, pod name, image, digest, route counts, host codes, metrics count — to the snapshot README and the runbook's "Performed" line.

```bash
git add cluster/infrastructure/traefik/snapshot/2026-11-21/README.md docs/runbooks/traefik-release.md
git commit -m "📝 docs(traefik): record the ownership cutover and its numbers"
```

---

### Task 8: Hand over the branch

**Files:** none.

- [ ] **Step 1: Rebase and run the suite on the rebased tree**

```bash
git fetch origin
git rebase origin/develop
hack/check-exposure.sh
uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests
python3 hack/check_docs_links.py
```

- [ ] **Step 2: Push and open the pull request**

```bash
git push -u origin feature/traefik-ownership
```

Then open a pull request against `develop` with the GitHub MCP. The body: what moved, the evidence (render diff, digest, route counts, host codes), what did not change (version, image, certificate), the node steps already run, and the follow-ups (`#9` remaining files, `#11` 2.11, `#466` ArgoCD, `#530`).

---

### Task 9: Reconcile the board

The board is Vikunja project 2, view 13, through the REST v2 API.

- [ ] **Step 1: Move to `Validation`, with a comment**

`#469`, `#464`, `#315`, `#6`, `#531` — each with the branch, the PR link, the verification output, and a comment naming what was actually observed.

- [ ] **Step 2: Rewrite in `Backlog`, do not move**

- `#9`: `values.yaml` for `25.0.3` is done; `values-2.11.yaml` (chart `27.0.2`) and `values-v3.yaml` (chart `42.4.1`) remain.
- `#11`: the cutover is done at `25.0.3`; this card is now only the 2.11 bump on an owned release.
- `#466`: unblocked — the release is separate from K3s, ArgoCD ownership is the remaining step.

- [ ] **Step 3: Comment only**

`#530`: the Traefik addon no longer depends on the dead helm-controller; `coredns`, `local-path` and `metrics-server` still do.

- [ ] **Step 4: Read the board back**

Re-read view 13 and confirm every card sits where this task says.
