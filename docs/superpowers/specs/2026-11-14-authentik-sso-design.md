# Authentik single sign-on — design

Status: draft, awaiting approval.
Date: 2026-11-14.

The work is one programme in three phases, each with its own implementation plan: repair the
instance, add the two sign-in sources, then wire applications one at a time.

Vocabulary used below:

- **SSO (single sign-on)**: one login service that other applications trust instead of asking for
  their own password.
- **OIDC (OpenID Connect)**: the modern handshake an application uses to trust that login service.
  SAML is the older equivalent.
- **Provider**: the authentik object that describes one application's trust (client ID, secret,
  allowed redirect address).
- **Source**: a place people can prove who they are *to* authentik — Discord and Plex here.
- **Blueprint**: a YAML file that describes authentik objects; authentik applies it on startup or
  on a schedule, so the configuration lives in git instead of only in its database.
- **Outpost**: an authentik component that enforces access for applications that cannot speak
  OIDC. Not used in this programme.

## 1. What this is for

Authentik becomes the single login for every application that natively supports OIDC or SAML.
People sign in to authentik with their Discord or Plex account, and each application trusts
authentik's answer. Applications that have no native support keep their own password and stay
outside authentik, by decision: no proxy gate is placed in front of them, because that breaks
phone apps and API clients.

## 2. What exists today

Evidence as of 2026-11-14.

- `cluster/helm/authentik` is a wrapper chart (pattern D) pinning upstream `2025.12.4` while
  forcing image `2026.2.1`. Upstream current is `2026.8.5`.
- Both authentik Deployments are at 0 replicas. The Application is `OutOfSync`/`Healthy`. Only
  the bundled Redis runs (368 days). Nothing in `docs/` records a deliberate stop.
- The chart's affinity targets `node-type: general`, a label the cluster retired, so the pods
  could not schedule if scaled up.
- The chart renders a plain `networking.k8s.io/v1` Ingress with Traefik annotations. The house
  rule is Traefik CRDs only.
- The Traefik middleware named `authentik` (`cluster/infrastructure/traefik/middleware/authentik.yaml`)
  only sets CORS headers. filebrowser, firefly and dashy reference it; it enforces nothing.
- PostgreSQL in `databases` is 3 hours old and authentik's database was not carried over. All
  previous authentik configuration is gone; this is a rebuild, not a restore.
- `authentik.kub.home.ashudev.com` resolves to `192.168.1.46` on the LAN and to the public
  address `80.14.57.209` everywhere else, because `*.kub.home.ashudev.com` is a public wildcard.
  Every "internal" application is therefore reachable from the internet. The internal/public
  split is DNS convenience, not a security boundary.
- `authentik.ashudev.com` resolves only on the LAN today and has no public DNS record.
- Let's Encrypt issues certificates for both name families through the existing
  `myresolver` (TLS-ALPN-01). The current certificate for
  `authentik.kub.home.ashudev.com` was issued 2026-11-08.

## 3. Goals, non-goals, anti-goals

Goals:

- authentik runs on the current upstream release, managed by ArgoCD, with its configuration in
  git and no manual UI state that a rebuild would lose.
- People can sign in with Discord and with Plex.
- Every application with native OIDC/SAML is protected by authentik, in a deliberate order.
- A rebuild of the cluster restores the same authentik configuration without clicks.

Non-goals:

- Protecting applications that have no native SSO. This excludes, by decision, the *arr
  applications, qBittorrent, Tautulli, Plex itself, Syncthing, FreshRSS, TheLounge, Koillection,
  LinkStack, Dashy, Calibre-Web and Filebrowser.
- Signing in to Plex clients with authentik. Plex has no SSO; it can only act as a source.
- Replacing local application administrator accounts during the first wiring of an app.
- Multi-factor authentication, invitations, or per-application fine-grained policy beyond a
  single "allowed users" group.

Anti-goals (these would make the result a failure even if it works):

- A proxy gate in front of an app that breaks its phone app or API.
- Configuration that exists only in authentik's database and disappears on the next rebuild.
- Any secret committed to git.
- Losing the ability to administer authentik if a source provider (Discord, Plex) is unavailable.

## 4. Constraints

- GitOps: chart changes reach the cluster only through `develop`; Applications carry no
  `syncPolicy.automated` in git.
- No secrets in git. New secret values are created by the operator with `kubectl`.
- Ingress is Traefik v2 CRDs (`traefik.containo.us/v1alpha1`), TLS by `myresolver`.
- Workloads are pinned to a named node; `nuc0` is the general catalogue node.
- PostgreSQL 18.7 is supported by authentik 2026.8 (supported range 14–18).
- Blueprints are mounted into the worker only, which is where they are applied.
- ArgoCD runs `helm dependency build`; `charts/` stays out of git for wrapper charts.

## 5. Decisions

### 5.1 Configuration lives in git, as blueprints

Alternatives considered:

| Option | Trade-off |
|---|---|
| Blueprints in git | Reproducible and reviewable; every change is a commit plus a sync; needs the secrets to be referenced from environment, which the blueprint `!Env` tag supports. **Chosen.** |
| Click it in the UI | Fastest first pass; state lives only in the database, which is exactly the loss being repaired. Rejected. |
| Hybrid (apps in git, sources in UI) | Less work now; splits the truth in two and leaves the fiddliest part untracked. Rejected. |

One blueprint file per application, not one large file: a blueprint is applied as a single
transaction, so one broken entry would otherwise roll back every application. Separate files
also make a per-application rollback a matter of deleting one file.

### 5.2 One hostname (revised)

Alternatives considered:

| Option | Trade-off |
|---|---|
| One public name (`authentik.ashudev.com`) | Simplest, but changes the address internal apps already point at. |
| Internal only (`authentik.kub.home.ashudev.com`) | Chosen. Its wildcard DNS already answers with the public address, so it works from the internet as well as the LAN. |
| Two names | Initially chosen, then dropped: the second name adds a DNS record and a certificate without adding reach, because `*.kub.home.ashudev.com` is already public. |

`authentik.kub.home.ashudev.com` is the only address. LAN DNS answers `192.168.1.46` and the
public wildcard answers `80.14.57.209`, so the same name works on both sides. The session cookie
is scoped to that host; nothing else on `ashudev.com` receives it.

**Issuer rule.** Every provider names `https://authentik.kub.home.ashudev.com` as its issuer,
including applications reached from the internet. Browsers and in-cluster backends both reach
that name, so no request hairpins through the public IP. Phase 1 verifies that a pod resolves it.

One router, one certificate. There is no public DNS record to create, so there is no ACME order
that can fail on a name that does not resolve.

### 5.3 Builder calls

- Chart and image both `2026.8.5`, pinned exactly.
- Database `authentik` and role `authentik` created with the existing PostgreSQL runbook; the
  instance starts empty.
- Placement `kubernetes.io/hostname: nuc0`; the retired `node-type` affinity is removed.
- No Redis. 2026.8 moved the task broker onto PostgreSQL and the chart no longer ships a Redis
  subchart; the old StatefulSet is deleted after the sync. Sessions live in PostgreSQL.
- The chart's Ingress is disabled and replaced by one `IngressRoute` for the hostname, plus a
  redirect router (`templates/ingressroute.yaml`), with the existing `redirect-https` and
  `security` middlewares, which are the ones confirmed live in `kube-system`. The chart ships two
  other security middlewares in git that are not deployed; they are not referenced.
- The local `akadmin` password login remains as break-glass; sources are added beside it, not
  instead of it.
- A fresh `AUTHENTIK_SECRET_KEY` and `AUTHENTIK_BOOTSTRAP_PASSWORD` are generated for the empty
  database and recorded in the operator's password manager. Rotating both is safe only because
  there is nothing to preserve.
- Email settings are kept but do not block: SMTP credentials live in the secret and can stay
  empty for now.
- Per-application client secrets live in the single `authentik-sso` secret, one key per
  application, exposed with `envFrom`; blueprints read them with `!Env`. One secret to maintain,
  and `docs/secrets.md` records each key.
- A PostSync hook runs after every ArgoCD sync and deletes the worker pod, so the worker restarts
  and applies the committed blueprints immediately. A checksum annotation on the worker template
  would do the same, but it cannot be set through a dependency's values, and patching the
  Deployment would show as drift on every sync. Deleting a pod creates none.
- Renovate groups the authentik chart dependency and the server image so they bump in one pull
  request; the mismatch being repaired came from them moving separately.
- Application-side OIDC settings that have no chart knob (Kavita, Wiki.js, Discourse, …) are
  clicked once through the app's own admin interface and recorded in the runbook. The
  no-clicks goal covers authentik's configuration, not application databases.

### 5.4 Enrollment gate — membership (decided)

The login page is public, and both sources can create an account on first sign-in. Open
enrollment was rejected: anyone with a Discord account could then reach every wired application
with a read-only role.

| Option | Consequence |
|---|---|
| Closed | Nobody self-enrolls; every identity is created deliberately. Safest, most administration. Rejected: too much upkeep for a household. |
| **Membership** | **Chosen.** Discord admits members of the homelab server; Plex admits users of the server. Inviting someone to the Discord server is the act of granting access, and removing them takes it away. |
| Fixed ID list | Safe, but every new person needs a commit. Rejected as unnecessary upkeep once guild membership is available. |
| Open | Rejected: a public page plus open enrollment is an open door. |

Mechanics:

- **Discord.** The source requests the `guilds guilds.members.read` scopes. A source property
  mapping asks Discord, on each sign-in, whether the account is a member of the homelab guild and
  stores the answer with the user's attributes. An expression policy then reads that answer and
  refuses anyone who is not a member. The policy is bound to **both** the enrollment and the
  authentication flow, so a person removed from the server is refused at their next login rather
  than keeping access through an existing account. The guild ID is not a secret and lives in the
  blueprint.
- **Plex.** The source admits the owner and anyone with access to the AshuFlex server, matched by
  the server's machine identifier in `allowed_servers`. The friend list is not used: Plex retired
  the `/api/v2/friends` endpoint and authentik 2026.8.5 still calls it when `allow_friends` is on,
  which fails with HTTP 410 and aborts the sign-in.
- Both sources land admitted identities in `users`. Nobody reaches an application without
  passing the gate first.

The cost is a dependency: Discord's API is consulted at each Discord sign-in. If Discord is
unreachable, that sign-in fails. Plex sign-in and the local break-glass admin are unaffected.

## 6. Architecture

```
browser ──443──► Traefik ──► Service authentik-server:80 ──► server pod
                                                            │
Discord / Plex ◄── OAuth redirects ──────────────────────────┤
                                                            │
                    ┌──────────── worker pod (applies blueprints)
                    │                │
              ConfigMap blueprints   └──► postgres.databases.svc:5432
```

- **Server** answers logins and application handshakes; **worker** runs background tasks and
  applies blueprints; **PostgreSQL** holds everything durable, including sessions and the task
  queue.
- **Blueprint source.** Chart templates render one ConfigMap from `files/*.yaml`; values mount it
  with `blueprints.configMaps`. The files are committed in the chart.
- **Secrets.** `authentik-env` keeps `AUTHENTIK_SECRET_KEY`, the PostgreSQL password and the
  bootstrap password. A second secret, `authentik-sso`, holds the Discord client ID and secret,
  the Plex token, and one client secret per application wired in phase 3; values expose it with
  `envFrom`, and blueprints read each value with `!Env`. Both secrets are created by the operator,
  never in git.
- **Flows.** Password login uses authentik's built-in `default-authentication-flow`, which the two
  sources are attached to. First-time source logins use `default-source-enrollment` and land in
  a group named `users`. Application authorization uses
  `default-provider-authorization-implicit-consent`: once a person is signed in, opening an
  application does not ask again.
- **Access model.** `users` may open every application wired in phase 3, with that app's
  read-only or viewer role by default (ArgoCD read-only, Grafana Viewer); a higher role per app
  is a separate decision. `authentik Admins` (built-in) may administer authentik. Fine-grained
  per-application groups are deferred until a real need appears.
- **Issuer rule.** Every provider names `https://authentik.kub.home.ashudev.com`; browsers and
  in-cluster backends both reach it, because the name answers with the LAN address from inside
  and the public address from outside. The provider's redirect address must match the
  application's real URL exactly.
- **OIDC providers** are confidential clients (client ID and secret), signing with authentik's
  built-in self-signed key pair. Each provider names exact redirect addresses rather than a
  wildcard.

## 7. Phases

### Phase 1 — Repave the instance

Outcome: authentik is back on the current release, the hostname serves a valid certificate, the
admin can log in, blueprints are wired (initially just the `users` group), and nothing is
clicked that a rebuild would lose.

Work:

1. Create role and database `authentik` on the new PostgreSQL with the documented runbook; write
   the password into `authentik-env` and fix the database host there.
2. Generate a fresh `AUTHENTIK_SECRET_KEY` and `AUTHENTIK_BOOTSTRAP_PASSWORD`; the operator
   records them before the first sync.
3. Bump the chart dependency to `2026.8.5`, the image to `2026.8.5`, and correct values:
   database host `postgres.databases.svc.cluster.local`, `nuc0` pin, memory requests and limits,
   probes, cookie domain, bootstrap admin.
4. Disable the chart Ingress; add `templates/ingressroute.yaml` with the secure router and its
   redirect, TLS by `myresolver`, `redirect-https` and `security` middlewares, plus the PostSync
   hook that restarts the worker after a sync.
5. Add the blueprint plumbing: one ConfigMap from `files/`, the mount in values, and a first
   blueprint creating the `users` group.
6. Render, commit, PR to `develop`; after sync, confirm migrations, the login page, the admin
   login, and that a pod resolves the issuer name.
7. Update `docs/secrets.md` (new keys), `docs/status.md` (authentik leaves the parked list),
   `renovate.json` (group chart and image), and the chart's README if it carries one.

Acceptance: server and worker Running with no restart loop, migration job complete, a valid
certificate, admin login works, `users` group exists, no blueprint errors in the worker log, and
no plain Ingress left in the namespace.

### Phase 2 — Identity sources

Outcome: a person can sign in with Discord and with Plex; new identities land in `users`; the
admin account can still use its local password.

Work:

1. Operator creates the Discord application (client ID, client secret) and registers **both**
   callback addresses, `https://authentik.kub.home.ashudev.com/source/oauth/callback/discord/`
   and the same path on the public name, because authentik builds the callback from the host the
   login started on. Operator supplies the homelab guild ID. Values go into `authentik-sso`.
2. Operator obtains a Plex token and stores it in `authentik-sso`.
3. Blueprints: the Discord source with `guilds guilds.members.read` scopes; a source property
   mapping that records guild membership; the expression policy that reads it; the Plex source
   restricted to the server and its friends. Both sources attach to the default authentication
   and enrollment flows, and the membership policy is bound to both.
4. Test both sign-ins in a private window; confirm the created users join `users`; grant
   `authentik Admins` to the operator's own identity; confirm a Discord account outside the
   server is refused at enrollment and at a later login.
5. Document the two provider consoles, the guild-membership rule and the recovery path in
   `docs/runbooks/`.

Acceptance: both sources appear on the login page, both complete a first login for a listed
person, an unlisted Discord account is refused, duplicate identities are not created on second
login, local admin login still works.

### Phase 3 — Applications, one at a time

Order is by risk and value, and can change as each app is examined:

1. ArgoCD — the admin path itself; verifies the whole chain. Stays first; if its plain-HTTP
   callback is refused it moves to a TLS hostname rather than dropping out.
2. Grafana.
3. Wiki.js.
4. Ghostfolio.
5. pgAdmin.
6. Discourse (official OpenID Connect plugin, no custom image).
7. Kavita.
8. Firefly III.
9. Remaining candidates after capability checks: Autobrr, Postiz.
10. Deferred: Guacamole — its SSO support is a jar that must be baked into or downloaded by the
    image, and this repo has no image build path. Revisit if that changes.
    Removed: n8n — OIDC is an enterprise feature in community builds.

Per application, the same five steps:

1. Confirm the application's native OIDC/SAML support and the exact redirect address.
2. Add one blueprint with the provider and application, client secret key from `authentik-sso`.
3. Configure the application: chart or environment where a knob exists; otherwise one recorded
   setup pass through the app's own admin interface, keeping its local admin login.
4. Test in a private window and, where the app has one, with a non-admin account.
5. Record the app in the inventory and, only once it works, consider disabling self-service
   signup.

Rollback for one app: remove its blueprint (or set its binding to inactive) and restore the
previous application settings; other apps are untouched because each lives in its own file.

## 8. Application inventory

Capability is the application's own native support, verified per app before wiring. "No" here
means the app keeps its local password, by decision.

| Application | Where it lives | Native SSO | Note |
|---|---|---|---|
| ArgoCD | `argocd.kub.local` | OIDC | Plain HTTP; check whether a plain-HTTP redirect is accepted |
| Grafana | `monitoring` | OIDC | |
| Wiki.js | `wiki` | OIDC | |
| Ghostfolio | `ghostfolio` | OIDC | |
| pgAdmin | `databases` | OIDC | |
| Discourse | `forum.ashuvidz.com` | OIDC | Needs the official OpenID Connect plugin |
| Kavita | `media-stack` | OIDC | |
| Firefly III | `firefly` | OIDC | Verify the current image's support |
| Guacamole | `guacamole` | OIDC/SAML | Deferred: needs an SSO jar in the image and there is no build path |
| Autobrr | `media-stack` | OIDC | Verify |
| Postiz | `postiz` | OIDC | Verify |
| n8n | `n8n` | OIDC | Out of scope: enterprise-only in community builds |
| Sonarr, Radarr, Lidarr, Prowlarr, Readarr, Bazarr, Maintainerr | `media-stack` | No | |
| qBittorrent, autobrr's clients, Tautulli, Plex | `media-stack` | No | |
| Filebrowser | `media-stack` | No | Proxy-header auth only |
| Syncthing, FreshRSS, TheLounge, Koillection, LinkStack, Dashy, Calibre-Web | various | No | |
| Prometheus, Loki | `monitoring` | No | No login of their own |
| Monitorss | `monitorss` | Discord itself | Already authenticated by Discord |
| Minecraft | `minecraft` | No | Game protocol |
| authentik | `authentik` | — | The login service itself |

## 9. Verification

Everything below is checked on the real tree before a completion claim:

- `helm dependency build` then `helm template` for the chart, with `--namespace authentik`.
- The rendered output contains no `kind: Ingress`, one `IngressRoute` plus its redirect, the
  blueprint ConfigMap mounted into the worker, and the PostSync hook with its Role.
- The ArgoCD reference check from `AGENTS.md` §6 still passes.
- After sync: pods `Running` with no restarts, migration complete, `/-/health/ready/` returns 200,
  the hostname serves a valid certificate and a login page, worker log has no blueprint error.
- Phase 2 and 3 add their own login tests, always in a private window.

## 10. Risks

- **Big version jump.** 2025.12 to 2026.8 crosses three releases, but the database is empty, so
  there is no data to migrate and no old schema to repair.
- **Public login page.** The hostname is reachable from the internet through the wildcard.
  authentik has built-in login throttling; Traefik has no rate limiter here. Who may create an
  account is set by 5.4.
- **One hostname.** Every provider names `authentik.kub.home.ashudev.com`, so one mistake there
  breaks every app at once, and the wildcard DNS becomes a single dependency. The phase 1
  pod-resolution check and the ArgoCD test catch name problems early.
- **Plain-HTTP internal app.** An OIDC client on `*.kub.local` may be refused because the
  redirect is not HTTPS. Those apps are examined individually; if refused, they stay out or move
  to a TLS hostname.
- **Blueprint failure is atomic.** One bad file fails only that file, but leaves its app
  unwired; the worker log names the file.
- **Discord dependency.** The guild check calls Discord's API at each Discord sign-in. If Discord
  is unreachable, that sign-in fails; Plex sign-in and the local break-glass admin do not.
- **Plex dependency and a retired endpoint.** Plex admission calls `plex.tv` to list the servers a
  token can see. The friend list is avoided because Plex retired it and authentik still calls it;
  see 5.4. If `plex.tv` is unreachable, Plex sign-in fails.
- **Removed member.** The membership policy is bound to authentication as well as enrollment, so
  removal from the server takes effect at the next login rather than leaving the old account
  usable.
- **No Redis in 2026.8.** The old Redis StatefulSet is orphaned by the upgrade and is deleted by
  hand because pruning is not guaranteed; nothing reads it afterwards.
- **DNS.** The public record is an operator action and gates phase 1 acceptance for the public
  name only.

## 11. Left to the builder

- Exact blueprint fields and model names for 2026.8.
- How each application stores its OIDC settings (environment, config file, database) and the
  exact variable names.
- The order of applications within phase 3 after capability checks.
- Whether an application's local signup is disabled immediately or after a trial period.

## 12. Open questions

None blocking phase 1. Per-application access policy (who may open which app) is decided when
that app is wired, against the single `users` group established here.
