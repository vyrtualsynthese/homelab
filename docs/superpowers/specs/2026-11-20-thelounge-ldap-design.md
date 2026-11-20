# The Lounge: one sign-in through authentik's LDAP directory

> **In one line.** Replace the forward-auth gate on The Lounge with an authentik LDAP provider and a
> small LDAP outpost, so a person signs in to the app with their own authentik username and password
> instead of passing a shared gate and then typing a second, local password.
>
> **Use it when** you are building task #340, or an application has no OIDC and you are weighing the
> gate against LDAP · **Verified** 2026-11-20 · **Kind:** design

## What exists today

The Lounge answers at `https://thelounge.kub.home.ashudev.com` behind two middlewares:
`kube-system-lan-only` and `kube-system-authentik-forwardauth`. The gate is a proxy provider named
`thelounge`, attached to the application `thelounge` and bound to `authentik Admins`, in
`cluster/helm/authentik/files/app-thelounge.yaml`. The chart's own login still asks for a local
account, so a person signs in twice: once to the gate, once to The Lounge. The gate proves the
browser passed authentik but carries no identity into the app — every browser arrives as the same
person, which is exactly the problem this task removes.

The cluster runs no LDAP outpost. The embedded outpost serves proxy providers only, and it cannot
serve an LDAP provider.

## What one sign-in can and cannot mean here

- **The Lounge has no OIDC or SAML.** LDAP is its only built-in identity mechanism. Its documentation
  and its `config.js` both describe the LDAP path; there is no other one.
- **Its LDAP plugin replaces local login.** `server/plugins/auth.ts` takes the first enabled plugin in
  the list, and `ldap` comes before `local`; `server/plugins/auth/ldap.ts` then validates every login
  with an LDAP bind. Accounts, their saved networks and their history are keyed by username and
  survive, but **local passwords stop working**. The card's "the existing accounts keep working" is
  true for the accounts and false for their passwords.
- **It is one credential, not one session.** The gate disappears, so there is no double prompt, but
  The Lounge still shows its own form and the person types their authentik username and password
  there. That is what LDAP buys; the only way to remove the form entirely would be OIDC, which the app
  does not have.
- **LDAP binds need a password in authentik.** A person who only ever signed in through Discord or
  Plex has none until one is set. With one human account today this is a note, not a migration.
- **MFA is optional in this cluster and no account has a device enrolled** (checked 2026-11-20).
  authentik's `default-authentication-flow` includes an authenticator-validation stage that skips when
  no device is enrolled, so a password bind works today. `mfa_support` stays **off**: with nothing
  enrolled it changes nothing, and on, it would read a `;` in a password as the start of a code and
  reject the bind. The flip belongs with the first enrolment, not with this change.

## Goals

1. The route no longer carries `kube-system-authentik-forwardauth`; `lan-only` and `security` stay.
2. A person opens The Lounge, types their authentik username and password, and is in.
3. The app knows it is that person: the account it loads is keyed by their authentik username, so
   their saved networks and history follow them.
4. No secret is committed to git.

## Non-goals

- Changing who may use The Lounge. The authentik side stays `authentik Admins`, as the gate is today.
  Opening it to `homelab-users` is a second binding and a separate decision.
- Migrating accounts or history from anywhere. The claim was rebuilt empty on 2026-11-19.
- Adding OIDC to The Lounge. It does not support it.
- Removing `kube-system-security` from the route.

## Anti-goals

- A shared bind account used for every person. That is the gate again, with a form in front of it.
- Storing the LDAP bind password in `config.js` or in the ConfigMap.
- Running the outpost through authentik's Kubernetes integration. It creates cluster-scoped RBAC and
  puts workloads outside git; this repo deploys its own outposts.

## Approaches considered

### A. Direct bind — chosen

The Lounge binds to the outpost as `cn=<username>,ou=users,dc=ldap,dc=goauthentik,dc=io`; authentik
runs the authentication flow and validates the password. `config.js` sets `baseDN` and
`primaryKey: "cn"` and does **not** set `searchDN`.

- One provider, one outpost, one application, one Secret (the outpost's API token).
- Access is decided where it already is decided: the application's policy binding. authentik refuses
  a bind from someone who cannot open the application, so an empty group cannot accidentally admit
  anyone.
- No service account, no bind password, no `config.js` Secret, no directory-search permission.
- Cost: no group filter inside `config.js`, and no pre-check that a local account still exists in
  authentik. Both are covered by the application binding, which is the stronger place for the
  decision.

### B. Service-account search — the card's recipe

`config.js` uses `searchDN`: a `thelounge-ldap` service account binds first, searches for the person,
then the person's own DN is bound with their password. This is authentik's documented recipe.

- Adds a service account with a password in a Secret, a role holding *Search full LDAP directory*, an
  object permission on the provider, and a user binding so the service account may open the
  application.
- The group filter the card asks for lives in `config.js` and duplicates what the application binding
  already says.
- Two credentials to rotate instead of one, and one more moving part whose failure is a login failure.

Rejected: more objects, the same access decision in two places, and the card's own step 3 (move
`config.js` into a Secret) exists only to hide the bind password — a password approach A does not
have.

### Not taken further

- **OIDC**: not supported by The Lounge.
- **Proxy auth**: authentik's proxy provider can pass identity, but only to apps that trust its
  headers. The Lounge does not read `X-authentik-*` and has no setting for it.
- **No gate at all**: The Lounge's local login behind `lan-only` is the status quo the task exists to
  improve.

## The design

### 1. authentik objects — one blueprint file

`cluster/helm/authentik/files/app-thelounge.yaml` is rewritten to hold, in this order:

1. the `ashulab-groups` meta-apply, as today;
2. an **LDAP provider** named `thelounge-ldap`;
3. an **application** with the new slug `thelounge-ldap`, display name "The Lounge", the same launch
   URL and icon, bound to `authentik Admins` — the same access as the gate;
4. the **LDAP outpost** object, created after the provider so the `!Find` can resolve in one pass.

```yaml
  - model: authentik_providers_ldap.ldapprovider
    id: thelounge-ldap-provider
    identifiers:
      name: thelounge-ldap
    attrs:
      name: thelounge-ldap
      # The login flow, not the implicit-consent flow the proxy providers use:
      # an LDAP bind is an authentication, and this flow carries the password
      # stage that validates it.  Its MFA stage skips when no device is
      # enrolled (none is, as of 2026-11-20).
      authorization_flow: !Find [authentik_flows.flow, [slug, default-authentication-flow]]
      invalidation_flow: !Find [authentik_flows.flow, [slug, default-provider-invalidation-flow]]
      base_dn: dc=ldap,dc=goauthentik,dc=io
      # One human, occasional logins: always consult the database, so a changed
      # password or a revoked account takes effect on the next bind.
      bind_mode: direct
      search_mode: direct
      # Off on purpose: no device is enrolled, so the flow's MFA stage skips
      # anyway, and on it would split a password at a `;`. Turn it on with the
      # first enrolment, and tell that person to type password;code.
      mfa_support: false
```

```yaml
  - model: authentik_outposts.outpost
    identifiers:
      name: ashulab-ldap
    attrs:
      name: ashulab-ldap
      type: ldap
      providers:
        - !Find [authentik_providers_ldap.ldapprovider, [name, thelounge-ldap]]
      config:
        refresh_interval: minutes=5
        log_level: info
```

**Why a new application instead of switching the existing one.** The existing `thelounge`
application points at the proxy provider and the route's gate depends on it. Changing that provider
in the same change that starts using LDAP would leave a window — between the two Applications syncing
— where the route still has the gate but the gate has no provider, or the app has no application
admitting the bind. Both Applications sync automatically and independently, so the order cannot be
guaranteed from git. Two applications remove the window: the old one keeps the gate alive until the
route stops asking for it, then both it and its provider are deleted by hand.

The final slug is therefore `thelounge-ldap`. The slug appears in URLs the person never opens; the
display name, launcher icon and host are unchanged. Reusing `thelounge` would mean deleting and
recreating the application, which buys nothing and loses the binding.

**The outpost is created by the blueprint, not by authentik's integration.** A blueprint-created
outpost gets its service account and token from the same model signal the UI uses; the token is what
the outpost pod authenticates with. The outpost must **not** be added to `outpost-embedded.yaml`:
that file's `providers` list is for the embedded proxy outpost, and putting an LDAP provider there
does not attach it to anything.

### 2. The outpost pod — `cluster/helm/authentik/templates/ldap-outpost.yaml`

A Deployment and a Service in the `authentik` namespace, rendered by the wrapper chart so the outpost
image follows the authentik version (`.Values.authentik.global.image.tag`, `2026.8.5`).

- Image `ghcr.io/goauthentik/ldap:2026.8.5`, pinned the same way the server is.
- `AUTHENTIK_HOST=http://authentik-server.authentik.svc.cluster.local` — in-cluster HTTP on the
  server Service (port 80 → 9000). No TLS, no hairpin NAT through Traefik, no certificate to trust.
- `AUTHENTIK_TOKEN` from Secret `authentik-ldap`, key `AUTHENTIK_TOKEN`.
- One container port, `3389` (plain LDAP). The TLS listener (`6636`) is not exposed: no certificate
  is configured on the provider, and a Service port with nothing behind it is a false promise.
- Service `authentik-ldap`, ClusterIP, port 3389 named `ldap`, selecting the pod.
- `nodeSelector: kubernetes.io/hostname: nuc0`, the same node as authentik.
- Requests 10m CPU / 64Mi, limit 128Mi; pod seccomp `RuntimeDefault`, container
  `allowPrivilegeEscalation: false` and all capabilities dropped.
- TCP liveness probe on 3389, initial delay 10s: the listener opens after the outpost has fetched
  its configuration, which is the only health signal it offers.

Chart version `0.10.5 → 0.11.0`.

### 3. The token — the one Secret

Secret `authentik-ldap` in namespace `authentik`, key `AUTHENTIK_TOKEN`. It is created by the
operator after the outpost exists, because the value is generated by authentik:

1. `GET /api/v3/outposts/instances/?name=ashulab-ldap` → `token_identifier`.
2. `GET /api/v3/core/tokens/<token_identifier>/view_key/` → `key`.
3. `kubectl -n authentik create secret generic authentik-ldap --from-literal=AUTHENTIK_TOKEN="<key>"`
   (the runbook carries the one-liner that does 1–3 without printing the value).

Until the Secret exists the outpost pod sits in `CreateContainerConfigError`. That is the documented
behaviour of a missing Secret in this repo, not a sync failure.

### 4. The Lounge — `cluster/helm/thelounge`

`config.js` gets the LDAP block, replacing the commented example:

```js
ldap: {
    enable: true,
    // Plain LDAP inside the cluster: the outpost and the app are pods, the
    // Secret above is the only credential, and no request leaves the node.
    url: "ldap://authentik-ldap.authentik.svc.cluster.local:3389",
    tlsOptions: {},
    // authentik exposes users at cn=<username>,ou=users,<base DN>.
    primaryKey: "cn",
    baseDN: "ou=users,dc=ldap,dc=goauthentik,dc=io",
},
```

No `searchDN`, no password in the file. `config.js` stays a ConfigMap; the card's step 3 is not
needed.

`values.yaml`:

- the middleware list drops `authentik-forwardauth`, keeps `lan-only` and `security`, and gains the
  reason next to it (the house rule for a route that keeps `lan-only` alone);
- `extraEnv`'s comment loses "it has no LDAP ... of its own" — the app now speaks LDAP, sourced from
  the network, not from a Secret;
- chart version `0.2.1 → 0.3.0`.

`config.js` is already hashed into `checksum/config`, so the change rolls the pod by itself.

### 5. Documents

- `docs/reference/sso-applications.md`: the matrix row becomes LDAP; the LDAP path joins the
  mechanisms worth explaining, with the traps that cost time (bind flow, MFA, `cn=` DN).
- `docs/reference/routes.md`: the protection column becomes `lan-only` plus the app's own LDAP login.
- `docs/reference/chart-notes.md`: the The Lounge paragraph is rewritten; the LDAP debt line goes.
- `docs/secrets.md`: new row for `authentik-ldap`.
- `docs/runbooks/authentik-apps.md`: a section for adding an LDAP outpost, with the token one-liner.
- `cluster/helm/thelounge/README.md`: the gate section becomes the sign-in section.

## Before anything is built: two facts only the operator holds — answered 2026-11-20

The bind validates against authentik's password table, and The Lounge keys everything by username.
Neither is readable from here, and a wrong assumption in either would break the verification step
rather than the build.

1. **The Lounge account's username is `vyrtualsynthese`, the same as the authentik username.** No
   migration step, no rename, no abandoned account; the first LDAP login lands on the existing account
   with its networks.
2. **The authentik account has a local password and can sign in with it.** No pre-step in authentik.

A third, answered by the same conversation: the operator's is the only Lounge account that matters.
Every local password stops working, and each account needs an authentik identity with the same name.

## Rollout: three pull requests, with the operator at each gate

Both Applications sync `develop` automatically and independently, so a git order between them is not
a deployment order. The gates are what make the order real.

**PR 1 — the authentik side.** Blueprint rewrite, outpost Deployment and Service, chart bump,
`docs/secrets.md` and the runbook section. The old application and proxy provider are untouched, so
the gate keeps working. On merge ArgoCD syncs authentik, the worker restarts and applies the
blueprint, and the outpost pod starts and waits for its Secret.

*Gate:* the operator creates Secret `authentik-ldap` from the token, using the runbook one-liner. The
outpost must then report healthy (`GET /api/v3/outposts/instances/?name=ashulab-ldap`, then its
`health/` endpoint; the pod log shows the LDAP listener). Nothing proceeds until it does.

**PR 2 — the The Lounge side.** `config.js`, the middleware list, the chart bump, and the reference
docs and chart README. On merge the pod rolls, the gate is gone, and the form checks against
authentik.

*Gate:* the operator signs in with the authentik username and password in a browser and confirms the
account is the right one, with its networks. This is the only end-to-end proof.

**PR 3 — retire the gate.** Drop the `thelounge` proxy provider line from `outpost-embedded.yaml`,
then delete the application `thelounge` and the provider `thelounge` through the authentik API, in
that order: the application holds the provider's foreign key, and the outpost list must have applied
first because a `!Find` on a missing provider rolls the whole file back. Blueprints do not prune, so
those two deletions are by hand and belong in the runbook. If the browser check in PR 2 fails, this PR
does not open and the task stays in `Doing` with the blocker named in a comment.

## Verification

Runnable here, with no cluster write:

- `helm template` on `cluster/helm/authentik` (after `helm dependency build`) and on
  `cluster/helm/thelounge`.
- `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests`.
- `hack/check-exposure.sh` — the route must still be refused to the internet and still carry
  `lan-only`.
- authentik API: provider exists with the intended flows and base DN; outpost exists, is attached to
  the provider, and reports healthy; the token is readable.
- MCP: the outpost pod is `Running` and its log shows the LDAP listener on 3389 and a successful
  configuration refresh; the Lounge pod is `Running` with the new `checksum/config`.

Needs the operator:

- creating Secret `authentik-ldap` from the token;
- the browser login, which is the only proof that bind → flow → app access are all correct.

## Rollback

Before the Secret, nothing has changed for a user. After PR 2, putting `authentik-forwardauth` back in
the middleware list restores the gate; the old application and provider are still there until PR 3.
After PR 3, rollback means re-running the old blueprint entries (the provider and app are recreated)
and re-adding the middleware — slower, which is why PR 3 waits for a verified login.

## Limits recorded

- Local Lounge passwords stop working; the authentik password replaces them.
- A person with no authentik password (Discord/Plex-only identity) cannot sign in until one is set.
- If MFA is enrolled later, `mfa_support` flips on and the code is appended to the password
  (`password;123456`); a password containing a semicolon would then be split. Written down next to the
  provider, and the flip is not part of this change.
- Plain LDAP on the pod network: the password is not encrypted on the wire, but both ends are pods on
  `nuc0` and the Service is ClusterIP. LDAPS would need a certificate on the provider and the internal
  CA in the app's trust store — debt, not this change.
- Admins only, as the gate is today.

## Calls I made

- **No service account** (approach A): access lives in the application binding.
- **A new application slug** `thelounge-ldap`, so the gate stays up until the route stops using it.
- **`mfa_support: false`**, because nothing is enrolled and the flag only adds a way for a password
  containing `;` to be split; it flips with the first enrolment.
- **Direct bind and search modes**, so a password change or a revoked account takes effect at once.
- **Plain LDAP in-cluster**, not LDAPS, with the reason above.
- **The outpost in the authentik wrapper chart**, so its image moves with the server version.

## What is left to the builder

The exact template, the resource figures within the stated bounds, the wording of the docs, and the
order of the YAML inside each blueprint file.
