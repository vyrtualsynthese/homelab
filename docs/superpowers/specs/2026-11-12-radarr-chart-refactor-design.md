# radarr chart refactor — design

**Date:** 2026-11-12
**Status:** approved 2026-11-12
**Charts:** `cluster/helm/media-stack/radarr`, `cluster/helm/media-stack/radarr-anime`

## Intent

Bring the two Radarr charts to the hand-maintained chart contract in
[`docs/charts/README.md`](../../../docs/charts/README.md), using `prowlarr` as the worked example for a
LinuxServer *arr app. Both instances should be named, probed, sized, logged and hardened the same way,
and the metrics sidecars must actually be scraped.

Why now: both charts predate the contract. They still carry Plex leftovers (`service.port: 32400`,
`chart-example.local`), no ServiceAccount, no probes, no log level, and hardcoded route targets.
Separately, both `exportarr` sidecars answer **401** today because Secret `radarr` and Secret
`radarr-anime` hold API keys that no longer match the running apps. The chart cannot fix that; the
Secret values must be replaced.

## Goals

- Both charts render the same object set and the same values schema as `prowlarr`, plus the two named
  media mounts.
- Every object name is derived from the release, with no change to the live claim names.
- `radarr-anime` stops running its application as root: `PUID`/`PGID` move from `0` to `1100`.
- Liveness, readiness and startup probe Radarr's own unauthenticated `/ping`.
- Radarr logs at `warn` on both the file and the console sink.
- `exportarr` is scraped successfully for both instances, which requires an operator Secret repair.
- Each chart gets a README; the two rows are added to `docs/charts/README.md`.

## Non-goals

- `sonarr` and `sonarr-anime`. They have the same pre-contract shape and deserve their own pass; this
  change makes the two Radarr charts diverge from them deliberately.
- The Traefik v3 API-group migration. `traefik.containo.us/v1alpha1` stays, as a value.
- Changing media paths, claims or the qBittorrent layout.
- Editing the ArgoCD Application files. Release name equals Application name, so they need no change.
- A shared chart between the two apps. They are separate charts with the same principles.

## Anti-goals

- A render whose claim name does not match the live claim. That orphans data silently.
- Flipping `PUID` to 1100 without chowning the existing config directory first. The pod would start and
  then fail on its first write.
- Any API key in a manifest, a value, or a command line.
- Copying `prowlarr`'s exportarr defect note verbatim. Its cause there is a stale Secret too, but the
  note describes Prowlarr; here the repair is explicit and verified.

## Constraints

- **LinuxServer image.** It starts as root, prepares `/config`, then drops to `PUID`/`PGID`. Setting
  `runAsNonRoot` or `runAsUser` prevents it from starting, as documented in the `prowlarr` README.
  "Non-root" therefore means `PUID/PGID 1100` here, plus `allowPrivilegeEscalation: false` and the
  `RuntimeDefault` seccomp profile.
- **Capabilities are not dropped.** The LinuxServer init must chown `/config` and switch user, so
  `capabilities.drop: [ALL]` is not an option; only `allowPrivilegeEscalation: false` and the runtime
  seccomp profile apply.
- **`local-path` is hostPath-backed.** The kubelet does not apply `fsGroup`, so `fsGroup` is absent
  rather than set to something plausible.
- **The live claims are `radarr-config` and `radarr-anime-config`.** Both equal `<fullname>-config` for
  their release names, so derivation preserves them.
- **The live Applications auto-sync, with `prune` and `selfHeal` on.** No Application file records it;
  it was enabled from the ArgoCD UI (AGENTS §7.1). Merging to `develop` therefore rolls both apps
  without an operator step, so the pre-flight must happen before the merge and auto-sync must be
  paused from the UI for the duration.
- **Secrets are not in git.** The read-only Kubernetes MCP cannot read or write Secrets, and `kubectl`
  is blocked by the harness policy. Every cluster-side step is an operator runbook.
- **Placement.** Both pods stay on `streaming` through `nodeSelector`.
- **CRLF** applies only to `cluster/argocd/**`; chart files stay LF.

## Approaches

**A. Two independent charts rebuilt to the prowlarr shape — chosen.** Each chart owns its values and
templates, shares only the contract and the unprefixed helper names, and is reviewed as a unit.

**B. One chart with `radarr-anime` as a values overlay.** Half the templates, but it merges two apps
into one chart, which AGENTS §8 and the contract reject, and it leaks the release name into storage
naming for both.

**C. Minimal in-place fixes.** Fixes the ports and route names, but leaves `radarr-anime` root-owned,
the probes absent and the charts undocumented, so the same work returns as a second pass.

## Decisions

### Values schema (identical in both charts)

`nameOverride`, `fullnameOverride`, `replicaCount`, `strategy`, `image`, `imagePullSecrets`,
`serviceAccount`, `podAnnotations`, `podLabels`, `podSecurityContext`, `securityContext`, `service`,
`container.port`, `ingressRoute`, `storage`, `env`, `extraEnv`, `envFrom`, `downloads`, `media`,
`volumes`, `volumeMounts`, `exportarr`, `nodeSelector`, `tolerations`, `affinity`, `resources`,
`probes`.

The two app-specific mounts are named values, as `cross-seed` does for its torrent directory:

| Block | Claim | Mount | Mode |
|---|---|---|---|
| `downloads` | `pvc-nfs-qbittorrent-download-nfs-subdir-external-provisioner` | `/downloads` | read-write |
| `media` | `pvc-plex-media` | `/movies` | read-write |

Both are read-write: Radarr imports into the library and hardlinks or moves from downloads.

### Image

- `lscr.io/linuxserver/radarr:6.4.4.10685-ls326`, the current stable tag. Prowlarr already pulls from
  `lscr.io`, and Renovate has an explicit rule for `lscr.io/linuxserver/**`.
- `appVersion: "6.4.4.10685-ls326"` and chart `version: 1.0.0`.
- `exportarr` stays at `v2.3.0`; that is the current upstream release, so there is nothing to bump.

### Probes

All three probe `/ping`, Radarr's own unauthenticated health endpoint. Startup gates the other two
through a first start after the database migration a version bump triggers.

| Probe | Enabled | Timing |
|---|---|---|
| startup | yes | period 5s, timeout 5s, 60 failures (300s) |
| liveness | yes | initial 20s, period 30s, timeout 5s, 3 failures |
| readiness | yes | initial 10s, period 15s, timeout 5s, 3 failures |

The startup budget is 300s rather than prowlarr's 150s because this change also moves Radarr from
`6.3.0` to `6.4.4` and the first start runs a database migration. The probe only delays liveness and
readiness, so a larger number costs nothing when the migration is quick.

Liveness and readiness share the endpoint deliberately: Radarr's only dependency is its SQLite
database on the config volume, and a database that cannot be opened stops the process from starting,
so there is no dependency whose failure readiness could report separately. The README records this.

### Resources

Measured from Prometheus over 30 days, before this change:

| Instance | Peak working set | Peak CPU (5m rate) |
|---|---|---|
| `radarr` | 257 MiB | 20m |
| `radarr-anime` | 224 MiB | 12m |

Both charts get `requests: {cpu: 10m, memory: 256Mi}` and `limits: {memory: 1Gi}`. No CPU limit, in
line with the rest of the cluster: a limit throttles the short burst a library scan or import needs.

### Logging

`RADARR__LOG__LEVEL: warn` and `RADARR__LOG__CONSOLELEVEL: warn` in the inline `env` map. The console
level is the one Fluent Bit ships to Loki. Both move together so a debugging session cannot leave one
behind.

### Metrics and the exportarr repair

The chart keeps the `exportarr` sidecar, exposed as an `exportarr` values block. While
`exportarr.enabled` is true, the Deployment renders the four `prometheus.io/*` annotations from that
block, so the announced port cannot drift from the listener. The sidecar's `args` list selects the
application handler and must be `radarr`; leaving the reference chart's `prowlarr` value would query
the wrong API.

The 401 is not a chart defect. Both keys in `homelab.env` answer **200** against
`/api/v3/system/status`, so the running Secrets are stale. The operator patches both Secrets from
`~/.config/secrets/homelab.env` before the merge (never on a command line), and the refactor's own
rollout gives the new pods the new values. The verification is an exporter log without 401 and a
Prometheus target that is up.

### radarr-anime UID

`radarr` already runs as 1100 and needs nothing. `radarr-anime` runs as `0/0`; its config directory was
written by root. Before the new pod starts, the operator scales it to zero, resolves
`radarr-anime-config` to its volume and host path on `streaming`, and runs `chown -R 1100:1100`. Then
`PUID`/`PGID` become `1100` and the operator confirms the app can still write its database.

### Naming

| Object | `radarr` release | `radarr-anime` release |
|---|---|---|
| Deployment / Service / ServiceAccount | `radarr` | `radarr-anime` |
| Config claim | `radarr-config` (unchanged) | `radarr-anime-config` (unchanged) |
| IngressRoute | `radarr` | `radarr-anime` |
| Route target service | `radarr` | `radarr-anime` |

### Ingress

`IngressRoute` with API group as a value, host `radarr.kub.local` / `radarr-anime.kub.local`, entry
point `web`, no TLS, as today. The route target comes from `app.fullname` instead of a literal.

### Files

Per chart: `Chart.yaml`, `values.yaml`, `README.md`, `templates/{_helpers.tpl,deployment.yaml,
service.yaml,serviceaccount.yaml,persistentvolumeclaim.yaml,ingressroute.yaml,NOTES.txt}`. The old
`templates/pvc-config.yaml` and `templates/ingress.yaml` are removed.

Documentation: the two rows in `docs/charts/README.md`, plus a "What good looks like" entry covering
the pair.

## Verification

- `helm lint` and `helm template <release> . --namespace media-stack` for each chart.
- Object-name guard, because `prune: true` makes a renamed or disabled object a live deletion: the
  `radarr` render must contain exactly five objects, named `radarr` (Deployment, Service,
  ServiceAccount, IngressRoute) and `radarr-config` (PVC); `radarr-anime` renders the same set under
  its own prefix. No extra object may appear, and none of the five may be missing.
- Claim derivation: `--set fullnameOverride=demo` renders `demo-config` and `demo`.
- Greps: no `32400`, no `chart-example.local`, no `radarr-webui` container port name, no
  `networking.k8s.io`, no tabs, no `PUID: "0"`.
- Whole-repo render loop and ArgoCD path check from AGENTS §6, with only the two known pre-existing
  broken references.

## Operator runbook

The order matters: the pre-flight must finish **before** the PR merges, because auto-sync rolls both
apps the moment `develop` moves. `prune: true` also means the object-name guard above is the last
protection against a deletion.

1. **Pause automation.** In the ArgoCD UI, set both `radarr` and `radarr-anime` to manual sync for the
   duration. Do not edit the Application files: the UI state is where automation lives, and a root-app
   sync would rewrite them from git anyway.

2. **Patch the Secrets** (keys never appear in `argv`):

   ```bash
   source ~/.config/secrets/homelab.env
   printf '%s' "$RADARR_API_KEY" | base64 -w0 \
     | jq -R '{data:{secret:.}}' \
     | kubectl -n media-stack patch secret radarr --type merge --patch-file /dev/stdin
   printf '%s' "$RADARR_ANIME_API_KEY" | base64 -w0 \
     | jq -R '{data:{secret:.}}' \
     | kubectl -n media-stack patch secret radarr-anime --type merge --patch-file /dev/stdin
   ```

   The running pods keep their old environment until they roll; the new ReplicaSet picks the new
   values up.

3. **Chown `radarr-anime-config`.** Scale `radarr-anime` to zero, resolve `radarr-anime-config` to its
   PV and the PV to its host path, `chown -R 1100:1100` it on `streaming`. **Then leave the Deployment
   at zero until the merge.** Scaling it back up would start the old Deployment, which still renders
   `PUID`/`PGID 0`, and it would write root-owned files back into the directory that was just fixed.
   Automation is paused, so nothing rescales it; the merge sync in step 5 starts the pod as 1100 for
   the first time. This is the `autobrr`/`cross-seed` procedure.

4. **Merge the PR.** ArgoCD is paused, so nothing rolls yet.

5. **Sync `radarr`, verify, then sync `radarr-anime`, verify.** Manual sync from the UI.

6. **Verify**, pasting back:

   ```bash
   kubectl -n media-stack get pods -l app.kubernetes.io/instance=radarr -o wide
   kubectl -n media-stack get pods -l app.kubernetes.io/instance=radarr-anime -o wide
   kubectl -n media-stack exec deploy/radarr-anime -- stat -c %u /config
   kubectl -n media-stack exec deploy/radarr-anime -- sh -c 'test -w /config && test -w /downloads && test -w /movies && echo writable'
   kubectl -n media-stack logs deploy/radarr -c radarr-exporter --tail=20
   kubectl -n media-stack logs deploy/radarr-anime -c radarr-anime-exporter --tail=20
   curl -s http://radarr.kub.local/ping
   curl -s http://radarr-anime.kub.local/ping
   ```

   Expected: both pods `2/2 Running`, `1100` as the `/config` owner, `writable`, no 401 in either
   exporter log, and `{"status":"OK"}` from both `/ping` endpoints. `stat -c %u /config` is the
   ownership check, not `id`: the LinuxServer entrypoint runs as root and `id` reports 0. The
   `/config` write test is the one that catches a missed chown; the `/movies` and `/downloads` tests
   catch root-owned media left by the old `PUID=0` process, because a write to a directory only needs
   write permission on that directory.

7. **Re-enable automation** on both Applications in the UI, then confirm both report
   `Synced/Healthy`.

## What is left to the builder

Exact template text, values comments and README prose, following `prowlarr` line by line and diverging
only where the two media mounts and the UID runbook require it.
