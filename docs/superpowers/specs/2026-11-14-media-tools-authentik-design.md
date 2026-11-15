# Authentik in front of the media tools

Date: 2026-11-14
Status: draft, awaiting approval

## 1. Intent

Nine media tools answer on `http://<tool>.kub.local` with nothing in front of them: autobrr,
bazarr, sonarr, lidarr, radarr, qbittorrent, maintainerr, prowlarr and tautulli. Each has its own
login, some have a weak one, and maintainerr has none. The operator is the only person who should
reach them.

The goal is one Authentik sign-in gate in front of all nine, and the tools' own logins switched off
where the tool allows it, so that opening Sonarr after signing in to Authentik asks for nothing.

None of these nine speaks OIDC (OpenID Connect, the sign-in protocol Authentik normally uses), so
there is no native integration to configure. Authentik's forward-auth mode is the only mechanism
that fits: a check in front of each host that stops anyone without a valid Authentik session.

## 2. Goals

- An unauthenticated browser request to any of the nine hosts is redirected to Authentik, and only
  after signing in does it reach the tool.
- An existing Authentik session opens a tool without another prompt.
- A signed-in Authentik user who is not an administrator is refused.
- The nine tools' own logins are turned off where the software allows, so the operator sees one
  login, not two.
- The whole arrangement lives in git and is applied by ArgoCD, with no recurring manual step.

## 3. Non-goals

- Not moving these nine to TLS. They stay on the `web` entry point over `http://<tool>.kub.local`.
- Not protecting the anime twins (sonarr-anime, radarr-anime, bazarr-anime) or any other media
  namespace app. Same recipe, different request.
- Not changing how apps inside the cluster talk to each other. See §6.
- Not adding MFA, changing groups, or touching the existing OIDC applications.

This work deliberately overrides a non-goal in `2026-11-14-sso-access-model-design.md` ("No proxy
gate in front of applications that would break their phone apps or APIs"), which assumed a gate
would break clients. The operator chose the gate knowingly for these nine; that line and the
matching statements in `docs/charts/README.md` must be corrected rather than left to contradict.

## 4. Anti-goals

These would each mean the work failed even if every pod stayed healthy:

- Breaking a cluster-internal integration (Prowlarr feeding Sonarr, Bazarr reading Radarr,
  Maintainerr calling the three \*arrs, cross-seed driving qBittorrent).
- Leaving one of the nine reachable without authentication.
- Leaving a step that has to be repeated by hand after every authentik restart.

## 5. Constraints found in the repo

- **Charts already have the hook.** Eight of the nine `values.yaml` files carry
  `ingressRoute.middlewares: []`, rendered by `templates/ingressroute.yaml` as
  `<namespace>-<name>@kubernetescrd`. Populating it is a values change, no template change.
- **qBittorrent is the exception.** `cluster/helm/media-stack/qbittorrent/templates/ingress.yaml`
  hardcodes `Host(\`qbittorrent.kub.local\`)`, `entryPoints: [web]` and has no middleware list. It
  needs a small template edit plus a values key.
- **The middleware already exists.** `cluster/infrastructure/traefik/middleware/authentik-forwardauth.yaml`
  defines `kube-system/authentik-forwardauth`, pointing at
  `http://authentik-server.authentik.svc.cluster.local/outpost.goauthentik.io/auth/traefik`.
  `cluster/infrastructure/` is applied by hand, but this particular object is already live, so no
  hand step is needed here. Verify it is present before starting; if it is missing, apply that one
  file.
- **Blueprints are chart files.** `cluster/helm/authentik/templates/blueprints-configmap.yaml` turns
  every `files/*.yaml` into the `authentik-blueprints` ConfigMap, which the worker reads. A file
  that fails rolls back atomically and names itself in the worker log.
- **Blueprint entry ids are local to a file.** `!KeyOf` cannot reach into another blueprint file;
  anything cross-file has to use `!Find` against the database.
- **The embedded outpost is a managed object** keyed by `managed`, not `name`, and already serves
  FileBrowser. Attaching providers to it is the one step the repo currently leaves to the UI.
- **`authentik Admins` exists** and is the group every other application blueprint binds to.
- **Plain HTTP is supported, checked in upstream source.** The outpost's session cookie takes its
  `Secure` flag from the scheme of the provider's `external_host`
  (`src/outpost/proxy/application/mod.rs`, `external_url.scheme() == "https"`). A provider whose
  external host is `http://...` therefore issues a non-`Secure`, `SameSite=Lax` cookie that the
  browser keeps over plain HTTP. Two consequences: nothing here needs TLS, and `external_host` must
  say `http://` or the cookie stops working.

## 6. What does not change, and why that is safe

Every integration between these apps uses Service DNS inside the cluster
(`http://sonarr.media-stack.svc.cluster.local:8989` and so on), so it never passes through Traefik
and never meets the forward-auth check. Only browser traffic uses the `kub.local` names. Nothing in
the nine charts points at a `kub.local` host for API calls; this was checked before writing this
document and must be rechecked if any of them is reconfigured.

The consequence to accept knowingly: with the tools' own logins off, anything inside the cluster
that can reach a Service directly has full access to that tool. That is the same trust boundary the
cluster already extends to every pod, and the operator chose it.

## 7. Approaches considered

**A. One proxy provider per tool, all attached to the embedded outpost.** Chosen.

Nine Authentik applications, nine proxy providers, one middleware per router. Nine tiles in the
Authentik application grid, one per tool. Costs nine objects and one outpost attachment problem;
buys per-tool policy later, matches the existing one-blueprint-per-application convention, and does
not depend on browser behaviour for a made-up domain name.

**B. One provider in "forward domain" mode for `*.kub.local`.** Rejected.

One provider and one policy for the whole zone, with a single cookie on the parent `.local` domain,
so every current and future `kub.local` host is covered at once. Rejected because it makes access
all-or-nothing, collapses nine tiles into one, and bets all nine tools on how browsers treat cookies
for `.local`, a reserved name. If that misbehaves, it is discovered at cutover with nine apps down.

**C. Native OIDC per tool.** Not available for most of them. Only autobrr has any OIDC support among the
nine, and the rest do not, so this cannot cover the set. Calibre-Web, added later, turned out to have it
too — see §12.4 — so the claim is accurate for the nine and was wrong for the tenth.

## 8. What gets built

### 8.1 Authentik side

One new blueprint file per tool, `cluster/helm/authentik/files/app-<tool>.yaml`, each self-contained:

| Object | Details |
|---|---|
| `authentik_providers_proxy.proxyprovider` | `mode: forward_single`, `external_host: http://<tool>.kub.local`, default authorization and invalidation flows |
| `authentik_core.application` | `slug: <tool>`, name the tool's display name, `meta_launch_url: http://<tool>.kub.local/` |
| `authentik_policies.policybinding` | target the application, `group: authentik Admins` |

No client secret is involved: forward auth uses no OIDC client.

The host in `external_host` must match exactly what the browser shows, scheme included. These are
`http` hosts, so the entries say `http://`, not `https://` as FileBrowser's does.

### 8.2 Attaching the nine providers to the embedded outpost

This is the one genuinely new mechanism, and the only part of the design that is not already proven
in this repo. Two ways:

- **Declarative (chosen).** One file, `cluster/helm/authentik/files/outpost-embedded.yaml`, sets the
  embedded outpost's `providers` list to the nine new providers plus FileBrowser's, each resolved
  with `!Find [authentik_providers_proxy.proxyprovider, [name, <name>]]`. It declares
  `metaapplyblueprint` entries requiring each `app-<tool>.yaml` so the providers exist first.
  FileBrowser must be in the list: setting `providers` replaces the whole list, and leaving it out
  would detach FileBrowser.
- **UI fallback.** If the blueprint refuses to write the managed outpost, attach each provider by
  hand in the Authentik UI and record it in the runbook. This is FileBrowser's current path.

The declarative route is chosen because it removes a step that fails silently (a 403 at the browser,
with no clue why) and repeats after every rebuild. Reverting to the UI is a single file deletion.

Trade-off to accept: git becomes the source of truth for that outpost. A provider attached by hand
later is dropped on the next worker start.

### 8.3 Traefik side

Eight charts: fill `ingressRoute.middlewares` with one entry.

```yaml
ingressRoute:
  middlewares:
    - name: authentik-forwardauth
      namespace: kube-system
```

qBittorrent: add a `middlewares` list under the existing `ingress:` key in `values.yaml` and render
it in `templates/ingress.yaml` with the same `<namespace>-<name>@kubernetescrd` expression the other
eight use, so the two shapes stay identical in meaning.

`strip-authentik-header` is **not** added. None of these nine trusts an Authentik header; they
simply run with their own login off. Adding it would imply a protection that is not needed here.

### 8.4 Tool side: turning off the internal login

Applied by hand in each tool's UI after the gate works, because none of these settings lives in git.

| Tool | Setting | Note |
|---|---|---|
| sonarr, radarr, lidarr, prowlarr | Settings → General → Authentication Required: `Disabled for Local Addresses`; add `0.0.0.0/0, ::/0` to Ignore Authentication for these networks | The ignore list is explicit; relying on the private-range default alone would break the day Traefik's pod IP stops looking local |
| bazarr | Settings → General → Authentication Type: `None` | |
| tautulli | Settings → Web Interface → turn off the login, no guest restrictions | |
| qbittorrent | Preferences → Web UI → bypass authentication for whitelisted IPs, whitelist the cluster pod network (`10.42.0.0/16`) | API users inside the cluster keep working: they authenticate with credentials that remain valid |
| maintainerr | nothing to do | no internal login exists |
| autobrr | verify in the UI; disable if the software allows it | if it cannot be disabled, a second prompt stays and is accepted |

API keys are unaffected by the login settings and keep working for the internal integrations in §6.

### 8.5 Documentation

- `docs/runbooks/authentik-apps.md`: a section covering the nine, the middleware, the outpost
  attachment, the UI settings in §8.4, and the recovery route in §9.5, so a rebuild can be repeated.
- `docs/superpowers/specs/2026-11-14-sso-access-model-design.md`: two statements become false — that
  FileBrowser is the only forward-auth consumer, and the non-goal about proxy gates breaking phone
  apps and APIs. Correct both, and say why the second was overridden.
- `docs/charts/README.md`: the tautulli and maintainerr entries state that the LAN boundary is the
  only access control. Both now have a gate; correct those entries.

## 9. Rollout: three merges, one tool at a time

The applications pin `targetRevision: develop`, and `develop` only takes changes through a pull
request, so the pilot cannot be staged on a branch. It is staged in three merges instead; each one
is small enough to read and to undo on its own.

**Merge 1 — the application exists, and nothing is gated.**

One `app-sonarr.yaml` blueprint and the outpost file. This creates Sonarr's provider, application
and policy binding, and attaches it to the embedded outpost. Because no chart carries a middleware
yet, no tool is gated, so a mistake here is invisible to users rather than an outage.

**Merge 2 — Sonarr behind the gate.**

The middleware in Sonarr's values only. Sonarr is the pilot because it has the most internal
callers, so it is the best test of §6.

1. Browser test in a private window: authentik login, then Sonarr.
2. Confirm Prowlarr, Bazarr and Maintainerr still talk to Sonarr.
3. Turn off Sonarr's own login; repeat the browser test.

Merge only when those pass.

**Merge 3 — the other eight, the qBittorrent template, and the docs.**

The eight remaining blueprints extend the same outpost file; then the eight charts, the qBittorrent
template and values, and §8.5. Same browser check per tool. Merge when all pass.

The per-task detail is in `docs/superpowers/plans/2026-11-14-media-tools-authentik.md`.

### 9.5 Recovery when authentik is unreachable

Gating nine tools behind one service means that service becomes their single point of failure. The
runbook records both routes, and the second is exercised as a drill on one tool during PR 1:

- **Read access now:** `kubectl -n media-stack port-forward svc/<tool> <local-port>` reaches the tool
  without Traefik, and therefore without the gate. This works because the tools' own logins are off.
- **Restore the gate's absence:** revert the `middlewares` block in the affected chart's
  `values.yaml` and merge; ArgoCD applies it. This is the documented, tested path, not an emergency
  `kubectl edit`.

## 10. How we know it worked

Per tool, from evidence rather than assumption:

- `curl -sI http://<tool>.kub.local/` without a session returns a redirect to
  `authentik.kub.home.ashudev.com`, not a `200` from the tool.
- The same request with a valid session cookie returns the tool's page.
- A non-administrator Authentik account is refused.
- **Settings audit before each tool's cutover:** each tool's own UI is checked for a stored URL that
  points at another tool's `kub.local` name rather than its Service name. Maintainerr's \*arr URLs,
  autobrr's qBittorrent URL, cross-seed's config and recyclarr's config are the ones that carry
  such URLs and none of them is in git. A hit is rewritten to the Service name before the gate goes
  on that tool; otherwise the integration dies silently, since a daemon failing is not a browser
  error.
- The integration checks in §9 step 5 pass for Sonarr, Radarr, Lidarr, Prowlarr, Bazarr and
  Maintainerr.
- `helm template` renders every changed chart without error (the repo's pre-commit check).

## 11. Left to the builder

- The exact display name of each application in Authentik's grid.
- Whether `metaapplyblueprint` on nine files is acceptable boilerplate, or whether the nine
  providers should collapse into one file when the entries are near-identical.
- Where the outpost file goes if the blueprint rejects a `managed` identifier: the UI fallback, with
  the runbook updated to match.
- Wording of the runbook section.

## 12. Implementation record (2026-11-15)

The design above was followed, with four changes made during the work. Each is recorded with the
reason, because they change what a later reader should expect.

### 12.1 autobrr uses native OIDC instead of the gate

autobrr supports OIDC as a first-party feature, documented at
`autobrr.com/configuration/authentication` and present in the pinned `v1.90.0`. Stacking the gate
on top would have meant two authentik applications for one tool and a second redirect. The route
keeps `lan-only` and nothing else; the app is its own gate. Side effect, and the reason the
operator chose it: `/api` stays reachable from the LAN with the API key, with no exemption rule.

Consequence: autobrr cannot log the user out of authentik. Its logout destroys its own session and
returns `204`, and it has no OIDC logout URL in `v1.90.0` or `main`. Accepted rather than reverting
to header trust, which would have required `authAllowedPeerCIDRs` over the pod network.

### 12.2 The pod network joined the `lan-only` allowlist

`lan-only` allowed the LAN and WireGuard only, so any **server-side** OIDC call to a public
hostname was answered `403`. That is every native-OIDC client: Grafana's token call, Argo CD's Dex
issuer, pgAdmin's metadata fetch, autobrr's discovery. `10.42.0.0/16` was added to the middleware
(`traefik-gate` chart). It grants nothing new — pods could already reach every Service over Service
DNS — and no public address falls in that range.

### 12.3 A selective `/api` exemption instead of gating or not gating

API clients send an API key, not a browser session. Rather than choose between breaking scripts and
leaving a tool ungated, bazarr and bazarr-anime carry a second rule on the same IngressRoute:

```yaml
Host(<tool>.kub.local) && PathPrefix(/api)   priority: 100   middlewares: [lan-only]
```

Applied only where the API has its own credential. Maintainerr has neither login nor key, so its
API stays gated; exempting it would remove the only control it has.

### 12.4 Calibre-Web was added, beyond the original nine — first with the gate, then over OIDC

It was first put behind `lan-only` + `strip-authentik-header` + `authentik-forwardauth`, on the strength
of §7's claim that none of these tools speaks OIDC and that Calibre-Web only supports reverse-proxy
header authentication. The claim was wrong for the pinned image: **Calibre-Web Automated v4.0.8 has a
generic OIDC client** (`cps/oauth_bb.py`, whose module docstring names authentik), so it is now an OAuth2
provider in authentik with no gate in front of it, exactly as autobrr is. Two reasons the gate was the
wrong shape rather than merely unnecessary:

- `/opds` and the Kobo sync endpoints authenticate with basic auth and cannot follow a redirect to
  authentik, so a gate would have needed an exemption there anyway;
- the header path needs the local account to be renamed to the authentik username, and its own
  header-trust setting switched on; OIDC registers the account through the normal sign-in.

**It also forced TLS, which no other media tool has.** The OIDC button only renders when Calibre-Web's
login type is `OAuth`, and that setting forces `SESSION_COOKIE_SECURE=True` (`cps/__init__.py:134`); a
browser drops a `Secure` cookie on a plain-HTTP origin, so the callback would come back without its
session and the password login would stop sticking too. The route therefore moved to `websecure` on
`https://calibre.kub.local`, with the `web` router redirecting, and the cluster's default certificate
(`kube-system/local-cert`) rather than an ACME one — a `.local` name cannot be issued for, since public
CAs have been barred from internal names since 2015. That certificate turned out to have expired on
2026-05-06; `hack/internal-cert.sh` now signs it from a private CA kept outside the repo, so the next
renewal is a documented command rather than a lost artefact. Clients without the root see a warning; that
is accepted.

The local password survives as break-glass and is what `/opds` uses. Because the session cookie is now
`Secure` everywhere, that break-glass login works only on `http://localhost:8083` after a port-forward;
browsers exempt `localhost` from the https rule.

### 12.6 The whole media stack moved to TLS (2026-11-15)

§3 declared TLS out of scope: the tools are on `web` over plain HTTP and `lan-only` is the only
restriction. Calibre-Web broke that assumption — its OIDC needs a `Secure` session cookie — and the
operator then asked for the same treatment everywhere in the namespace. So every media-stack `kub.local`
route is now served on `websecure`, with a `web` twin that redirects.

Three mechanisms, deliberately shared rather than copied per chart:

| Object | Where | Why |
|---|---|---|
| `kube-system/redirect-to-https` | `traefik-gate` chart | one `redirectScheme` for all 13 redirect routers |
| `kube-system/internal-tls` | `traefik-gate` chart | one TLSOption; a copy per chart would only let them drift |
| `kube-system/local-cert` | Secret, signed by `hack/internal-cert.sh` | the default certificate, `*.kub.local`, see `docs/runbooks/internal-ca.md` |

What changed in the charts: `entryPoints: [websecure]`, `tls.enabled: true` with `options: internal-tls`,
`redirectToHttps: true`, and a `-redirect` IngressRoute rendered from `ingressroute.yaml`. qBittorrent's
hand-written `ingress.yaml` got the same shape, since it never had a values-driven route.

What changed in authentik: the nine proxy providers now say `https://<tool>.kub.local` in `external_host`,
and autobrr's registered callback became `https://autobrr.kub.local/api/auth/oidc/callback`. Both matter
mechanically — the outpost takes its cookie's `Secure` flag from the provider scheme, and autobrr builds
its callback from `X-Forwarded-Proto` — and neither is visible from the chart side.

The `-redirect` routers carry `lan-only` and nothing else. Putting the gate there sent a plain-HTTP visitor
through authentik first and then back to `http`, which is a redirect loop waiting to happen; the gate runs
on the TLS router, once.

Consequence to accept: every client needs the private root installed, and anything calling
`http://<tool>.kub.local` from a script now gets a `301`. Both are recorded in the runbook.

### 12.5 What is finished and what is not

Finished and verified live: sonarr, radarr, lidarr, prowlarr, bazarr, bazarr-anime, tautulli,
maintainerr, autobrr (OIDC), calibre (OIDC over TLS, signed in from the button on 2026-11-15) and kavita
(OIDC) — with the two bazarrs exempting `/api`.

Not finished:

- **qbittorrent** is not gated. Its `templates/ingress.yaml` is hand-written rather than the shared
  `ingressroute.yaml`, so a middleware list must be added there before the values can carry one.
- **tautulli** still asks for its own login. Its web auth has no API command; it is a UI toggle
  under Settings → Web Interface.
- The other six tools' APIs are still gated. Only bazarr and bazarr-anime have the
  `apiBypassMiddlewares` template block; the rest need it copied in.
- autobrr's post-logout "no state found in session" is a browser-cache symptom, worked around by a
  hard reload. A `Cache-Control: no-store` on `/api/auth/oidc/config` is the targeted fix.

The operational detail — recipes, recovery, and the seven traps that cost the most time — is in
`docs/runbooks/authentik-apps.md`, section "Media tools behind the gate".
