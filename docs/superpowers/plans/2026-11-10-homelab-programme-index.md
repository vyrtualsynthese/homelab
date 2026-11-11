# Homelab rebuild — programme index

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Sequence the nine workstreams of the homelab rebuild so each one can be planned, executed and verified on its own.

**Architecture:** One K3s cluster, GitOps through ArgoCD for applications, manual Helm for ArgoCD and Traefik, Ansible for hosts. Configuration volumes on `local-path` pinned per node, media on NFS provisioner classes, offsite backups to OVH over the S3 API.

**Tech Stack:** K3s, Helm, ArgoCD, Traefik, PostgreSQL, Prometheus/Alertmanager/Loki/Alloy, restic, Ansible, Material for MkDocs, Vikunja, OpenMemory.

**Spec:** `docs/superpowers/specs/2026-11-10-homelab-stabilisation-design.md`

## Global Constraints

These apply to every plan in the programme, verbatim from the spec.

- One cluster, three control planes. etcd quorum is two of three. Never upgrade two servers at once.
- Free and open source software only. No Bitnami, in any form.
- Official Helm chart where one exists; otherwise an owned chart in `cluster/helm/`. No vendored upstream charts.
- Images from the upstream project or LinuxServer. Docker Hub only where no alternative exists, recorded in the spec's §8.7 table.
- `local-path` for configuration, NFS classes for media. Every pod pinned with `nodeSelector: kubernetes.io/hostname: <node>`; the `node-type` label is retired.
- No failover: `local-path` plus a node pin means node disk loss is total app loss. Backup is the mitigation.
- Secrets stay plain Kubernetes Secrets at runtime. The rebuild delivers an inventory plus one encrypted dump; SOPS comes after the platform upgrades.
- Files under `cluster/argocd/**` keep CRLF. Ingress is Traefik CRDs only.
- LinuxServer workloads keep `TZ=Europe/Paris`, `PUID=1100`, `PGID=1100`.
- The builder has no cluster write access. Every cluster-side step is a runbook the operator executes, ending in a verification the operator pastes back.
- Nothing is fixed with `kubectl apply` if it is managed by ArgoCD. Drift is fixed in git.

## Verification convention

This programme has no unit tests. A step's test is one of:

| Change type | Verification |
|---|---|
| Chart or values | `helm lint`, `helm template ... --namespace <ns>` with no error, and `kubeconform -strict` on the rendered output |
| ArgoCD Application | `argocd app diff` empty, then `Synced`/`Healthy` |
| Cluster operation | the exact `kubectl get` output that means success, pasted back |
| Runbook | re-run from a clean state and get the same result |

## Workstreams

| ID | Workstream | Produces | Depends on | Tasks | Sessions |
|---|---|---|---|---|---|
| W0 | Longhorn cleanup | Longhorn gone from cluster and repo; eleven apps listed for rebuild | — | 3 | 0.5 |
| W1 | Storage foundation | A small, correct set of StorageClasses; three broken Applications fixed | W0 | 9 | 1.5 |
| W2 | PostgreSQL | One PostgreSQL 17 on `local-path`, database-per-app, dump-backed | W1 | 7 | 1.5 |
| W3 | Application waves | Whole catalogue Synced/Healthy, or deleted on purpose | W1, W2 | 14 | 5 |
| W4 | Offsite | restic to OVH, Route 53 zones moved to OVH DNS, AWS credentials gone | W1 | 10 | 2.5 |
| W5 | Platform | K3s 1.36.x, Traefik v3, a rehearsed runbook | W4 recommended | 11 | 4 |
| W6 | Observability | Metrics, logs and alerts that would have caught this incident | W2, W5 | 9 | 2.5 |
| W7 | New services | filebrowser for the editor, OpenMemory, Vikunja | W2, W3 | 8 | 3 |
| W8 | Ansible and docs | Machine rebuild from git, documentation that is readable | W3, W4 | 10 | 2.5 |

**W4 is not urgent.** No data is being rescued and the operator accepts loss for now, so backups are
independent work rather than a gate. Its cost is that every workstream before it is unrecoverable.

## Task outlines

Titles only. Each becomes a full plan when its workstream is picked up.

### W0 — Longhorn cleanup (plan written: `2026-11-10-w0-cleanup.md`)

1. Inventory the stuck claims and the eleven affected applications
2. Stop those applications deliberately and clear the finalizers
3. Delete `longhorn-system` and the repo leftovers

### W1 — Storage foundation

1. Inventory the live StorageClasses against the spec's list of seven
2. Repair or retire `nfs-ssd`
3. Settle the qbittorrent downloads class
4. Collapse `plex-media` and `nfs-plex` into one
5. Retire `nfs-qbittorrent-config` and the `nfs-ultrafaststorage` reference
6. Fix the `plex-media` Application path
7. Fix the `nfs-audio` values path
8. Convert `nfs-qbittorrent-config` to multi-source
9. Pilot: one app end to end on `local-path` with a hostname pin

### W2 — PostgreSQL

1. Write the StatefulSet chart on the official image, pinned by digest
2. Create the databases and roles as SQL in git
3. Enable `pgvector`
4. Point the consuming apps at the new instance
5. Add the per-database dump CronJob
6. Prove a restore into a scratch instance
7. Remove the Bitnami chart and the `postgres-secrets` shape it required

### W3 — Application waves

1. Wave A: the six media apps missing config PVCs
2. Wave A: discourse, koillection, news-freshrss
3. Wave A: postiz, minecraft, monitorss
4. Wave A: wiki and ghostfolio, both crash-looping
5. Wave B: delete readarr and maintainerr-anime
6. Wave B: delete the eight empty namespaces and the minio chart
7. Wave C: chart shape, pins and probes across the media stack
8. Wave C: fix the sonarr exportarr port
9. Wave C: firefly and postiz onto Traefik CRDs
10. Wave C: authentik rebuilt as SSO
11. Wave C: image and registry migration per §8.7
12. Wave D: the NFS Applications and leftover dead directories
13. Codify sync policy per wave: write `automated` + `selfHeal` + `prune` into the Applications, matching what the UI already does
14. Final sweep: no Pending pods, no namespace without an owner

### W4 — Offsite

1. Choose the OVH region and create the object storage bucket
2. restic repository over the S3 API, credentials out of `/etc/environment`
3. Back up `local-path` volumes and the PostgreSQL dumps
4. Back up the K3s etcd snapshots off-box
5. Export the Route 53 zones to the repo
6. Create the OVH zones and validate the records
7. Lower TTLs, switch NS, verify, delete Route 53
8. Remove the AWS repository, keys and the old backup playbook
9. Schedule, retention and the backup-age alert
10. Run and record a restore drill

### W5 — Platform

1. Build the throwaway three-VM cluster on the NAS
2. Rehearse an etcd snapshot and restore on it
3. Install the official Traefik chart on the test cluster, 2.11
4. Cut production over to the official chart, 2.11, reusing the ACME claim
5. Rename IngressRoutes to `traefik.io/v1alpha1`, namespace by namespace
6. Upgrade Traefik to v3 and fix the routing regressions
7. K3s 1.31 on the servers, one at a time
8. K3s 1.32 and 1.33
9. K3s 1.34 to 1.36.x, then the NAS agent
10. Validate the GPU stack on `streaming` after every step
11. Delete the test cluster and publish the runbook

### W6 — Observability

1. Remove the vendored Prometheus and Grafana
2. `kube-prometheus-stack` as pattern B, TSDB on `local-path`
3. ServiceMonitors for mktxp, pihole-exporter, smartctl
4. Alertmanager to Discord from a secret
5. The alert rules that would have caught this incident
6. Loki and Alloy, 90-day retention
7. Dashboards as files in git
8. Traefik metrics and the ingress dashboards
9. Deliberately break one thing and confirm the alert fires

### W7 — New services

1. Authentik forward-auth pattern, proven on one host
2. filebrowser: storage, mounts, SSO, editor scope
3. filebrowser: public exposure, rate and upload limits
4. Prove the editor workflow end to end
5. PostgreSQL schema and pgvector for OpenMemory
6. OpenMemory with its MCP endpoint
7. Vikunja with its API token and MCP server
8. The operator↔agent protocol document

### W8 — Ansible and docs

1. Inventory, groups and `group_vars` into the repo
2. `setup-node.yaml` rewritten for the real Ubuntu version
3. containerd, K3s role, token and config on the node
4. Node labels and the retirement of `node-type`
5. NFS exports and the GPU host preparation
6. `upgrade-all.yaml` replaced by an ordered, serial procedure
7. Material for MkDocs scaffold and the docs index
8. Architecture document with Mermaid diagrams
9. Runbooks: upgrade, restore, add a node, rotate a secret
10. Disaster-recovery document, proved by one dry run

## Priority

**Operator's order, 2026-11-11: W5 first, W6 second.** Everything else follows.

That inverts the dependency order and has two consequences worth stating once:

1. **W5 runs with no restore path.** Backups (W4) are deferred, so an etcd or storage failure during
   six minor platform upgrades is a rebuild-from-git rather than a restore. What the cluster holds
   today is mostly reproducible, so the real cost is downtime rather than data — but it is a
   deliberate one-way door.
2. **W5 runs as the baseline is still noisy.** 20 pods `Pending` and three crash-looping mean that
   after each upgrade step, a new failure is hard to distinguish from the existing ones. **W0 is half
   a session** and buys a clean baseline; it is worth taking before W5 even though the operator did
   not order it that way.

Revised sequence: `W0 (recommended) → W5 → W6 → W1 → W2 → W3 → W7 → W4 → W8`.

## Recommended order

The dependency order, kept here for reference: W0 → W1 → {W2, W3} → W5 → W6 → W7 → W8, with W4
anywhere the operator wants it. The operator has chosen to start with W5.

W5 (platform) and W6 (observability) are first by operator decision. Read their dependency notes
above before starting: W5 carries the most risk in the programme because nothing backs it up, and W6
is easiest to verify once the upgrade noise is gone.
