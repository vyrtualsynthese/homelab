# SSO access model — design

Status: approved and implemented, 2026-11-14.
Date: 2026-11-14.
Supersedes the access model in §5.4 and §6 of
[`2026-11-14-authentik-sso-design.md`](2026-11-14-authentik-sso-design.md). That document's
instance repair, sources and per-application wiring still stand; this one decides *who* gets
*what*, and adds optional MFA and one capability-gated application.

Vocabulary used below:

- **Group**: a list of accounts in authentik. Applications bind to groups: being in the group is
  what lets a person open the application.
- **Binding**: the link between an application and a group. No binding for a person's group means
  the application does not appear in their list and the app refuses their authorisation.
- **Provider**: the authentik object describing one application's trust (client ID, secret,
  redirect address).
- **Forward auth**: authentik's other way of protecting an application — a small proxy check in
  front of the app that hands it the signed-in username. Used only for FileBrowser here.
- **MFA**: a second proof of identity at login — a code from an authenticator app (TOTP) or a
  hardware key (WebAuthn, which is what a YubiKey speaks).
- **Capability group**: a group that grants something extra on top of the tier, for example
  FileBrowser folder access.

## 1. What this is for

One login, three levels of access, and a way to grant a single person one extra thing without
inventing a fourth level.

The operator is always an administrator of every application. A curated set of people can use
most applications as ordinary users, never as administrators. Everyone else who can prove an
identity has an account and no access at all. Access is granted by moving a person up a level or
into a capability group, and removed the same way.

## 2. What exists today

Evidence as of 2026-11-14, from the repo and the previous design.

- Two groups carry all the meaning: `users` means "may sign in", `apps` means "may open an
  application". Four applications have providers in the blueprints (ArgoCD, Grafana, pgAdmin,
  Kavita), but only **Grafana and pgAdmin are actually configured to trust authentik**. Argo CD has
  no Dex connector, and Kavita has no OIDC settings in its database, so neither signs anyone in
  today. FileBrowser is not wired at all.
- Enrollment writes new identities into `users`. Discord guild membership and Plex server access
  are the admission gate.
- Grafana already maps `authentik Admins` to its Admin role and everyone else to Viewer.
- Every application is bound to one expression policy, `app-access` (commit `cab70fbb`), which
  admits members of `apps`, members of `authentik Admins`, and superusers. It exists because a
  single binding holds one subject and the applications run in `all` mode; the policy comment says
  two group bindings would require both groups, which is only true in that mode.
- ArgoCD has no SSO wiring at all: `argocd-cm` carries no Dex connector and `argocd-rbac-cm`'s
  `policy.csv` is empty. Both ConfigMaps carry Helm release labels, so a hand edit is lost on the
  next chart upgrade. ArgoCD itself is installed by hand from `cluster/helm/argocd/values.yaml`;
  no Application in this repo manages it.
- No MFA is configured: the default validation stage is bound to the password flow with its
  "not configured" action left at *skip*, and it is not bound to the Discord/Plex source flows.
- No outpost runs beyond the embedded one. (Superseded: the nine media tools now use the
  forward-auth gate, and FileBrowser was already fronted by it. The current picture lives in
  `2026-11-14-media-tools-authentik-design.md` and `docs/runbooks/authentik-apps.md`.)
- (Historical) FileBrowser is fronted
  by a Traefik middleware named `authentik` that only sets CORS headers and enforces nothing,
  on a hostname that the public wildcard DNS exposes.
- FileBrowser is pinned at `v2.63.2`. Upstream archived the project on 2026-09-01; the last
  release line is 2.63.x, and `v2.63.19` is the first that does not give an auto-created
  proxy-auth user the whole server root as its scope.

## 3. Goals, non-goals, anti-goals

Goals:

- Three named levels with an unmissable meaning: admins, users, guests.
- Every new identity lands in guests and reaches nothing until promoted.
- Promotion and demotion are one group change, not a per-application edit.
- One person can be granted extra access (FileBrowser folders) without joining the admin level.
- MFA can be added by anyone who wants it, and is used when present.
- The model is visible in git: the groups, the enrollment default and each application's
  bindings are blueprints.

Non-goals:

- MFA is not forced on anyone.
- No change to which applications are wireable: an application with no native OIDC stays on local
  accounts and is outside this model.
- No per-folder, per-file policy in authentik. Folder scope lives in FileBrowser.
- No invitation system, no self-service access requests.
- ~~No proxy gate in front of applications that would break their phone apps or APIs.~~ **Overridden
  on 2026-11-15 by the operator, knowingly.** The media tools are behind the gate; the ones whose
  APIs matter (bazarr, bazarr-anime) exempt `/api` on a `lan-only` rule, and autobrr uses native
  OIDC instead of a gate. See `2026-11-14-media-tools-authentik-design.md`.

Anti-goals (failures even if the system works):

- A guest or a user reaching an application administrator role.
- Access granted by an account attribute that nobody can review in git.
- A FileBrowser deployment where the username header can be forged from the network.
- Losing the ability to administer authentik or an application if Discord, Plex or authentik's own
  MFA is unavailable.

## 4. Constraints

- GitOps: blueprints in the chart, changes reach the cluster only through `develop`.
- Group *membership* is not in git and is not meant to be. It is the access decision, and it is
  made in the UI.
- Secrets stay out of git; client secrets live in `authentik-sso`.
- Traefik v2 CRDs for routing; the house middleware set (`redirect-https`, `security`).
- Workloads pinned to a named node.
- FileBrowser accepts exactly one authentication method at a time. Choosing proxy auth removes
  its local login form.
- ArgoCD is not managed by this repo: `argocd-cm` and `argocd-rbac-cm` are edited by hand.

## 5. Decisions

### 5.1 Three levels, and the admin level is authentik's own

| Option | Trade-off |
|---|---|
| Reuse `authentik Admins` as the admin level | One membership means "admin everywhere", which is exactly the requirement. Grafana already reads it. Any future authentik administrator is also an application administrator — acceptable for a one-person homelab. **Chosen.** |
| A separate `homelab-admins` group | Separates "administers authentik" from "administers applications"; needs two memberships to maintain and changes Grafana's mapping, for a distinction nobody currently needs. Rejected. |

The three levels:

| Level | Group | Meaning |
|---|---|---|
| Admin | `authentik Admins` (built-in) | Administers authentik. Where an application can read a group, it is that application's administrator. |
| User | `homelab-users` | May open the applications marked user-safe, always in the plain user role. |
| Guest | `homelab-guests` | Default landing for every new identity. May sign in and sees an empty application list. |

`apps` and `users` are retired by this design. The names are too close to the levels to keep.

Enrollment (`default-source-enrollment-write`) writes into `homelab-guests`. Promotion to
`homelab-users` is a deliberate act by the operator in the UI; demotion is removing them from
it. Discord/Plex membership therefore grants only the right to create an account — it no longer
grants application access.

### 5.2 The access matrix lives in each application's blueprint

Every application blueprint carries one binding per level that may open it, and the application
runs in `any` policy mode: passing any one binding admits. Guests get no binding, so their list is
empty and a bookmarked app URL is refused by authentik.

Alternatives considered:

| Option | Trade-off |
|---|---|
| Per-application group bindings, `any` mode | The matrix is visible in each application's own blueprint, and the three levels can differ per app (Kavita open to users, ArgoCD and Grafana not). Needs `policy_engine_mode: any` on multi-level apps. **Chosen.** |
| One expression policy for every app (`app-access`, as on `develop` today) | One rule in one place, but it can only express "apps or admins for everything": per-app differences become a Python table of application slugs, and a mistake there is invisible in the blueprint it affects. Retired by this design. |
| One policy per level, bound side by side | Same as the chosen option with extra moving parts: a policy that only checks one group is exactly a group binding. Rejected. |
| A nested group hierarchy | authentik does not nest groups, and a sync job to fake it would be the only moving part in the model. Rejected. |

The chosen option also removes a live trap: in `all` mode, two bindings require both groups, so a
user added to the user level without the admin level would be refused everywhere.

Current applications, by level. "Wired" means the application itself has been configured to trust
authentik; the others have authentik-side objects in git but no configuration in the application,
so they cannot sign anyone in yet.

| Application | Admin | User | Guest | User-level role | Wired |
|---|---|---|---|---|---|
| ArgoCD | yes | no | no | — | no: no Dex connector yet |
| Grafana | yes | no | no | — | yes |
| pgAdmin | yes | no | no | — | yes |
| Kavita | yes | yes | no | normal account | no: no OIDC settings yet |
| Wiki.js | yes | yes | no | editor group rule | no |
| Discourse | yes | yes | no | ordinary member | no |
| Ghostfolio | yes | yes | no | ordinary account | no |
| Firefly III | yes | yes | no | ordinary account | no |
| Postiz | yes | yes | no | ordinary account | no |
| FileBrowser | yes | capability | no | see 5.4 | no: cutover pending |
| No native SSO (Plex, *arr, qBittorrent, Tautulli, Syncthing, Dashy, Calibre-Web, …) | local | local | local | out of the model | — |

Consequence of that column: **the user level has no application to open today.** The only two
applications actually wired, Grafana and pgAdmin, are admin-only by design. `homelab-users`
therefore grants nothing until Kavita, Wiki.js or another user-safe application is wired. That is
correct behaviour, not a gap in the model, but it means the user level can only be proven once
one such application is wired; until then its test is "signed in, refused everywhere".

Infrastructure applications stay admin-only. Users are community and household members; a
metrics dashboard, a database console and the deployment control plane are not user surfaces.
The LAN-only hostnames (`*.kub.local`) are convenience, not a boundary: the wildcard DNS is
public, so "internal" is not a reason to grant access.

Prometheus is deliberately absent: it has no login of its own, and its route is unauthenticated,
so it is not a level-gated application at all. It is queried by Grafana server-side, which carries
no user session; putting a gate in front of it would empty every dashboard. Its unauthenticated
route is a separate finding, not part of this model.

### 5.3 Administrator roles are mapped where the application allows it

| Application | How admin is decided | Consequence |
|---|---|---|
| Grafana | `role_attribute_path` reads `authentik Admins` → Admin, else Viewer | automatic |
| ArgoCD | Dex connector plus `policy.csv` in `cluster/helm/argocd/values.yaml`, applied with `helm upgrade` | GitOps-adjacent: the release is run by hand, but the settings live in the repo and survive chart upgrades |
| pgAdmin | first OIDC login is promoted to Administrator inside pgAdmin | one manual promotion, recorded |
| Kavita | admin flag per account inside Kavita | one manual promotion, recorded; not wired yet |
| Discourse | admin flag inside Discourse | one manual promotion, recorded |
| Wiki.js | group rules map an authentik group to wiki permissions | automatic once the group rule exists |
| Ghostfolio, Firefly III, Postiz | no administrator distinction to map | nothing to do |

"Administrator everywhere" is therefore exact for Grafana and ArgoCD, a recorded one-time step for
pgAdmin, Kavita and Discourse, and meaningless for applications that have no administrator role.
This is a property of those applications, not a gap in the model.

### 5.4 Capability groups, with FileBrowser as the first one

A capability group grants an application or an extra scope inside one, additively. The first is
`filebrowser-editors`: it admits its members to FileBrowser; the folder list is set per account in
FileBrowser itself.

The operator's decision: keep FileBrowser and set each person's folders by hand, selecting the
username in its admin interface. authentik can only say who a person is; it cannot describe
folder scope. Alternatives considered and rejected: FileBrowser Quantum (maintained fork, folder
rules by group — more migration than wanted now), and a network share instead of a web file
manager (different workflow for the editor).

Because FileBrowser has no OIDC, its login is its own proxy-header method:

- An authentik **proxy provider** plus the embedded outpost; FileBrowser's Traefik router gains a
  forward-auth middleware pointing at the outpost.
- The outpost sets `X-authentik-username`; FileBrowser runs with `auth.method=proxy` and
  `auth.header=X-authentik-username`.
- Any client-supplied value for that header is removed at the router before forward auth runs.
  FileBrowser trusts the header blindly; this is the one thing that must not be skipped.
- The FileBrowser application binds to `authentik Admins` and `filebrowser-editors` only. Nobody
  else reaches it, so nobody else is auto-created inside it.
- The image moves to the last 2.63.x release, at least `v2.63.19`, for the scope fix. The runner
  stays disabled.
- Ordering matters: the account and its folder scope are created in FileBrowser first, then the
  person joins `filebrowser-editors`. The reverse order gives a first login on the default scope,
  which is wider than intended.

Known costs, accepted: FileBrowser is archived and unmaintained, so it is treated as a legacy
component and stays behind authentik; and because it supports one auth method, there is no local
FileBrowser login — if authentik is down, FileBrowser is unavailable.

### 5.5 MFA is optional and actually applies

Enrolment is open to everyone, from their authentik profile: TOTP (authenticator app) and
WebAuthn (YubiKey as a security key). Two YubiKeys is two WebAuthn devices on the same account;
authentik prefers the last used and lets the person pick when both are present.

Validation keeps authentik's default behaviour — ask when the person has an enrolled device,
skip when they do not — so nothing is forced. The part that is easy to miss: the MFA stage is
bound to the password flow by default, and the people here sign in with Discord or Plex, whose
source flows do not run it. The existing `default-authentication-mfa-validation` stage is
therefore also bound to `default-source-authentication`, with the same skip-if-none setting.
Without that, someone enrols a YubiKey and is never asked for it on the login they actually use.

Device classes are TOTP and WebAuthn. SMS and email one-time codes are not enabled: neither is a
second factor for someone who already controls the mailbox or phone number behind the identity.

Recovery: an admin deletes the lost device in the authentik UI and the person enrols again. The
local `akadmin` account stays unenrolled on purpose as break-glass for authentik itself; the
management command that generates a recovery link is recorded in the runbook for the case where
an administrator loses every device.

### 5.6 Migration of the two existing groups

Groups are identified by name. Renaming one in the blueprint creates a new empty group, so the
move is two commits with a manual membership step in between:

1. Commit: create `homelab-users` and `homelab-guests`; move every application binding from
   `apps` to `homelab-users` plus the admin binding; point enrollment at `homelab-guests`.
   ArgoCD RBAC is updated by hand in the same window.
2. Manual: add the current `apps` members to `homelab-users` and every other identity to
   `homelab-guests`.
3. Commit: delete the `apps` and `users` group entries from the blueprint. Old groups are removed
   in the UI afterwards.

Until step 2, people hold the old groups and the new bindings do not admit them — a short window,
announced, not done during a release.

## 6. Architecture

```
                       ┌──────────────────────────────┐
 identities ──────────►│ groups                       │
 Discord, Plex         │  authentik Admins            │
 local break-glass     │  homelab-users               │
                       │  homelab-guests (default)    │
                       │  filebrowser-editors         │
                       └──────────────┬───────────────┘
                                      │ bindings (in blueprints)
                     ┌────────────────┼─────────────────┐
                     ▼                ▼                 ▼
              OIDC providers     OIDC provider     proxy provider
              (Kavita, Wiki,     (Grafana,         (FileBrowser)
               Discourse, …)      ArgoCD, pgAdmin)
                     │                │                 │
                     ▼                ▼                 ▼
              native OIDC      native OIDC     Traefik forward auth
              in the app       in the app      → X-authentik-username
                                                   → FileBrowser scope
```

- Application list: authentik shows only the applications a person's groups are bound to. A guest's
  list is empty without any extra work.
- Roles: applications that read groups map the admin level themselves; the rest keep a local
  role.
- Capability: ~~FileBrowser is the only forward-auth consumer.~~ Nine media tools and Calibre-Web
  use the forward-auth gate; autobrr, Argo CD, Grafana, pgAdmin and Kavita speak OIDC natively.
  FileBrowser remains the only consumer that also relies on the header-stripping rule for its own
  proxy auth.
- Git: groups, enrollment default, bindings, MFA stage bindings and FileBrowser's provider live in
  `cluster/helm/authentik/files/`. Folder scopes and group membership live in the two UIs.

## 7. Rollout

Phase A — levels and bindings.

1. Blueprint: `homelab-users`, `homelab-guests`; enrollment writes guests; admin binding plus user
   binding per app per the matrix; FileBrowser untouched.
2. Retire `app-access`: remove the policy entry and every application's metaapply reference and
   `policy:` binding, replacing each with group bindings. Apps with two levels run in `any` mode.
3. ArgoCD by repo values: Dex connector in `configs.cm`, `g, authentik Admins, role:admin` and
   `g, homelab-users, role:readonly` in `configs.rbac`, applied with `helm upgrade`; the local
   `admin` account stays as break-glass.
4. Operator moves memberships; second commit removes `apps` and `users`.
5. Verify: a guest signs in, sees no applications, is refused at a bookmarked app URL; a user
   sees the same, because no user-safe application is wired yet; the admin opens Grafana as Admin
   and pgAdmin as Administrator.

Phase B — FileBrowser capability.

1. Upgrade the image to the last 2.63.x (`>= 2.63.19`).
2. Blueprint: proxy provider, application `filebrowser`, bindings to `authentik Admins` and
   `filebrowser-editors`, capability group created.
3. Traefik: forward-auth middleware and the header-stripping rule on the FileBrowser router.
4. FileBrowser: `auth.method=proxy`, `auth.header=X-authentik-username`.
5. Create the editor's account and scope in FileBrowser, then add them to the group.
6. Verify: a user who is in neither group is refused; a forged `X-authentik-username` from the
   LAN is refused; the editor sees only their folders.

Phase C — MFA.

1. Blueprint: validation stage bindings (password flow and source authentication flow), device
   classes TOTP and WebAuthn, skip-if-none.
2. Verify: enrol TOTP, sign out, sign in with Discord, get asked for the code; delete the device,
   sign in again, not asked; enrol two YubiKeys and confirm either works.

## 8. Verification

Before any completion claim, on the real tree:

- `helm dependency build` then `helm template` for the authentik chart: blueprints render, group
  names appear, no secret is inline.
- ArgoCD reference check from `AGENTS.md` §6 still passes.
- Worker log after a sync has no blueprint failure; the groups exist; each application's binding
  list matches the matrix.
- Live tests, in a private window, with a guest, a user and the admin identity. Only one human
  identity exists today, so the level and binding tests use a locally created test account (the
  local password login remains enabled as break-glass); the Discord/Plex source flow and MFA are
  tested with the operator's own identity, enrolling then removing a device.
- Wired applications today are Grafana and pgAdmin, both admin-only: the admin establishes both,
  and both refuse a user-level account. The user level is proven "refused everywhere" until a
  user-safe application is wired.
- FileBrowser: header spoof refused, scope correct after the manual step.
- MFA: enrolment, challenge on both login paths, removal.

## 9. Risks

- **Group rename window.** Bindings point at new groups before people are in them. Short and
  announced; phase A step 4 is not optional.
- **ArgoCD is a hand-run Helm release.** Its values live in the repo, but nothing applies them
  until the operator runs `helm upgrade`; a rebuild repeats it. Recorded in the runbook.
- **Retiring `app-access` briefly changes admission.** Between removing the policy and the group
  bindings taking effect in the same sync there is one blueprint apply; both are in the same
  commit, so the window is the apply itself.
- **FileBrowser is archived.** No security fixes. It stays behind authentik, unprivileged, with
  the runner off. If it breaks, the migration to Quantum is the replacement path, and the manual
  scopes are re-created there.
- **Header forgery.** Only Traefik with the stripping rule may reach the pod; the Service stays
  ClusterIP.
- **Auto-created scope.** Proxy auth creates accounts with the default scope; the group is granted
  only after the account and scope exist.
- **MFA is honest but optional.** Someone who never enrols a device has one factor. This is the
  operator's decision, not an oversight.
- **Source-flow MFA binding is the fragile part.** A future source that adds its own flow must
  bind the validation stage too, or its users bypass MFA.
- **Admin mapping is per application.** pgAdmin, Kavita and Discourse need one manual promotion
  each, and a rebuild repeats it.

## 10. Left to the builder

- Exact blueprint fields for the Dex connector and RBAC in the ArgoCD chart values.
- The exact recovery command for a locked-out administrator.
- Whether Kavita links an OIDC identity to an existing local admin account by email.
- The order of applications within phase A after capability checks.
