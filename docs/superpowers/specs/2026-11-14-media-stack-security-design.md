# Media stack security — design

Date: 2026-11-14.
Status: **Phase 0 implemented in git (2026-11-14); Phases 1, 1B and 2-6 remain unbuilt.**
Phase 0 closes the door only once the branch is merged, ArgoCD has synced, and the unmanaged
`readarr` route (live, no chart, no middleware) is deleted or brought under a chart. Until those
three happen, the internet can still reach the routes this design describes.
Scope: the `media-stack` namespace and the shared paths that carry its risk (Traefik routing,
Authentik, ArgoCD, the NFS exports, cluster policy). Other namespaces are in scope only where they
share one of those paths.

Vocabulary used below:

- **Host header**: the name a client says it is asking for inside an HTTP request. A client can set
  it to anything; it is not proof of who the client is or where it came from.
- **Source allowlist**: a Traefik Middleware (`ipWhiteList`) that rejects a request with `403` when
  the connecting address is outside a list of ranges.
- **Forward auth**: a proxy gate that asks Authentik "is this person signed in?" before the app sees
  the request.
- **NetworkPolicy**: a firewall rule for pods, applied by the cluster network plugin.
- **Privileged container**: a container that gets most of the host kernel's powers. It is close to
  running the process directly on the node.
- **Pod Security Admission (PSA)**: a per-namespace minimum standard the API server enforces on
  every pod created there.
- **Egress**: outbound traffic from a pod.

## 1. Intent

Close the internet-facing door on everything that is not meant to be public, then reduce what a
successful compromise can reach, make the damage recoverable, and make an intrusion visible.

The operator's decision, taken during this design: **Plex and the Seerr request portal stay
public; nothing else does.** LAN and WireGuard access keep working unchanged.

## 2. What is true today

Measured on 2026-11-14 from this workstation, with the read-only `kubernetes` MCP server and plain
HTTP probes. The probes are reproducible; the commands are in §9.

### 2.1 The internet-facing door is open

The home router forwards TCP 80 and 443 to the cluster's Traefik load balancer. Traefik chooses a
route by the Host header alone. Therefore **every route on the `web` and `websecure` entry points is
reachable from the internet by setting the Host header**, whatever its hostname resolves to.

| Probe from the public address `80.14.57.209` | Result | What it means |
|---|---|---|
| `http://80.14.57.209/` (no matching Host) | `404` from Traefik | Port 80 is open and lands on Traefik |
| Same URL fetched by an external service (`r.jina.ai`) | `404` from Traefik | Confirmed from outside the home network, not a hairpin artefact |
| `Host: pgadmin.kub.local` | `403` | The existing source allowlist works |
| `Host: calibre.kub.local` | `403` | The existing source allowlist works |
| `Host: bazarr.kub.local` | `200`, full UI | Public, no gate |
| `Host: maintainerr.kub.local` | `200`, full UI | Public, **the app has no login at all** |
| `Host: qbittorrent.kub.local` | `200`, WebUI shell (its API answers `403`) | Public login page, brute-force target |
| `Host: sonarr.kub.local` and the other *arr apps | `302` to a login page | Public login pages |
| `Host: tautulli.kub.local` | `303` | Public |
| `Host: prometheus.kub.local/api/v1/query?query=up` | `200` | **Metrics API open, unauthenticated** |
| `Host: loki.kub.local/ready` | `200` | **Log store open, unauthenticated** |
| `Host: grafana.kub.local` | `302` | Login page, public |
| `Host: traefik.kub.local/dashboard/` | `401` | Basic auth holds |

Two non-HTTP ports on the same load balancer are also open to the internet:

| Port | Service | Consequence |
|---|---|---|
| `25565/TCP` | Minecraft server | The game server answers the internet; its protocol is a known target for amplification and its plugins are untrusted code |
| `22000/TCP` | Syncthing | The sync protocol is public; device IDs are the only gate |

`32400/TCP` (Plex) is **closed**, so Plex remote playback currently works through Plex's relay, not
through an inbound port. `18515` (qBittorrent's torrent port) is also closed, which is correct: its
traffic leaves through the VPN tunnel.

`*.kub.home.ashudev.com` is a **split-horizon** name. The authoritative Route53 zone answers
`80.14.57.209` for every name in the wildcard (verified against the zone's own nameserver, including
a name that does not exist); only the LAN resolver rewrites them to `192.168.1.46`. So those
hostnames are reachable from the internet **by name**, with no forged header at all. The `*.kub.local`
names are not in public DNS, but the `Host` header path reaches them anyway.

### 2.1.1 One correction to the public list

"Plex stays public" is the operator's intent, but the inbound path for remote playback is not this
route: `32400` is closed, so remote Plex works through Plex's relay today. The allowlist therefore
does not change remote playback either way; Plex stays on the public list so a future reverse-proxy
arrangement is not surprised. Seerr, by contrast, **is** reachable from the internet today by name
(§2.1), protected only by its own login and rate limit — which is exactly the state the public list
makes explicit.

### 2.2 What exists on the edge

63 live `IngressRoute` objects. Only four carry a source allowlist or a login gate beyond the app's
own:

| Mechanism | Where it exists |
|---|---|
| Source allowlist `192.168.1.0/24` | `pgadmin` (own Middleware), `calibre-web-automated` (own Middleware) |
| Forward auth through Authentik | `filebrowser` only (core `Ingress`, not an `IngressRoute`) |
| Security headers + rate limit | `plex`, `seerr` only |
| Basic auth | Traefik dashboard only |

The middleware mechanism is proven on this cluster: both allowlists return `403` from the public
address, and the live objects carry a non-empty `spec.ipWhiteList.sourceRange`. The `pgadmin` chart
also records a trap worth keeping: **the installed CRD silently prunes a field name it does not
know**, which once left the object empty and the allowlist unenforced. Every change to this mechanism
must be verified against live behaviour, not against the rendered YAML.

One defect sits on the public list itself: `plex` has a second router, `plex-media-public`, on the
same host with **no middleware at all**. Traefik may serve the unhardened router, which bypasses the
security headers and the rate limit. It is recorded as a Phase 2 item because it does not change what
the internet can reach, only how hard it can push.

### 2.3 What is exposed inside

- **No `NetworkPolicy` exists in `media-stack`.** Every pod can reach every other pod, every Service
  in every namespace, and the NFS server on `192.168.1.41`.
- **qBittorrent is `privileged: true`** with `NET_ADMIN` and `SYS_MODULE`, and mounts the host's
  `/dev/net/tun`. That is the single most powerful container in the namespace. Its pod uses the
  `default` ServiceAccount and does not set `automountServiceAccountToken: false`, so a Kubernetes
  API token sits inside it.
- **The media library is mounted read-write by more than ten pods**, including the three apps that
  process untrusted files: `unpackerr` (archive extraction into the library), `calibre-web-automated`
  (book ingest), `bazarr` (subtitle archives). A path-traversal bug in any of them writes anywhere
  in the share.
- **No host firewall is configured** by any playbook, and `/etc/exports` is not in the repo, so the
  NFS export options are unknown from git.
- **There is no working backup.** W4 is deferred; the media library and the application configs have
  no recovery point an attacker could not also reach.
- **Nothing looks for vulnerabilities** — that design exists and is deferred
  ([`2026-11-14-vulnerability-alerting-design.md`](2026-11-14-vulnerability-alerting-design.md)).
- **No Kubernetes audit log and no runtime detection.** A compromise leaves pod logs only.
- `filebrowser` runs `v2.63.23` in its own namespace and chart (moved there upstream on
  2026-11-15) behind its own local login. Upstream archived the project on 2026-09-01; `2.63.x` is
  the last line.

### 2.4 A contrary detail worth knowing

The `qbittorrent` download share is a **static PV/PVC pair**, created by the `nfs-qbittorrent-download`
release and pinned by `claimRef` to namespace `media-stack`. Its PV has carried a
`deletionTimestamp` of `2023-12-06` for three years while still serving reads and writes, and seven
other charts mount the same claim. Any change that moves `qbittorrent` between namespaces therefore
touches state that is not in git and a volume already stuck mid-deletion. That is why it is a phase
of its own below, not a step in the urgent block.

## 3. Goals, non-goals, anti-goals

**Goals**

1. From the internet, only the agreed public routes answer; everything else returns `403` at
   Traefik before any application code runs.
2. LAN and WireGuard users keep exactly today's access, with no port numbers or hostnames added.
3. A new route added by a future change cannot silently reopen the door.
4. A compromised application pod can reach only the services it needs, not the whole cluster and not
   the whole NFS share.
5. The privileged download path is isolated from the rest of the namespace.
6. There is a recovery point for configs and the library that an attacker with cluster access cannot
   delete.
7. High-value events (new pod, privileged pod, authentication failures, mass file deletion) produce
   an alert through the existing Discord contact point.

**Non-goals**

- Zero trust inside the LAN. The LAN stays a trust boundary.
- A service mesh, mutual TLS between pods, or a policy engine.
- Rewriting the media apps or replacing them with hardened alternatives.
- HA or failover. The cluster is a single point of failure by design.
- Host-level CIS hardening of the four nodes beyond the firewall and patch cadence.

**Anti-goals (failures even if the mechanism works)**

- A allowlist that renders empty because the CRD pruned the field, and therefore allows everyone.
  This has happened once already.
- Breaking Plex remote playback or the phone apps while closing the door.
- An allowlist that trusts `X-Forwarded-For` that a client can forge.
- Claiming the stack is "secured" because the door is closed; the door is one layer.

## 4. Constraints

- GitOps: every change reaches the cluster through `develop`; `cluster/argocd/**` is CRLF; ArgoCD
  rewrite is not permitted. Application sync automation is enabled from the UI, not git.
- Traefik is K3s 2.9.4 with `traefik.containo.us/v1alpha1` and the pre-2.5 `ipWhiteList` field name.
  W5 moves both; this design must survive that rename, so the field name lives in one place per chart.
- The Traefik Service uses `externalTrafficPolicy: Local`, so the client's real source address is
  preserved. Verified from the Service spec; it must also be verified behaviourally (§10).
- Plex clients, phone apps and the *arr mobile apps cannot complete a browser-based forward-auth
  flow. Source allowlisting is the correct gate for them; Authentik cannot be.
- `local-path` claims are hostPath-backed and node-bound. Moving a workload between namespaces
  creates a new claim; data does not follow.
- Charts are per-app by decision. There is no shared library chart, so a rule that applies to every
  chart is applied in every chart and checked mechanically.

## 5. Approaches

### A. One source allowlist on every non-public route — **chosen**

One `Middleware` in `kube-system` holding the LAN and WireGuard ranges, referenced by every
`IngressRoute` that is not on the public list. A repository check fails when a route lacks it and is
not on the list.

- **Cost:** 60-odd routes across 20 charts, one small chart and Application for the gate itself, one
  check script.
- **Benefit:** hostnames, ports and clients unchanged; everything is in git; the mechanism is already
  proven on this cluster.
- **Weakness:** the guarantee comes from the check, not the network. A route added by hand with
  `kubectl apply` breaks it. Accepted: hand-applied manifests are already forbidden by git policy.

### B. Unpublished entry points

Move internal routes to `lan-web`/`lan-tls` on ports the router does not forward. The internet cannot
reach them even with a forged Host.

- **Cost:** every internal URL gains `:8080`; Traefik entry points live in
  `cluster/infrastructure`, which is applied by hand outside GitOps; bookmarks, phone apps and
  Authentik callbacks all change.
- **Benefit:** a physical guarantee.
- **Verdict:** stronger, but the day-to-day cost is permanent and the hand-applied dependency is the
  opposite of the rest of the repo. Recorded as the upgrade path if the operator ever wants a
  guarantee that does not depend on discipline.

### C. Edge proxy in front

The edge Pi already runs Traefik v3. Make it the only internet-facing proxy, teach it only the
public names, and close 80/443 to the cluster.

- **Cost:** a new component to operate, ACME certificates to move, a Pi as single point of failure,
  router configuration outside git.
- **Benefit:** the strongest isolation.
- **Verdict:** the right long-term shape and the natural home for WAF-style filtering, but too much
  new machinery for the first fix.

## 6. Design

### 6.1 The gate: one middleware, one list

A small chart of its own, owning exactly one object: a `Middleware` named `lan-only` in
`kube-system`, with an ArgoCD `Application` pointing at it. This is a new chart, not a new pattern —
it follows the same Application+chart shape as every other app, and it does not adopt the existing
hand-applied middlewares in `cluster/infrastructure/traefik/middleware`. The point is that a commit
to the ranges reaches the cluster on a sync, and that one place can be verified.

```yaml
spec:
  ipWhiteList:
    sourceRange:
      - 192.168.1.0/24        # LAN
      - <wireguard-range>     # wg-easy default 10.8.0.0/24; confirm before applying
```

- It is added to the `middlewares` list of every `IngressRoute` and every core `Ingress` annotation,
  first in the chain, on `web` and `websecure` alike.
- The WireGuard range is a single value documented in one place. It must be confirmed from the
  wg-easy instance; guessing it wrong either locks the operator out of remote access or opens a hole.
- `calibre-web-automated` and `pgadmin` keep working; their per-chart allowlists are replaced by the
  shared one so there is one range to change.
- The public set is an explicit list in the check script: `plex`, `seerr`, `ashuvidz.com`,
  `forum.ashuvidz.com`, `wiki.ashuvidz.com`. `dashy.ashudev.com` is **not** public: it is a start
  page that lists internal URLs, and it currently has no authentication.

### 6.2 The check that keeps it closed

There is no CI. So the check is a script in `hack/` that the operator and every agent runs before a
PR, and its output is pasted into the PR body:

1. Enumerate every `IngressRoute` template, every core `Ingress` host in `cluster/helm`, **and** the
   dashboard route in `cluster/infrastructure/traefik/traefik-config.yaml`, which is rendered from a
   hand-applied `HelmChartConfig` rather than a chart.
2. Assert each one either references `lan-only` or is on the public list.
3. Assert `lan-only` has a non-empty `sourceRange` in the rendered output (the CRD-pruning trap).
4. Assert no route uses `X-Forwarded-For` to make the decision.

The check is also wired into the chart checklist in [`docs/charts/README.md`](../../charts/README.md)
and mentioned in `AGENTS.md`, so it is part of the definition of "done" for a new chart.

### 6.3 Verification from outside, not from the LAN

Every step that touches the gate is verified by a probe from the public address, because that is the
only vantage point that matters:

```bash
curl -s -o /dev/null -w '%{http_code}\n' -H 'Host: sonarr.kub.local' http://80.14.57.209/   # expect 403
curl -s -o /dev/null -w '%{http_code}\n' -H 'Host: pgadmin.kub.local' http://80.14.57.209/   # expect 403
curl -s -o /dev/null -w '%{http_code}\n' -H 'Host: prometheus.kub.local' http://80.14.57.209/ # expect 403
```

And by a probe from the LAN, to prove the change did not break legitimate use. A `403` from both is a
failed change, not a secure one.

### 6.4 Phases

Each phase is independently useful and independently verifiable. The written plan will turn these
into tasks.

**Phase 0 — close the door (same day, one PR)**

1. Create the gate chart and its Application (one `Middleware`, `kube-system/lan-only`), with the
   confirmed WireGuard range. Sync it and verify the live object has a non-empty `sourceRange`
   before any route references it — routes that reference a missing middleware answer `404`.
2. Add it to every non-public route: `media-stack` first (18 routes), then `monitoring` (Prometheus,
   Loki, Grafana), `authentik`, and the remaining `*.kub.home.ashudev.com` and `*.kub.local` routes.
3. Replace the two per-chart allowlists with the shared one.
4. Add `hack/check-exposure.sh` and run it.
5. Verify: 15 public probes `403`, LAN probes `200`, Plex and Seerr unchanged.
6. Operator, at the router: close `25565` and `22000`. Both are confirmed open from the internet
   today and neither needs to be. Verify with a TCP connect from outside; both must fail. If
   Minecraft must stay reachable, it gets the same treatment as a public route and is upgraded
   before it is republished.

**Phase 1 — stop trusted-by-default networking (1-2 days)**

1. Default-deny ingress and egress `NetworkPolicy` in `media-stack`, then explicit allows:
   ingress from Traefik only; egress to DNS; egress to NFS `192.168.1.41:2049`; egress to the
   internet for the apps that need it; `flaresolverr` accepts requests from `prowlarr` only.
2. Turn off service-account token mounting in the charts that still mount one, starting with
   `qbittorrent`.
3. Reduce the VPN sidecar from `privileged: true` to `NET_ADMIN` (+ `SYS_MODULE` only if the
   WireGuard module is not loaded in the kernel), and verify the tunnel still comes up and traffic
   still fails closed.
4. Confirm Plex, the *arr apps and the download path all still work.

**Phase 1B — isolate the privileged download path (separate change, after Phase 1)**

This is deliberately not part of Phase 1. The builder check found that the move touches three claims
and several objects outside git, so bundling it would have broken the urgent block's estimate and,
worse, split the download/import pipeline.

1. Duplicate the two static PV/PVC pairs into a new `media-download` namespace, pointing at the same
   NFS paths (`/mnt/storage/nfs/qbittorrent-config`, `/mnt/ultrafaststorage/nfs`) with new PV names
   and their own `claimRef`. Do not touch, rename or delete the existing pair — the download PV has
   been `Terminating` since 2023 and still serves seven other charts.
2. Recreate the local-path VPN config claim in the new namespace, or copy it before the first start;
   a namespace move creates a new claim and the tunnel must not come up without its keys.
3. Update `qbittorrent-exporter`'s base URL to the new Service name.
4. Cut over the download-client host in each consumer. Those addresses live in the apps' own
   databases and configs, not in git: Sonarr, Sonarr-anime, Radarr, Radarr-anime, Lidarr, Autobrr,
   Cross-seed, Unpackerr. This is a manual checklist with a verification per app.
5. Only now set `media-stack` to `pod-security.kubernetes.io/enforce: baseline` and
   `media-download` to `privileged`.

Acceptance: torrents restore, an import still moves a file from downloads to the library, cross-seed
still matches, and the exporter reports. Rollback is redeploying the old namespace's objects, which
are untouched until step 5.

**Phase 2 — identity (2-3 days)**

1. Maintainerr: it has no login, so it is either LAN-only (Phase 0 already does this) or gets forward
   auth. Decide by testing whether its UI tolerates the gate; it has no mobile app, so it should.
2. Enforce stronger authentication on the admin UIs: unique passwords for qBittorrent, Sonarr,
   Radarr, Lidarr, Prowlarr, Bazarr, Tautulli, Autobrr, record them in the password manager.
3. qBittorrent: confirm "bypass authentication for localhost or whitelisted IPs" is off, UPnP/NAT-PMP
   off, and the WebUI cannot be reached without the password from a pod IP.
4. Add Authentik MFA to the operator account, and put Authentik itself behind the allowlist.
5. Rate-limit login endpoints at Traefik for every app that has a login form (the *arrs, qBittorrent,
   Tautulli, Bazarr, Autobrr, Grafana), reusing the `plex`/`seerr` rate-limit pattern.

**Phase 3 — data integrity and recovery (2-3 days, depends on W4)**

1. Read `/etc/exports` on `nas` and record it in `docs/`; require `root_squash`, no `no_root_squash`,
   and export access limited to the four cluster nodes.
2. Split the library exports as an **overlay**, not a reorganisation: add subtree exports for the
   paths each app writes (unpackerr, bazarr, calibre ingest) and mount only those. No library data
   moves, so the change is remount-and-verify per pod with a pod-level rollback.
3. Backups are W4's work, referenced rather than re-planned here. This design adds one requirement to
   W4: the offsite copy must be undeletable by someone holding the cluster's credentials (object
   versioning plus a retention lock, or a second credential the cluster never sees).
4. Test one restore end to end and record it as a runbook.
5. Alert when a backup has not completed, and when a single pod deletes an unusual number of files.

**Phase 4 — supply chain (3-5 days)**

1. Implement the deferred vulnerability-alerting design, scoped to `media-stack` first.
2. Pin images by digest in `values.yaml`, and remove the `alpine:latest` init that downloads and
   unzips VueTorrent from GitHub on every pod start; bake it into a pinned image or a claim.
3. Ensure Renovate covers every media-stack chart; close the `latest` tags that remain.
4. Plan the FileBrowser exit: archived upstream, running `v2.63.23`. Its own login (not forward
   auth) is the gate; the LAN allowlist is the network boundary.
5. Traefik and K3s upgrades are W5, referenced not re-planned. The CRD rename is a prerequisite for
   `ipAllowList` in v3, so the gate's field name changes in that step, in one place per chart.

**Phase 5 — detection (2-3 days)**

1. Kubernetes audit log to file on each server, shipped to Loki, with a retention that survives a
   node reboot. Alert on `pods/exec` into `media-stack`, on privileged pod creation, and on
   ServiceAccount token use from an unexpected pod.
2. Runtime detection (Falco or Tetragon) with a small rule set: shell in a container, write to
   `/dev/net/tun` outside `media-download`, unexpected outbound connection, mount of the host
   filesystem.
3. Alert on Authentik failures, on a Traefik 4xx spike per host, and on a new public route appearing.
4. Enable secrets encryption at rest for the K3s API server.

**Phase 6 — hosts and edge (1-2 days)**

1. Host firewall on all four nodes: only the ports the cluster needs plus SSH from the LAN.
2. Confirm the non-HTTP ports stay closed after Phase 0's router change, and add a check that they
   have not been reopened.
3. Turn off `--api.insecure=true` on the edge Pi's Traefik and confirm Pi-hole does not answer DNS
   queries from the internet.
4. Record the whole edge path (router → Traefik → route) in `docs/`; it is currently tribal knowledge.

### 6.5 Priority and ordering

Phase 0 is the only urgent one: it is the difference between "internet-reachable admin UIs" and
"LAN-only". Phases 1 and 1B reduce what a compromise reaches; 1B is separate because its blast radius
is the download pipeline, not the edge. Phase 2 closes the remaining authentication gaps. Phase 3 is
the difference between an incident and a disaster; its backup half is W4's work and should not block
Phases 0-2. Phases 4-6 are sustained hygiene and can run alongside.

Sequence against the programme: the operator's order is **W5 first, W6 second**, and **Phase 0 is
decided to run ahead of W5**: it is hours rather than days and it closes a live internet exposure.
The cost is that the gate's field name (`ipWhiteList` → `ipAllowList`) changes in W5 and must be
changed in one place per chart, which the check script enforces. Phase 4 step 5 and Phase 3 steps 3-5
are references to W5 and W4, not tasks re-planned here.

## 7. Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| The allowlist renders empty (CRD pruning) and allows everyone | Low, happened once | The check asserts a non-empty rendered range; the outside probe is the acceptance test |
| The WireGuard range is wrong, locking the operator out remotely | Medium | Confirm it before applying; keep a LAN path; change it in one place |
| Source address is not the client's (proxy or masquerade) | Low | `externalTrafficPolicy: Local` verified; behavioural probe from LAN and WAN before declaring done |
| Plex remote playback breaks | Low | Plex remote does not use this route today; keep Plex on the public list and test after |
| NetworkPolicy breaks DNS or NFS and pods become unreachable | Medium | Apply egress allows first, then deny; roll back by deleting the policy |
| Moving qBittorrent loses its data | High if done carelessly | Phase 1B duplicates the claims and leaves the originals untouched until every app is verified |
| Backups restore slower than an attacker deletes | Medium | Retention lock, offsite copy, and a tested restore |
| The plan is read as "the stack is now secure" | High | Anti-goal §3; the phases each state what they do not cover |

## 8. What is left to the builder

- The exact WireGuard subnet, read from the wg-easy instance.
- Whether `dashy.ashudev.com` is public; this design assumes not.
- Whether `25565` and `22000` should be reachable at all, or closed at the router (recommended).
- The NFS export options on `nas`.
- The choice of runtime detection tool (Falco or Tetragon) and whether the nodes can carry it.
- Whether Maintainerr tolerates forward auth.

## 9. Evidence

### 9.1 Probes (2026-11-14)

```bash
# public address, Host header set per app
curl -s -o /dev/null -w '%{http_code}\n' -H 'Host: bazarr.kub.local' http://80.14.57.209/       # 200
curl -s -o /dev/null -w '%{http_code}\n' -H 'Host: maintainerr.kub.local' http://80.14.57.209/  # 200
curl -s -o /dev/null -w '%{http_code}\n' -H 'Host: qbittorrent.kub.local' http://80.14.57.209/ # 200
curl -s -o /dev/null -w '%{http_code}\n' -H 'Host: prometheus.kub.local' http://80.14.57.209/api/v1/query?query=up  # 200
curl -s -o /dev/null -w '%{http_code}\n' -H 'Host: loki.kub.local' http://80.14.57.209/ready    # 200
curl -s -o /dev/null -w '%{http_code}\n' -H 'Host: pgadmin.kub.local' http://80.14.57.209/      # 403
curl -s -o /dev/null -w '%{http_code}\n' -H 'Host: calibre.kub.local' http://80.14.57.209/      # 403
```

External confirmation that port 80 is reachable from outside the home network:
`https://r.jina.ai/http://80.14.57.209/` returned the same `404 page not found` from Traefik.

### 9.2 Objects read

- 63 `IngressRoute` objects, all `traefik.containo.us/v1alpha1`; 18 in `media-stack`.
- `Middleware/databases/pgadmin-lan-only` and `Middleware/media-stack/calibre-web-automated-allowlist`
  both hold `ipWhiteList.sourceRange: [192.168.1.0/24]`.
- `Service/kube-system/traefik`: `type: LoadBalancer`, `externalTrafficPolicy: Local`, ports
  `web 80`, `websecure 443`, `mc 25565`, `sttcp/studp 22000`, `stdiscovery 21027`.
- `Namespace/media-stack` has no `pod-security.kubernetes.io/*` labels.
- `kubectl get networkpolicy -A` equivalent: 11 policies, none in `media-stack`; they exist in
  `argocd`, `ashuvidz`, `authentik`, `discourse` and `postiz`, so the network plugin enforces them.
- `Deployment/media-stack/qbittorrent`: VPN sidecar `privileged: true`, capabilities `NET_ADMIN`
  and `SYS_MODULE`, hostPath `/dev/net/tun`, `dnsPolicy: None`.

### 9.3 Files that carry the decision

- `cluster/infrastructure/traefik/middleware/security.yaml` — the house header middleware.
- `cluster/helm/databases/pgadmin/templates/middleware.yaml` — the existing allowlist pattern and the
  CRD-pruning note.
- `cluster/helm/media-stack/calibre-web-automated/templates/middleware-allowlist.yaml` — the second.
- `docs/charts/README.md` — the chart contract the check must join.
