# Homelab stabilisation and rebuild — design

Date: 2026-11-10
Status: draft for operator approval
Supersedes: `REBUILD.md` and `TODO.MD` once approved (both are re-cut here)

---

## 1. Intent

The homelab runs day-to-day services on a single K3s cluster of four hand-built nodes. It lost
its storage layer to a Longhorn failure and has not been recovered. The goal is a cluster that is
coherent again — every application either working or deliberately gone — with backups that have
been tested, a platform that is not four years behind, and three new capabilities on top:
file sharing for the editor, memory for the LLM, and a task hub that joins project todos to the
LLM's todos.

The reason this matters beyond "make it work": every incident so far was survivable in principle
and fatal in practice because there was no backup, no alerting, and no document saying what the
machines were. The rebuild is as much about removing those three failure modes as about restoring
the apps.

## 2. Where things stand

Full evidence in `docs/audit/{observability-storage,argocd-helm,ansible-hosts}.md`.

**Storage.** Longhorn is gone: the `longhorn-system` namespace has zero pods, the
`longhorn`/`longhorn-static` StorageClasses no longer exist, and 36 PVs plus 11 PVCs are stuck
`Terminating` with `driver.longhorn.io` finalizers that nothing can clear.

**Apps.** 57 ArgoCD Applications. 30 are fully healthy. Roughly 20 pods have been `Pending` for 68
days because their PVC objects were deleted. Three apps have been crash-looping for 92 days
(17 723, 18 737 and 12 432 restarts). Eleven apps still run on volumes that are mid-deletion; the
data is readable today and dies on the next pod restart.

**GitOps automation is configured outside git.** No Application manifest records
`syncPolicy.automated` — the block was removed from all of them, and a root-app sync therefore
removes it from the live objects. The applications themselves *are* reconciled — the operator enables
auto-sync from the ArgoCD UI, because toggling it off there is faster than editing git while
debugging pods. The drift is therefore not "half the catalogue is unmanaged", it is that the
Application objects in git do not describe the cluster, and a root-app sync that touches them can
silently reset a setting the operator set by hand. Fixing this is a five-minute-per-app codification
job, not a repair.

**Backups.** None work. The only offsite target is an AWS S3 bucket. The k3s and NAS hosts have
had a daily cron since 2024 pointing at a script that was never deployed to them. The k3s script
dumps a SQLite file that does not exist on an etcd cluster. The NAS script backs up a MinIO
directory that was deleted in July 2024.

**Platform.** K3s `v1.30.1+k3s1` against a current stable of `v1.36.x`. Traefik `2.9.4` (June
2023) running from the K3s bundled addon. 106 repo files and 58 live objects use
`traefik.containo.us/v1alpha1`; none use `traefik.io/v1alpha1`.

**Observability.** Prometheus has alerting fully disabled and no remote write. Grafana provisions
zero dashboards and has been `Pending` for 68 days because its claim is gone. Loki is a 32-line
values file with no chart, no Application and no log shipper — there is no log pipeline at all.

**Ansible.** Cannot run: no inventory, no `group_vars`, no `ansible.cfg` in the repo or its
history. It also does not install K3s, containerd, node labels, NFS exports or the GPU stack, so
it could not rebuild these machines even if it ran.

## 3. Goals

1. Every application in the catalogue either `Synced`/`Healthy` on stable storage, or deleted on
   purpose and recorded as such.
2. A working PostgreSQL that serves every app that needs one, on the official image, backed up.
3. Offsite backup on a European provider, covering the control plane, PVC data, databases and
   configuration, with a restore that has actually been performed.
4. K3s and Traefik on supported versions, upgraded through a rehearsed, reversible procedure.
5. Observability that would have caught this incident: alerts on Pending pods, unbound claims,
   crash loops, certificate expiry, disk pressure and stale backups.
6. Filebrowser serving the video and asset libraries to the editor.
7. LLM memory in Postgres, reachable by agents over MCP.
8. Vikunja as the task hub, with an API and MCP access for the LLM.
9. Documentation that lets someone else operate this: architecture, runbooks, secret inventory,
   disaster recovery.

## 4. Non-goals

- Rebuilding Longhorn or introducing any replicated storage layer.
- A second permanent cluster, or a "staging" environment.
- Offsite backup of the media library (multi-terabyte, replaceable, cost-prohibitive).
- Migrating applications to a different orchestrator.
- A shared multi-tenant platform; this is one operator's homelab.

## 5. Anti-goals

- **Any big-bang cutover.** Every step must be individually reversible.
- **A partial restart.** With no rescue, a pod that touches a doomed claim loses its data *and*
  fails to start, leaving the app half-dead with no record of why. Each of the eleven is either
  rebuilt now or stopped deliberately, never left to restart on its own.
- **Fixing drift with `kubectl apply`.** If it is in ArgoCD, it changes in git.
- **Reintroducing Bitnami, or any vendor whose chart licence can change under us.**

## 6. Constraints

- **One cluster, three control planes.** etcd quorum is two of three. Never upgrade two servers
  at once. There is no environment to test in — one must be created.
- **Free and open source only.** No proprietary licences, no Bitnami charts ever, no source-
  available-with-restrictions products.
- **Official Helm charts first, own charts second.** Remote official chart plus values in the repo
  (pattern B) where one exists. Where none exists, the chart is written in this repo (pattern A) —
  the operator maintains the charts. No vendored upstream charts.
- **Official images only, and not from Docker Hub.** An image must come from the upstream project or
  from LinuxServer, and must be pulled from `ghcr.io`, `quay.io`, `registry.k8s.io`, `lscr.io` or
  the project's own registry. Docker Hub is a last resort. No third-party repackagers, and no image
  built or maintained by the operator.
- **`local-path` for configuration, NFS classes for media.** Confirmed by the operator. Every
  workload pinned to a named node.
- **No failover.** `local-path` plus a node pin means a node's disk loss is an app's total loss.
  Backup is the only mitigation, which is why it cannot be last.
- Secrets never in git. Files under `cluster/argocd/**` keep CRLF. Ingress is Traefik CRDs only.
  LinuxServer workloads keep `TZ=Europe/Paris`, `PUID=1100`, `PGID=1100`.
- **The eleven volumes are already gone.** Their contents are accepted as lost and every affected
  application is reconfigured from scratch. Nothing in this programme attempts to recover them, and
  no session is spent on it.

## 7. Decisions taken

| Decision | Value | Chosen by |
|---|---|---|
| Catalogue scope | Restore all, staged in waves | operator |
| Placement | Every pod pinned to a named node | operator |
| Config storage | `local-path`, node-bound | operator |
| Media storage | NFS provisioner classes | operator, existing |
| PostgreSQL | Restore as a real working instance, official image | operator |
| Authentik | Keep, and rebuild its configuration — it was a proof of concept and is still broken | operator |
| Charts | Official chart where one exists, otherwise the operator's own chart | operator |
| Images | Official upstream or LinuxServer only; no self-maintained images | operator |
| Backup | Rebuild from scratch, European provider → **OVH** | operator |
| LLM memory | **OpenMemory (Mem0)** on PostgreSQL | operator |
| Task hub | **Vikunja** | operator |
| Public DNS | Move from AWS Route 53 to OVH; registration only if forced | operator |
| Authentik scope | SSO for every app eventually; internet-facing services first | operator |
| Node pinning | `kubernetes.io/hostname` only; the `node-type` label is retired | operator |
| Software licence policy | Free and open source only, never Bitnami | operator |
| Scope of offsite backup | Config, databases, documents — not media | this design |

## 8. Cross-cutting conventions

These are decisions this design makes. Change them by changing this document.

### 8.1 Placement map

Each workload declares `nodeSelector: kubernetes.io/hostname: <node>` in its values, and its
`local-path` claim lands on that same node. A pod and its config volume must never be movable
independently.

| Node | Role | Capacity | Hosts | Rationale |
|---|---|---|---|---|
| `streaming` | GPU + media | SSD ≥ 1 TB | Plex, the `*arr` stack, qbittorrent, autobrr, cross-seed, unpackerr, flaresolverr, seerr, calibre, kavita, tautulli, maintainerr, recyclarr | Plex needs the GPU; the rest stream from the NAS over NFS anyway |
| `nas` | Storage + data-adjacent | ~30 TB HDD + 2 TB NVMe | PostgreSQL, filebrowser, syncthing, stash, Prometheus TSDB, Loki, the restic working copy, and every NFS provisioner chart | The disks live here; config volumes on the same box as the data they serve |
| `nuc0` | Application catalogue | SSD ≥ 1 TB | authentik, dashy, wiki, firefly, guacamole, koillection, linkstack, ghostfolio, discourse, postiz, n8n, monitorss, minecraft, news-freshrss, Vikunja, OpenMemory | General-purpose x86, 32 GB |
| `rbp2` | Control plane only | SSD | nothing | A Pi with an SSD, ARM64. Keeping it app-free avoids the multi-arch image problem entirely |

Disk is therefore **not** a constraint anywhere, and placement needs no special handling: the NAS
root filesystem is on a **dedicated SSD used only for `local-path`**, so config volumes, PostgreSQL,
the Prometheus TSDB and Loki all land on flash without extra configuration. The 30 TB mechanical
array holds media and the restic working copy. The NVMe is not part of this design.

The binding constraints are RAM on `nuc0` and the fact that `local-path` ties a volume to one node —
which is exactly why the operator pins every pod rather than relying on a label.

`nuc0` is currently at ~28.5 GB of memory used and is the node to watch. `rbp2` reports ~4.3 GB
used. Before wave C, measure real capacity per node and rebalance — the map above is a starting
position, not a measured one.

### 8.2 Storage classes

Keep a small, named set and delete the rest:

| Class | Purpose | Backing |
|---|---|---|
| `local-path` (default) | all configuration volumes, 128Mi unless noted | node disk |
| `nfs-plex` | video library | `/mnt/storage/nfs/plexmedia` |
| `nfs-audio` | music | NAS export |
| `nfs-books` | books | NAS export |
| `nfs-stash` | assets and documents | NAS export |
| `nfs-downloads` | qbittorrent output | NAS export |
| `nfs-syncthing` | syncthing folder | NAS export |

Retire: `nfs-ssd` (its provisioner has been `ContainerCreating` for 68 days), `plex-media`
(duplicate of `nfs-plex`, two charts for one export), `nfs-qbittorrent-config` (unused),
and every reference to `nfs-ultrafaststorage` (a class that does not exist).

PVC naming: `<app>-config` for configuration. Configuration volumes are sized 128Mi and grow on
demand — the current mix of 1Gi through 50Gi config claims is a symptom of guessing.

### 8.3 Chart policy

1. **Official chart as a dependency.** Where the upstream project publishes a Helm chart, the
   repo owns a small chart in `cluster/helm/` whose `Chart.yaml` pins that chart to an exact
   `version:`, and whose `values.yaml` pins the image repository and tag of every container it
   renders. The chart version never lives in an Application's `targetRevision`. `charts/` and
   `Chart.lock` are not committed — ArgoCD runs `helm dependency build` at sync time, and a
   committed lock goes stale on the first Renovate bump.
2. **Otherwise, an owned chart.** The operator maintains one chart per app in `cluster/helm/`.
   These are first-class, not scaffolding, and are kept to a single shape:
   - image pinned by tag, never `latest`
   - `strategy: Recreate` for anything with a configuration volume
   - explicit `resources` (memory request and limit on every container)
   - explicit `nodeSelector` from the placement map
   - liveness and readiness probes
   - `securityContext` that drops capabilities and avoids root where the image allows it
   - one `values.yaml` per environment, no per-app template forks of the same logic
3. **No vendored charts.** `postiz` is the last one and migrates to rule 1 on its next touch. Until
   then it is exempt by name, not by category.
4. **No Bitnami.** Not as a chart, not as an image.

### 8.4 Sync policy

Today auto-sync is switched on per Application **from the ArgoCD UI**, not from git. That is a
deliberate habit — toggling it off in the UI is faster than a commit when debugging a pod — and it
works. The cost is that the Application manifests in git understate the real policy, so a root-app
sync that rewrites an Application can silently disable automation the operator turned on by hand.

The rebuild keeps that habit, and it is now a rule rather than drift to be tidied away:

- **No Application in `cluster/argocd/projects/` records `syncPolicy.automated`.** The manifests
  keep only what is structural: project, destination, `syncOptions: [CreateNamespace=true]`, and the
  source. Auto-sync is the operator's switch, in the UI, per Application.
- A root-app sync rewrites an Application to match git, so it removes the `automated` block from the
  live object and automation stops. That is expected, not a sync bug: re-enable it in the UI.
- Debugging therefore keeps its fast path — toggle off, work, toggle on — with nothing to commit
  either way.
- A PR that adds `automated:` is a regression, however convenient it looks, and it is the one thing
  to look for when an Application stops reconciling after a root-app sync.

### 8.5 Secrets

**Unchanged:** Kubernetes Secrets remain the runtime mechanism. Charts keep referencing
`existingSecret` and `secretName` exactly as they do today. Nothing here changes how an app
consumes a secret.

Today those Secrets are created by hand and no record of their contents exists anywhere. That is
why `AGENTS.md` §5 cannot be verified, and why a rebuild starts by guessing which keys each chart
needs. The design problem is not "Kubernetes Secrets are wrong" — it is "there is no source of
truth for what goes in them".

Two levels, and this is a preference to confirm rather than a technical requirement:

- **Level 1 — inventory plus encrypted dump (the minimum).** `docs/secrets.md` records every
  Secret: name, namespace, keys, consumer, and where the value comes from. An encrypted
  `secrets.yaml` (age, one file) joins the backup set so a rebuild can restore the values. Secrets
  are still created by hand; ArgoCD still cannot see them. This is a day's work and it removes the
  "nobody knows what is in there" failure.
- **Level 2 — SOPS in git.** Same inventory, but values live encrypted in the repo and ArgoCD
  decrypts them at render time through a KSOPS plugin in the repo-server. This is what lets an app
  with a secret be `selfHeal`-ing, and what makes the whole cluster reproducible from git alone.

Recommendation: **Level 1 in this rebuild, Level 2 as a follow-up.** Every wave in W3 has to
recreate Secrets by hand anyway, and doing that once with an inventory in place is cheap. SOPS
adds a plugin to the render path, which is a platform change — it belongs after the upgrades in
W5, not before them on a cluster that is still broken.

Migration detail that applies either way: export the current values with an admin session **before
Wave A**, because the read-only ServiceAccount cannot read Secrets and the old values are the only
record. For each one, decide preserve (a login someone uses) or rotate (a leftover), and record the
decision in `docs/secrets.md`.

Rejected: **Sealed Secrets** — decryption is bound to a cluster key, so a rebuild loses every
secret. That is the failure mode this workstream exists to remove.

### 8.6 Documentation

`docs/architecture.md` (what runs where and why), `docs/runbooks/*.md` (one per routine operation,
including the upgrade runbook and the restore runbook), `docs/decisions/` (one short record per
decision, starting with this document's table), `docs/secrets.md` (name, namespace, keys, consumer,
never the value), `docs/disaster-recovery.md` (how to rebuild from an empty disk).

**Reading surface: Material for MkDocs.** Markdown in git stays the source of truth — that is what
agents read and what pull requests review — and MkDocs renders the same files into a site with
navigation, search and a proper table of contents for a human. It is free and open source, there is
no database and no state to back up, and the build is one command. Mermaid diagrams are supported
through `pymdownx.superfences`, so graphs stay as code in the Markdown rather than becoming
screenshots. GitHub also renders Mermaid natively, so the repository is readable without the build
step at all.

Serve the built site read-only from the cluster if a browsable URL is wanted; it is a static
directory, so a plain web server and a Traefik route are the whole deployment, and losing it costs
nothing.

### 8.7 Image and registry policy

Two rules, in order:

1. **Provenance.** The image comes from the upstream project itself — its own registry, `quay.io`,
   `ghcr.io` under the project's organisation, or LinuxServer (`lscr.io/linuxserver/*`). No
   third-party repackagers, no images the operator builds or maintains.
2. **Registry.** Pull from `ghcr.io`, `quay.io`, `registry.k8s.io`, `lscr.io`, or a project's own
   domain (`docker.n8n.io`, for example). **Avoid Docker Hub.**

Where the two rules conflict, rule 1 wins: an image that exists officially only on Docker Hub is
used rather than substituting an unofficial source. Every such image is listed below, so the
exception surface is one visible list instead of scattered tags.

| Image | Registry with no official alternative | Note |
|---|---|---|
| `postgres` | Docker Hub official only | **The one that matters.** See the note after this table. |
| `busybox` | Docker Hub official only | init containers; five files |
| `alpine` | Docker Hub official only | `qbittorrent` template, currently `:latest` |
| `discourse/discourse` | Docker Hub only | upstream publishes nowhere else |
| `guacamole/guacamole`, `guacamole/guacd` | Docker Hub only | Apache publishes there |
| `vikunja/vikunja`, `mem0/openmemory-mcp` | Docker Hub only | verify on adoption; move if GHCR appears |

Likely moves out of Docker Hub, to be verified image by image before swapping:

| Current | Likely target |
|---|---|
| `sonarr`, `radarr`, `lidarr`, `prowlarr`, `bazarr`, `tautulli`, `calibre`, `kavita` | `lscr.io/linuxserver/*` — LinuxServer runs its own registry, so this also satisfies rule 2 |
| `filebrowser/filebrowser`, `ghostfolio/ghostfolio`, `firefly-iii/firefly-iii`, `requarks/wiki` | `ghcr.io/<project>/<project>` |
| `n8nio/n8n` | `docker.n8n.io/n8nio/n8n` |
| `traefik` | `ghcr.io/traefik/traefik` with the official chart |

Third-party images that must be replaced on provenance grounds, independent of registry:

| Current image | Where | Replacement |
|---|---|---|
| `jacklul/pihole` | `rbp0`, `rbp1` compose | the Pi-hole project's own image |
| `matusnovak/prometheus-smartctl` | `rbp0` compose | `prometheus-community`'s exporter |
| `eko/pihole-exporter` or equivalent | `cluster/helm/monitoring/pihole-exporter` | verify an upstream exporter exists; if not, the app goes |

Where no official image exists at all, the app is a candidate for deletion rather than for a
third-party image — the catalogue is being pruned in the same pass.

**The PostgreSQL exception.** The official PostgreSQL image is published only on Docker Hub. Two
ways out, and the choice belongs to wave W2:

- accept the single Docker Hub exception and pin the official image by digest; or
- use `ghcr.io/cloudnative-pg/postgresql`, which is a project-published image on GHCR and runs
  standalone, but is built for the CloudNativePG operator and carries that project's expectations.

Recommendation: keep the official image, pinned by digest, and record it as the one deliberate
Docker Hub dependency. Switching the image engine and the image source in the same wave doubles the
risk on the component that everything else depends on.

### 8.8 Where each kind of knowledge lives

Four kinds of information, four homes, no overlap. The failure mode this prevents is visible in the
repo today: `TODO.MD` and `REBUILD.md` disagree with each other and with the cluster, because both
are half-maintained stores of work state in a format nobody updates.

| Kind | Lives in | Read by | Rationale |
|---|---|---|---|
| Decisions and designs | git, `docs/decisions/` and `docs/superpowers/specs/` | operator and agents, as files | Reviewed in a pull request; travels with the code; survives any service dying |
| Evergreen knowledge (architecture, runbooks, secret inventory) | git, `docs/*.md` | operator and agents | Same reasons; agents can grep it, which no wiki API improves on |
| Work state (what is being done, by whom, blocked, done) | **Vikunja only**, via API and MCP | operator and agents | Git is a poor queue; state changes many times a day and must not require a commit. This is the operator↔agent hub. |
| Agent recall (facts learned, preferences, past mistakes) | **OpenMemory**, backed by PostgreSQL | agents | Needs semantic recall, not exact paths; per-agent scoping |

Rules that follow:

1. **No second todo store.** If it is work, it is a Vikunja task. No markdown todo lists, including
   in `docs/`. `TODO.MD` dies with this rebuild.
2. **No decisions in the task tracker.** A task links to a decision, it does not contain one.
3. **Documentation is files, not a service.** A wiki is a *reading surface*, never the source of
   truth. The existing Wiki.js deployment may keep a read-only mirror for phone access; if it dies,
   nothing is lost.
4. **The agent protocol is explicit.** An agent claims a Vikunja task, works, and reports back with
   what changed and what it verified. A task that an agent cannot claim and close through the API is
   not a task an agent should be given.

Why not put documentation in a service (Outline, BookStack, Docmost): every one of them adds an API,
an auth path and a second backup to a cluster that is currently recovering from having too many of
each. None of them makes an agent better at finding the answer, because the hard part is knowing
which document is authoritative — and that is solved by an index and a naming convention, not by a
database.

## 9. Workstreams

Each workstream has a scope and an acceptance test. They are ordered by dependency, not by
importance — the operator sets priority.

**Delivery format.** The builder has no write access to the cluster: the read-only ServiceAccount
can read and cannot `exec`, patch, delete or read Secrets. Every cluster-side step is therefore
written as a runbook the operator executes — exact command, expected output, and what to do when
the output differs. Repo work (charts, values, Applications, documentation, scripts) is written
directly. Each runbook step ends with a verification the operator pastes back, so the workstream
can proceed without a second session re-investigating the cluster.

### W0 — Longhorn cleanup

No data is rescued. The operator is reconfiguring every application from scratch, so the eleven
volumes that still exist are abandoned and their contents are accepted as lost. That removes the
only time-critical step in the programme and removes the "do not restart anything" constraint that
governed every other workstream.

1. Confirm which claims are still `Terminating` and which pods are still holding them, so the set
   is accurate on the day rather than from this document.
2. Stop the workloads that depend on them: delete the eleven Applications' pods by letting each
   Wave A/C rebuild recreate them, or scale them to zero first. Whatever is chosen, it is done
   deliberately and recorded, not by casually syncing an Application.
3. Remove the finalizers from the stuck PVCs and PVs, then delete `longhorn-system`.
4. Delete the Longhorn leftovers from git: `cluster/infrastructure/storageclass/lh-*.yaml`,
   `kubectl/ingressRoute/longhorn.yaml`, and the Longhorn references in `AGENTS.md`.
5. Verify: no `longhorn` string in the cluster or the repo outside the historical notes in `docs/`.

Acceptance: no `longhorn` object in the cluster, no `longhorn` reference in the repo outside
`docs/`, and a written list of which applications are now awaiting a fresh configuration.

The eleven affected applications are: filebrowser, calibre, firefly, linkstack, n8n, syncthing,
thelounge, pgadmin, Prometheus and postiz redis. Every one of them is either rebuilt in Wave A or
Wave C, or replaced wholesale in W6.

### W1 — Storage foundation

1. Repair or retire `nfs-ssd`: its provisioner cannot mount its own export root.
2. Settle the qbittorrent downloads class — one class, one name, referenced by the chart.
3. Collapse the duplicate classes in §8.2.
4. Rewrite `nfs-qbittorrent-config` as a multi-source Application like its siblings.
5. Fix the three broken Applications: `plex-media` path, `nfs-audio` values path,
   `nfs-qbittorrent-config`.
6. Establish the `local-path` + node-pin pattern in the chart template and apply it to one pilot
   app end to end.

Acceptance: every StorageClass in the cluster is in §8.2; every bound claim resolves; the pilot
app survives a pod delete and a reschedule attempt (it should stay on its node).

### W2 — PostgreSQL

1. Plain StatefulSet on the official `postgres` image, current stable major, `local-path` on
   `nas`, `strategy: Recreate`.
2. One instance, one role and one database per consuming app. No shared superuser.
3. Enable `pgvector` for OpenMemory.
4. Initialisation as SQL in git, applied by a Job — not by hand.
5. Backup: `pg_dump -Fc` per database on a schedule into OVH, plus the base backup path in W4.

Acceptance: every database restores from its dump into a scratch instance; no application holds
more privilege than it needs; no Bitnami chart remains.

### W3 — Application waves

Four waves, each ending `Synced`/`Healthy` and each getting automation switched on.

- **Wave A — the broken sixteen.** bazarr, bazarr-anime, kavita, lidarr, maintainerr, tautulli,
  koillection, discourse, news-freshrss, postiz, minecraft, monitorss, wiki, ghostfolio, and the
  two database consumers from W2. New `local-path` config volumes, restored from the W0 copies
  where a copy exists, fresh otherwise.
- **Wave B — deletions.** readarr (abandoned upstream), maintainerr-anime (duplicate), and the
  empty namespaces: nextcloud, synapse, inventree, inventree-lego, minio, orbital-sync, postfix,
  test. Plus the orphan chart `cluster/helm/nfs/minio-nfs`.
- **Wave C — the healthy but hand-built.** Migrate to the §8.3 chart shape, pin every image, add
  probes, fix the sonarr exportarr port (7878 → 8989 on an 8989 service), move firefly and postiz
  off native Ingress onto Traefik CRDs, and move every image to a GHCR, quay, `lscr.io` or
  project-registry source per §8.7 — verifying each target exists before swapping.

  Include **authentik**, which runs but was a proof of concept and is still broken: its forward-auth
  callback 404s for filebrowser, so the flow was never finished. Rebuild its configuration properly
  — one forward-auth provider and application per host, in git — rather than repairing the ad-hoc
  state. This is a prerequisite for W7.1, not an optional tidy-up.
- **Wave D — the NFS apps and leftovers.** Dead chart `cluster/helm/databases/pgadmin`,
  `cluster/helm/localpath/` reference in AGENTS.md, and the `renovate` version mismatch between
  Application and values.

Acceptance: no `Pending` pods cluster-wide; every Application `Synced`/`Healthy` with automation;
no namespace without an owner.

### W4 — Offsite: domains, DNS and backups

The AWS exit starts here and is one workstream, not two.

1. **Domains.** The public zones for `ashuvidz.com` and `ashudev.com` live in **AWS Route 53** and
   move to OVH. Two separate moves, only one of them decided:
   - **Public DNS → OVH.** Decided. Route 53 has no zone export, so the record sets come out through
     the API (`list-resource-record-sets` per hosted zone), get converted to a zone file, and are
     committed to the repo before anything is switched. Lower TTLs to the provider minimum at least
     48 hours ahead, create the zone at OVH, switch the NS records at the registrar, verify every
     name, then delete the Route 53 hosted zone. Watch for records that only make sense inside AWS —
     alias records, health checks, DNSSEC keys — because those do not survive the move as-is.
   - **Registration.** Optional and not recommended. A transfer means a registrar lock period and a
     second DNS cutover. Recommendation: move only the DNS unless renewal price or terms force a
     transfer. If it does move, keep the transfer auth codes offline.
   - The concentration-risk note below applies to whichever path is taken.
2. **Backups.** restic to OVH Object Storage over its S3-compatible API, EU region.
   - Control plane: K3s's own etcd snapshots, uploaded off-box.
   - PV data: every `local-path` config volume and the PostgreSQL data directory.
   - Databases: the W2 dumps.
   - Configuration: Ansible inventory and `group_vars`, Pi-hole and wg-easy configs, SOPS-encrypted
     secrets, and `docs/`.
   - Schedule, retention (7 daily, 4 weekly, 12 monthly), and a **monthly restore drill** recorded
     in `docs/runbooks/restore.md`.
3. **Remove the old path.** Delete the AWS S3 repository, the AWS keys, and the `/etc/environment`
   plaintext credentials. Delete or rewrite `ansible/playbooks/backup-setup.yaml` entirely.

Acceptance: a documented restore from OVH rebuilding one app's configuration and one database on a
clean path, performed at least once; a monitoring alert fires if the newest snapshot is older than
26 hours; no AWS credential anywhere.

> Concentration risk, stated once: putting domains and backups in one account means a single
> account lock costs you both at the same moment. Keeping the registrar at a different European
> provider and using OVH only for object storage removes that. The operator chose OVH on cost;
> this design records the recommendation without overriding it.

### W5 — Platform upgrades

1. **Build a throwaway test cluster.** Three K3s VMs under KVM/libvirt on `nas`, built by script,
   stood up only for an upgrade window and destroyed afterwards. It runs ArgoCD and the same charts
   from the same git branch, so the rehearsal exercises the real render path, not just `helm
   template`. If `nas` cannot spare three VMs alongside PostgreSQL, the TSDB and Loki, the fallback
   is a workstation; the requirement is three disposable machines, not a particular host.
2. **Rehearse the restore** before touching production: etcd snapshot, wipe one test node, restore,
   verify quorum. A backup nobody has restored is not a backup.
3. **The GPU node cannot be rehearsed.** The test cluster has no GPU, and `streaming` carries Plex
   and the whole media stack. Measured baseline on 2026-11-10, to be re-verified before the first
   upgrade:

   | Component | Version |
   |---|---|
   | GPU operator | `v26.3.1` |
   | NVIDIA driver (container image) | `580.126.20-ubuntu22.04` |
   | Container toolkit | `v1.19.0` |
   | Device plugin | `v0.19.0` |
   | Node Feature Discovery | `v0.18.3` |

   So `streaming` gets: a snapshot before the first step, upgrade **last among the servers**, and an
   explicit check after each step — `nvidia-smi` through the device plugin, one Plex transcode, one
   `*arr` UI load. The driver image is tied to `ubuntu22.04`, which is also the node OS: any move to a
   newer Ubuntu release has to carry the driver with it.
4. **Traefik in three moves.** Official chart pinned to 2.11 (serves both API groups, so routing
   never goes blind) — then rename everything to `traefik.io/v1alpha1` — then v3. Keep the existing
   ACME claim so certificates are not re-issued (Let's Encrypt allows five duplicates a week).
5. **K3s one minor at a time**, 1.31 → 1.36.x, servers in the order `nuc0`, `rbp2`, `streaming`,
   then the `nas` agent last. Pin each step; never let the stable channel choose.

Acceptance: all four nodes on the target version; zero `containo.us` references; the same
certificate served before and after; the test cluster proved the runbook first.

### W6 — Observability

Superseded by `2026-11-12-observability-stack-design.md`, which replaces the vendored Prometheus
and Grafana charts and the values-only `loki/` directory with one owned wrapper chart per
component.

What survives from this workstream:

1. The vendored `prometheus` and `grafana` charts are replaced by wrapper charts; the two
   Applications keep their names and paths.
2. `mktxp`, `pihole-exporter` and `prometheus-smartctl` keep working unchanged: Prometheus still
   scrapes them through their `prometheus.io/scrape` annotations, so no ServiceMonitor layer and
   no operator CRDs are introduced.

Deferred to their own features: alerting (Grafana unified alerting, Discord delivery), which is
what programme goal 5 still requires; dashboards as code; and the `mktxp` floating `latest` tag.

### W7 — New services

1. **Filebrowser — the editor's share, and it is internet-facing.** This is the one public
   service in the rebuild, so authentik comes first (Wave C) and filebrowser is the pilot for the
   SSO pattern.
   - Storage: config and database on `local-path`, pinned to `nas`; mount `nfs-plex` and
     `nfs-stash`. Configuration starts fresh — the old volume is abandoned, so budget for creating
     the admin user and setting the share roots by hand.
   - Auth: authentik forward-auth, with one provider and application in git. The current setup
     returns 404 on `/api/auth/callback`, so the flow was never completed — treat it as greenfield,
     not as a fix.
   - Scope: the editor's account sees the video and asset trees only, read-write; no access to the
     rest of the NAS. No anonymous browsing.
   - Exposure: TLS via the existing resolver, on a name that is deliberately public. Rate limiting
     at the proxy, and upload size limits sized to the editor's real files rather than the default.
   - Bandwidth: the editor pulls video through the home uplink. State the expected file sizes and
     whether the link can carry them before promising the workflow, because this is the part that
     fails silently.
2. **OpenMemory (Mem0)** against PostgreSQL with `pgvector`, MCP endpoint exposed to OpenCode,
   pinned to `nuc0`, backed up with the rest of the database.
3. **Vikunja** against PostgreSQL, API token for the LLM, MCP server, pinned to `nuc0`.
4. **The join.** One document describing how a todo flows between a project, Vikunja and an agent's
   memory, so the hub is a design rather than an aspiration.

Acceptance: the editor can retrieve a video and upload an asset; an agent can write and recall a
memory; an agent can create and close a Vikunja task.

### W8 — Ansible and documentation

1. **Inventory into the repo** — hosts, groups, and `group_vars` — with the secret values in one
   encrypted dump (§8.5 level 1). Without this the playbooks are unrunnable and the machines are
   undocumented.
2. **Rewrite `setup-node.yaml`** for the actual Ubuntu version: drop `apt_key` and the `focal`
   repo, add containerd, K3s role and token, node labels and taints, `open-iscsi`, NFS exports,
   GPU host preparation, timezone, and a firewall.
3. **Rewrite `upgrade-all.yaml`**: serial, drain, reboot, ordering, version pinning — or delete it
   and route upgrades through the System Upgrade Controller. It must not be runnable as-is.
4. **Write the documentation set** in §8.6, including the DR runbook tested in W4.

Acceptance: a fresh Ubuntu host becomes a cluster member by running one playbook; the DR runbook
has been executed at least once.

## 10. Ordering

Dependency order. Only three rules are hard:

- **W1 before anything that needs storage.** Nothing schedules without a working StorageClass.
- **W2 before the apps that need a database**, and before the two new services in W7.
- **Each wave finished before automation is enabled on it** (§8.4 stage 2).

Everything else is the operator's call. In particular **W4 is not a gate**: backups are deferred and
the operator has accepted the risk of rebuilding without them. That makes W5 the riskiest workstream
in the programme, because six minor platform upgrades would then run with no restore path — worth
revisiting if W5 comes up before W4.

```
W0 cleanup
  └─ W1 storage
       ├─ W2 postgres ──┬─ W3 waves ──┬─ W6 observability (after W5, superseded — see the 2026-11-12 design)
       │                └─ W7 new services
       └─ W5 platform ─────────────────┘
W4 offsite: not urgent, independent, take it whenever the operator wants it
```

W3 and W7 can proceed in parallel with W5 only if the upgrades are rehearsed on the test cluster
first; they touch the same ArgoCD applications otherwise.

**Operator's order (2026-11-11): W5, then W6 (superseded — see the 2026-11-12 design).** This is their call and it overrides the default
above. It has one cost that is worth writing down rather than arguing: W5 then runs with no backup
and against a noisy baseline, so each upgrade step is harder to verify than it would be after W0 and
W1. W0 — half a session — is the cheapest thing that improves those odds.

## 11. Open points

One left.

1. **Priority order across the nine workstreams.** The operator sets it after reading the
   implementation plan. Default until then: the dependency order in §10, with the filebrowser slice
   and the OVH bucket pulled forward.

Everything else is closed:

| Point | Decision |
|---|---|
| Editor access | Public internet, authenticated by authentik. Read-write on the video and asset trees only. |
| Domains | Public zones move from AWS Route 53 to OVH. Registration moves only if price or terms force it. |
| Authentik | Kept, configuration rebuilt. SSO for every app eventually; internet-facing services first. |
| Secrets | Level 1 — inventory plus one encrypted dump in the backup set. SOPS after W5. |
| `node-type` label | Retired. Every pod is pinned by `kubernetes.io/hostname`. |
| Knowledge split | As §8.8: git for designs and documents, Vikunja for work state, OpenMemory for agent recall. |
| NAS storage | Root filesystem is a dedicated SSD used only for `local-path`, so PostgreSQL, the TSDB and Loki land on flash with no extra configuration. |
| Media offsite | Out of scope for now, decided per service. |
| Disk capacity | Sufficient everywhere: ≥1 TB per node, 30 TB mechanical plus 2 TB NVMe on the NAS. |
| Documentation | Markdown in git, rendered by Material for MkDocs, Mermaid as code. |
| Charts and images | Operator's own charts where no official chart exists; upstream or LinuxServer images; Docker Hub only where no alternative exists. |

Answered during design: charts are the operator's own where no official chart exists; images come
from the upstream project or LinuxServer; Docker Hub is avoided, with the one PostgreSQL exception
recorded in §8.7.

## 12. Left to the builder

- Exact chart list and versions per wave.
- The upgrade runbook's step sequence and rollback commands, written for the operator to execute.
- Sizing for Prometheus, Loki and PostgreSQL.
- The Vikunja and OpenMemory chart shape, where no upstream chart exists or where the upstream one
  does not meet §8.3.
- The order in which the eleven affected apps are rebuilt.
- The `docs/` index and front-matter convention that makes §8.8 work in practice.

## 13. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Rebuilding without any backup (accepted) | a mistake in W3 costs that day's work and nothing older | small waves, one app at a time, configuration in git wherever the chart allows it; take W4 as soon as it stops feeling optional |
| A doomed-claim pod restarts before it is rebuilt | the data is lost (already accepted) and the app is left half-dead with no record | decide each of the eleven in W0 — rebuild now or stop deliberately — and never leave one to restart on its own |
| etcd quorum lost during a control-plane upgrade | cluster down, no easy recovery | test cluster, one server at a time, etcd snapshot verified before the first step |
| ACME duplicate-certificate limit during the Traefik cutover | certificates unavailable for a week | reuse the existing ACME claim, single cutover window |
| `local-path` node disk loss | app and config gone | backups from W4, monthly restore drill |
| Backup target locked out (single account) | domains and backups lost together | keep the registrar separate, export zones to git |
| No Application records a sync policy in git | a root-app sync can silently disable automation the operator enabled in the UI | expected by design: re-enable it in the UI, per §8.4 |
