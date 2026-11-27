# Internal zone: `*.kub.local` to `*.kub.internal` — design

**Status:** proposed · **Date:** 2026-11-27 · **Board:** homelab (card 894, Android and `.local`)
**Live basis:** chart `42.4.1` / Traefik `3.7.19`, the internal CA leaf `*.kub.local` in
`kube-system/local-cert`, the two edge Pi-holes on `rbp0`/`rbp1`, wg-easy handing peers
`192.168.1.22` then `192.168.1.38`.

## Intent

Make every Traefik-served internal name resolve on an Android phone joined to the house Wi-Fi,
with the WireGuard tunnel off, and keep every URL already saved somewhere working while it happens.

The trigger is measured and documented, not a guess. Android 12 and later resolve every name ending
in `.local` through mDNS: the resolver sends the query to `224.0.0.251:5353` and never to the DNS
server configured for the Wi-Fi network, and it explicitly excludes VPN and mobile-data networks
from that treatment. Nothing on the LAN answers mDNS for `kub.local`; the Pi-hole answers it as a
unicast wildcard only. Over WireGuard the exclusion applies, so the same name goes to the tunnel's
DNS — the two Pi-holes — and works. The suffix is the defect, not the LAN, the Pi-hole or the
router.

Sources: [Android DNS Resolver, mDNS `.local` resolution](https://source.android.com/docs/core/ota/modular-system/dns-resolver)
("VPN and mobile data connections are excluded from .local resolution"), the Samsung Knox
article [Devices can't reach sites with .local domain](https://docs.samsungknox.com/admin/knox-platform-for-enterprise/kbas/kba-665-devices-cant-reach-site-with-local-domain),
and RFC 6762 Appendix G, which reserves `.local` for mDNS.

## Goals

1. Every Traefik-served internal name answers as `<app>.kub.internal` over TLS from the ashulab
   internal CA, from the LAN with no tunnel.
2. An Android phone on the private Wi-Fi opens those names with no WireGuard and no per-app setup,
   once its resolver is the Pi-hole.
3. Every saved `*.kub.local` URL still reaches its application during the transition, through one
   redirect, so no bookmark, alert link or docs card is dead on the day of the change.
4. No login, API key, health probe or dashboard link breaks at cutover.
5. Up to the cleanup commit the change is reversible with `git revert`.

## Non-goals

- The edge Pis' own names: `pihole-rbp0.kub.local`, `pihole-rbp1.kub.local`, `vpn-rbp0.kub.local`,
  `vpn-rbp1.kub.local`, `rbp0.local`, `rbp1.local`, `livebox.local`. They are served by the Pis and
  the router, not by Traefik, and they keep their names and per-host certificates.
- The public names: `*.kub.home.ashudev.com`, `ashudev.com`, `ashuvidz.com`.
- Pi-hole's blocklist, upstream resolvers or panel settings.
- The Livebox's DHCP service itself. Its DNS option is measured here (card 894) and changed only if
  the operator chooses to.
- Historical records: `docs/audit/**`, dated `docs/decisions/**`, and
  `docs/superpowers/{specs,plans}/**` describe what was true then and are not rewritten.
- Grafana dashboards stored in Grafana's database, beyond the links that would break. The one
  snapshot in git (`cluster/helm/monitoring/grafana/export/traefik/dashboard.json`) is updated.

## Anti-goals

- **A hard cut.** It is smaller and leaves a dead name behind every bookmark, alert link, Vikunja
  card and phone app. It also gives no rollback path once the old route is gone.
- **Dual-hosting every route.** Matching both the old and the new host on each router would keep
  POSTs byte-identical, but an authentik proxy provider has one `external_host`, so every gated app
  would need a second provider, a second application and a second middleware chain. The duplication
  is larger than the redirect and doubles the thing that can drift.
- **An mDNS responder for `*.kub.local`.** It would answer the phone without a rename, but it adds a
  second source of truth for the same names, does nothing for a laptop or for an API client, and
  preserves a suffix the RFC reserves for mDNS.
- **Putting internal apps under the public suffix.** `*.kub.home.ashudev.com` resolves publicly;
  every internal app would answer the internet, and `lan-only` would become the only fence. The
  exposure model in `hack/check_exposure.py` exists to prevent exactly that.

## Approaches considered

**A — Rename the zone, keep the old names through one wildcard redirect, then remove the old zone
(chosen).** The new name is primary everywhere; one router at low priority catches `*.kub.local`
and redirects to the same subdomain under `.kub.internal`, keeping the path and upgrading to
`https` in the same hop for every name that has a TLS route. The two plain-HTTP names keep their
scheme, because they have no TLS route to land on. The certificate carries both wildcards while the
redirect exists. Old GETs and bookmarks keep working; API clients that cannot follow a redirect are
updated explicitly (there are few).

**B — Hard rename.** One commit changes every host, DNS record and certificate, and the old names
stop existing. Rejected: a saved URL, an alert link or a phone app on the old name breaks with no
signal, and there is no way to tell a missing client from a typo.

**C — Dual-host every route.** Every app matches `Host(new) || Host(old)` for the soak. Rejected for
the authentik reason above: the proxy provider's `external_host` is one value, so each gated app
would need a second provider and application, and the two would drift.

**D — mDNS responder for the wildcard.** Rejected: second source of truth, no help for non-Android
clients or APIs, and it keeps a suffix that should not carry unicast names.

**Suffix.** `kub.internal` (ICANN reserved `.internal` for private use in 2024; not delegated, short,
no resolver or phone special-cases it) over `kub.home.arpa` (RFC 8375, but residential-CPE framing
and longer) and over the public `*.kub.home.ashudev.com` (would make every app publicly resolvable).

## Constraints

- No CI. The gate is the `hack/` suite, `helm lint`/`template`, and the checks in
  `docs/charts/README.md`.
- GitOps: `cluster/helm/**` and `cluster/argocd/**` go through `develop`. `cluster/infrastructure/**`
  (CoreDNS, the Traefik dashboard redirect) and Secrets are applied by hand.
- The agent's shell is read-only. Replacing `kube-system/local-cert`, applying the hand-maintained
  manifests, running `ansible/pi/hosts-pi.sh setup` on the Pis, and updating app settings stored in
  application databases are operator steps, named in the plan.
- No shared/library chart. Each app owns its `ingressroute.yaml`; the shared redirect belongs in
  `traefik-gate`, which already owns the shared middlewares and TLS option.
- The internal CA root is unchanged, so a client that already trusts it needs no new install. The
  leaf, not the root, is reissued.
- Secrets never enter git. The new leaf goes to the secrets store and to the Bitwarden items named in
  `docs/secrets.md`.

## Design

### Names in scope

Twenty-three Traefik-served names, from `docs/reference/routes.md`:

| Current | New | Note |
|---|---|---|
| `argocd.kub.local` | `argocd.kub.internal` | ArgoCD `global.domain`, CLI, dex callback |
| `autobrr.kub.local` | `autobrr.kub.internal` | OIDC redirect in the chart |
| `bazarr.kub.local` | `bazarr.kub.internal` | forward-auth |
| `bazarr-anime.kub.local` | `bazarr-anime.kub.internal` | forward-auth |
| `calibre.kub.local` | `calibre.kub.internal` | native OIDC |
| `crowdsec.kub.local` | `crowdsec.kub.internal` | native OIDC |
| `dashy.kub.local` | `dashy.kub.internal` | redirect to `dashy.ashudev.com` |
| `docs.kub.local` | `docs.kub.internal` | forward-auth |
| `grafana.kub.local` | `grafana.kub.internal` | `root_url`, `domain`, native OIDC |
| `lidarr.kub.local` | `lidarr.kub.internal` | forward-auth |
| `loki.kub.local` | `loki.kub.internal` | forward-auth |
| `maintainerr.kub.local` | `maintainerr.kub.internal` | forward-auth |
| `pgadmin.kub.local` | `pgadmin.kub.internal` | plain HTTP, native OIDC |
| `prometheus.kub.local` | `prometheus.kub.internal` | plain HTTP, basic auth |
| `prowlarr.kub.local` | `prowlarr.kub.internal` | forward-auth; probe `Host` |
| `qbittorrent.kub.local` | `qbittorrent.kub.internal` | forward-auth |
| `radarr.kub.local` | `radarr.kub.internal` | native OIDC; probe `Host` |
| `radarr-anime.kub.local` | `radarr-anime.kub.internal` | native OIDC; probe `Host` |
| `sonarr.kub.local` | `sonarr.kub.internal` | forward-auth |
| `sonarr-anime.kub.local` | `sonarr-anime.kub.internal` | forward-auth |
| `tautulli.kub.local` | `tautulli.kub.internal` | forward-auth |
| `traefik.kub.local` | `traefik.kub.internal` | dashboard; hand-applied `web` redirect |
| `vikunja.kub.local` | `vikunja.kub.internal` | `publicurl`, native OIDC |

`dashy`'s public route (`dashy.ashudev.com`) and every `*.kub.home.ashudev.com` route are unchanged.
The edge Pi names and `livebox.local` are unchanged.

### DNS

- **Pi-hole.** Add `address=/kub.internal/192.168.1.46` to
  `ansible/playbooks/templates/runners/02-my-wildcard-dns.conf`. The `address=/kub.local/...` line
  stays: it serves the edge names and the redirect during the soak. The file is rendered by
  `ansible/pi/hosts-pi.sh setup` on each Pi and compared by `verify`; both hosts are run, and
  `hack/check_pihole_parity.py` confirms they agree.
- **CoreDNS.** The hand-applied ConfigMap `cluster/infrastructure/coredns/custom.yaml` adds
  `forward kub.internal 192.168.1.22 192.168.1.38` beside the existing `forward kub.local ...`, so a
  pod that inherits a node resolver never sends the new zone to a public resolver. The ConfigMap key
  can be renamed from `kub-local.override` to `internal-zones.override`; the content is what
  matters. `hack/tests/test_coredns_custom.py` is extended to require both forwards.
- **The phone.** `kub.internal` is an ordinary unicast name; Android asks the Wi-Fi network's DNS
  server. The phone is fixed only if that server is a Pi-hole. This is the one prerequisite, and it
  is measured before anything is renamed (see *The phone gate*).

### Certificate

`hack/internal-cert.sh` currently signs a leaf with
`DNS:*.kub.local,DNS:*.app.local,DNS:localhost,IP:127.0.0.1`. It gains `DNS:*.kub.internal` and keeps
`*.kub.local` while the redirect exists. The script reuses the existing CA, so the root and every
client's trust stay put. The operator:

1. runs `hack/internal-cert.sh` (same output directory, new leaf),
2. checks the SAN list with `openssl x509 -noout -text`,
3. reads the current Secret's type first. The script's header records the live object as `Opaque`,
   created by hand; replacing `Opaque` with a `kubernetes.io/tls` Secret needs delete-then-create,
   because a Secret's type is immutable. If it has since become `kubernetes.io/tls`, patch its
   `data` in place instead — the type does not change, and there is no window with no default
   certificate. The old leaf is kept until the new one is proven either way,
4. confirms Traefik serves the new names with the new SAN, and
5. refreshes the copies in the secrets store and the Bitwarden item
   `homelab-k8s-kube-system-local-cert`.

The leaf file names (`kub.local.pem`, `kub.local-key.pem`) stay for the cutover; renaming them is
cosmetic and not worth the operator's muscle memory during the change.

The edge Pis keep their per-host leaves. They are not the wildcard and are not reissued.

### The old-name redirect

One new template in `cluster/helm/platform/traefik-gate/templates/legacy-redirect.yaml`, so the
shared behaviour stays with the middleware that already owns `lan-only` and `internal-tls`:

- two routers, one per entry point:
  - `web`: two rules. The first matches only the two plain-HTTP names
    (`HostRegexp(\`^pgadmin\\.kub\\.local$|^prometheus\\.kub\\.local$\`)`, `priority: 2`); the second
    matches the rest (`HostRegexp(\`^[a-z0-9-]+\\.kub\\.local$\`)`, `priority: 1`). Both list
    `lan-only` first and the redirect second, and neither sets TLS;
  - `websecure`: the general rule with `tls: {}`, so the default `local-cert` answers the SNI.
- `priority: 1` on the general rules because Traefik's default priority is rule length, and the
  `HostRegexp` rule would otherwise outrank an exact `Host` route. The plain-name rule carries
  `priority: 2` so it wins over the general rule on the same entry point, and both stay below every
  exact `Host` route (whose priority is its own length, twenty or more).
- two `redirectRegex` middlewares, because the upgrade is not the same for both groups:
  - `redirect-to-internal-tls`, `regex: ^https?://([a-z0-9-]+)\.kub\.local(.*)$`,
    `replacement: https://${1}.kub.internal${2}`;
  - `redirect-to-internal-plain`, the same regex, `replacement: http://${1}.kub.internal${2}`.
  - `permanent: false` (302) on both, so a client that retries a POST is not taught to change it to
    GET.
  - Upgrading in the redirect is what keeps an old `http://` bookmark to one hop. Replaying its
    scheme would send it to `http://<new>`, where the application's own router answers with a second
    redirect to `https://<new>`. The two exceptions are `pgadmin` and `prometheus`: they have no
    `websecure` router, so a scheme upgrade would land on a port that answers nothing, and they are
    the only names the plain middleware serves.

The redirect is a safety net for GETs and browser history, not a contract for APIs. Every API client
that holds an old name is updated explicitly; the plan lists them.

### Routes, application config and authentik

The chart host is renamed in 20 `values.yaml` files (the 23 hosts above; `argocd` and `traefik` use
their own keys, `dashy` is the internal redirect). The rest of the surface is the part that breaks
silently, so it is enumerated:

| Where | What changes |
|---|---|
| `cluster/helm/argocd/values.yaml` | `global.domain`; the ArgoCD CLI login and any saved token |
| `cluster/helm/monitoring/grafana/values.yaml` | `root_url` and `domain`; every alert link is built from `root_url` |
| `cluster/helm/vikunja/values.yaml` | `VIKUNJA_AUTH_OPENID_REDIRECTURL` and `publicurl` |
| `cluster/helm/media-stack/autobrr/values.yaml` | `AUTOBRR__OIDC_REDIRECT_URL` |
| `cluster/helm/media-stack/{radarr,radarr-anime,prowlarr}/values.yaml` | probe `Host` headers |
| `cluster/helm/authentik/files/app-*.yaml` | every `external_host`, `redirect_uris` entry and `meta_launch_url` under `*.kub.local` |
| `cluster/helm/dashy/files/conf.yml` | the internal links on the start page |
| `cluster/helm/docs-site/values.yaml` | the `host` |
| `cluster/helm/platform/traefik/values.yaml` | the dashboard `matchRule` |
| `cluster/infrastructure/traefik/dashboard/redirect.yaml` | the hand-applied `web` redirect host |
| `hack/{vikunja_site_backfill,vikunja_migrate}.py`, `hack/vikunja-seed.sh` | default URLs |
| `cluster/helm/monitoring/grafana/export/traefik/dashboard.json` | the one in-git dashboard link |

Application settings stored in an app's own database are operator steps, not chart edits:

- the *arr apps' **Allowed Hosts** must name the new hostname, or their health probe (`Host:
  radarr.kub.internal`) and the forward-auth request are refused;
- qBittorrent's host-header validation, if enabled, must accept the new name;
- Radarr's native OIDC settings, if the redirect is stored rather than derived from the request;
- Grafana dashboards in the database that hard-code an old URL. They are found and updated over
  Grafana's API, not by hand.

### The phone gate (card 894)

The rename makes the name resolvable; it does not make the phone ask the right resolver. Before the
cutover, one measurement settles it: the DNS servers on the phone's Wi-Fi network (Android's
network details show them) and the DHCP DNS option the Livebox hands out.

- If the phone already asks `192.168.1.22`/`.38`: nothing more is needed.
- If it asks the Livebox: the Livebox must hand out the two Pi-holes instead. That is the global fix
  and card 894. If the box cannot carry custom DHCP DNS, the phone-side fallback is Android's
  per-network static IP with DNS 1/2 set to the Pi-holes — the address chosen outside the DHCP pool
  or reserved, so it cannot collide — and the network-wide fallback is running DHCP on Pi-hole,
  which is its own decision. The rename is still correct for every laptop and for the next device;
  the phone's last step is the resolver, not the suffix.
- **Private DNS is part of the gate.** A manual DoT hostname (a public resolver, for example)
  overrides the network's DNS and would send `kub.internal` to a resolver that answers NXDOMAIN,
  while every other measurement looks healthy. The gate reads Settings → Network → Private DNS
  alongside the Wi-Fi DNS. `Off` and `Automatic` are both acceptable; a hostname is not.
- The phone must trust the ashulab root (the procedure is in `docs/runbooks/internal-ca.md`). A
  client that trusted `*.kub.local` already trusts the root; the new leaf is signed by the same CA.

**Decided: the rename ships now.** The rename is independent and reversible. If the Livebox can hand
out the two Pi-holes, that setting is part of this work and the phone is fixed at cutover; if it
cannot, the phone fix stays on card 894 as its own change — Pi-hole DHCP or an equivalent — and the
plan reports the phone as unverified until it lands. The rename alone is not reported as the goal
met.

### The cluster's own resolvers

Card 889 (`nuc0`'s `/etc/resolv.conf` naming `1.1.1.1`) is the same failure for a host process: a
public resolver answers `kub.internal` with NXDOMAIN, exactly as it does for `.kub.local`. It is part
of this change: `ansible/roles/resolver` runs against `nuc0` before the node-side `dig`, and every
node's `/etc/resolv.conf` is read in the same step. Card 893 (what wg-easy hands peers) is settled by
`docs/runbooks/pi-reflash.md`, which records the two Pi-holes as the peers' DNS on 2026-11-27; a peer
whose downloaded profile predates that change keeps its old list until it imports a fresh one, so the
soak re-checks it from a peer.

### Consumers outside this repository

The redirect covers a browser GET. It does not cover a stored base URL used for POST, nor a client
that does not follow a redirect. Beyond the charts, the sweep covers:

| Where | How |
|---|---|
| n8n workflows | the n8n API, `X-N8N-API-KEY` |
| Grafana dashboards and alert rules | the Grafana API (the database is the only copy) |
| Vikunja cards and comments | the Vikunja API; historical cards are left, they are a record |
| `~/.config`, shell history on the operator's machines | read-only |
| other checkouts under `~/projets` | read-only, reported as findings, not edited |
| cron, systemd timers and scripts on the nodes | read-only over the collector or SSH |
| Bitwarden URIs | operator |
| phone and tablet app configurations | operator |
| browser bookmarks | operator |

The first six are swept from a session; the last three are confirmed by the operator. A client that
cannot be checked is named in the cleanup card, never assumed absent. The redirect is not a licence
to leave one behind: anything that stores a URL for POST is migrated before the soak.

### Docs and checks

- **Living pages** (`how-it-works/`, `runbooks/`, `reference/`, `security/`, `agent-access.md`,
  `secrets.md`, `observability-conventions.md`) carry the new names. A new decision record,
  `docs/decisions/2026-11-27-internal-zone-is-kub-internal.md`, states the choice, the Android
  evidence and the soak. It goes in `mkdocs.yml`'s nav.
- **Historical records** keep their old names, as above.
- `hack/gen_docs.py` learns `.kub.internal` as an internal name (`_tls_label`), and
  `docs/reference/routes.md` is regenerated from the rendered charts. The page is not hand-edited.
- `hack/check_exposure.py` sees the new hosts as non-public and requires `lan-only`; the wildcard
  redirect is invisible to its `Host(...)` parser, so a test asserts the redirect carries `lan-only`
  and cannot become an ungated door.
- The living docs' banner check, the relative-link check, the cited-path check and the nav check run
  as `hack/check_docs.sh`; the chart checks (`check_charts.sh`, `check_app_version.sh`,
  `check_config_keys.sh`) and `check_exposure.sh` run as usual.

## Rollout order

1. **Measure and back up.** Phone Wi-Fi DNS, the phone's Private DNS setting and the Livebox DHCP
   DNS (card 894); the phone's CA trust; each node's `/etc/resolv.conf` (card 889); the current
   leaf's SAN and expiry; a copy of the leaf in the secrets store. Run `ansible/roles/resolver`
   against `nuc0` if it still names a public resolver, before any node-side check.
2. **New names answer, old ones untouched.** Pi-hole wildcard on both Pis, CoreDNS forward, the
   reissued leaf and the replaced Secret. Check `dig` from a LAN host and `openssl s_client` for a
   new name. The old names still serve exactly as before.
3. **Add the redirect while it is dormant.** Apply `traefik-gate` first, before any chart is renamed.
   An exact `Host` route outranks the wildcard's `priority: 1`, so a request to a live old name still
   reaches its application; a request to an old name with no route already reaches the redirect.
   This removes the window in which a renamed app has neither an old route nor the redirect.
4. **Flip the charts and configs.** Rename the hosts and the stored URLs, update the authentik
   blueprints, Dashy links and the docs generator, and push to `develop`. ArgoCD syncs the charts
   one Application at a time; each old name falls through to the already-running redirect, so the
   gap is a request, not a minutes-long outage. Apply the hand-maintained CoreDNS ConfigMap and the
   Traefik dashboard redirect, and restart CoreDNS.
5. **Verify every name on both sides** (below), including one sign-in per auth style.
6. **Soak** for a fixed period agreed with the operator (30 days proposed). Old links keep working.
7. **Cleanup.** Remove the wildcard redirect, the `*.kub.local` route remnants if any, and the
   `*.kub.internal`-era docs, then the `kub.local` wildcard from Pi-hole and the `*.kub.local` SAN
   from the leaf once the soak evidence is clean. The edge names and their per-host leaves stay. The
   cleanup is a change of its own, with its own review. The evidence for "no client uses the old
   zone" is the Pi-hole query log on both Pis and the Traefik access log in Loki: no query for one
   of the 23 app names and no redirect hit from a real client over the last 30 days of the soak,
   excluding this change's own verification. A host still appearing is migrated, or the soak is
   extended. Log retention is checked before the soak starts, so the window is covered.

## Verification

- `dig @192.168.1.22` and `@192.168.1.38` for every new name answer `192.168.1.46`.
- `curl` with the internal CA for each HTTPS name: 200 (or the app's login page), and the leaf SAN
  read from the wire carries `*.kub.internal`.
- The phone on Wi-Fi, tunnel off: `https://grafana.kub.internal` opens with no certificate warning;
  a `*.kub.local` bookmark lands on the new name from a laptop.
- One login per auth style: forward-auth (`sonarr`), native OIDC (`grafana`, `vikunja`, `radarr`),
  basic auth (`prometheus`), no auth (`pgadmin`).
- The *arr probes are green (the `Host` header and the app's Allowed Hosts agree).
- `helm lint`/`template` for every chart; the full `hack/tests` suite; `hack/check_exposure.sh`;
  `hack/check_docs.sh`; `hack/check_charts.sh`; `hack/check_app_version.sh`;
  `hack/check_config_keys.sh`.
- The Edge Pi names, the public names and the WireGuard peer path are unchanged.

## Rollback

Until the cleanup commit, `git revert` restores the old routes and configs; the DNS and certificate
additions are additive and harmless, and the redirect can be deleted. The old zone still answers
throughout the soak, so a revert is a DNS/route change, not a data migration. After the cleanup, the
old names are gone by design and a rollback is a new change.

## Risks

| Risk | Why it matters | Mitigation |
|---|---|---|
| Phone still asks the Livebox | the new name fails exactly as `.local` did | measure first; card 894; manual Wi-Fi DNS fallback |
| Phone lacks the CA root | certificate warning on every app | verify, install the root from `runbooks/internal-ca.md` |
| OIDC redirect mismatch | login loop or `redirect_uri` error | update provider and app together; one login per style in verification |
| *arr Allowed Hosts | probe and forward-auth return 400 | add the new host in the app settings before the probe flips |
| API client on an old name | a 302 may turn POST into GET | update ArgoCD CLI, scripts and phone apps explicitly; the redirect is for GETs |
| Secret replacement error | Traefik serves no default cert for the internal names | keep the old leaf; verify with `openssl s_client`; recreate |
| CoreDNS ConfigMap not applied | pods get NXDOMAIN for the new zone | apply and restart in the same step; a pod-side `dig` in verification |
| Wildcard router priority | the redirect could shadow exact hosts | `priority: 1`; browse a specific app in verification |
| Plain-HTTP names | a scheme upgrade in the redirect breaks `pgadmin`/`prometheus` | they get their own rule and middleware that keep `http`; both are checked |
| Grafana links in the database | alert and dashboard links point at the old name | API sweep; old links redirect for GETs |
| Two Pi-holes drift | one host answers the new zone, the other does not | `hosts-pi.sh setup` on both; `check_pihole_parity.py` |
| The soak is forgotten | the old zone and the dual SAN live forever | the cleanup is a dated card with the soak end in its title |

## What is left to the builder

- The exact order of the chart commits (one commit per area or one wide commit) is a planning call.
- The soak length (30 days proposed) is the operator's call.
- The Grafana database sweep needs the API credential; if no token is available, it is an operator
  step, and the decision record says so.
- The edge Pi names are explicitly a follow-up, not part of this change.
- Whether to rename the leaf files is cosmetic; the plan leaves them alone during the cutover.
