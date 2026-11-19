# FileBrowser Quantum behind authentik — design

- **Date:** 2026-11-19
- **Status:** draft, awaiting operator approval
- **Card:** #227 (`FileBrowser: settle the login, the folders and the archived upstream`), plus #285 for
  the leftover pod
- **Supersedes:** the "authentik deferred" decision in
  [`2026-11-14-filebrowser-namespace-design.md`](2026-11-14-filebrowser-namespace-design.md) §1, and
  the FileBrowser rows in `docs/status.md`, `docs/reference/sso-applications.md` and
  `docs/how-it-works/applications.md`
- **Decisions marked (operator)** were taken in conversation; **(design)** is this document's call and
  can be overruled.

## 1. Intent

FileBrowser is not broken: the pod is 1/1, the three NFS shares are mounted, and the route answers the
LAN. What is wrong is that upstream archived the project on 2026-09-01, that the app still
authenticates with its own password-only login, and that there is no way to give one person access to
one folder.

The work is therefore three decisions the rebuild left open, plus the reason the operator asked for it:

- run the **maintained fork** (FileBrowser Quantum) instead of the archived upstream;
- put **authentik sign-in** in front of it, natively, so each browser carries its own identity;
- let the operator **share one file publicly** with a link, and give **one person (the editor) an
  account scoped to the creator folder**;
- hide the password form, so the app sends every browser straight to authentik.

Success is:

- `https://filebrowser.kub.home.ashudev.com` answers from the internet, and an unauthenticated browser
  is sent straight to authentik — there is no password form (operator, 2026-11-19).
- The operator signs in with authentik and sees every source, as an administrator.
- The editor has a real authentik account, signs in with it, and sees the creator folder only.
- No login surfaces in the app besides authentik's; with authentik down nobody signs in, and the
  documented recovery is a commit that re-enables the password method (§7).
- One normal share link created for one file downloads from a phone on mobile data, with no account.
- Pod 1/1, the same three NFS exports mounted plus the creator export, config and database on
  `local-path`, and `hack/check-exposure.sh` passes.

## 2. What exists today

Read from the repo and the cluster on 2026-11-19.

| Piece | State |
|---|---|
| Chart | `cluster/helm/filebrowser` is a wrapper (pattern D) around `filebrowser` 0.1.2 from `selfhosted-helmcharts`, pinning the archived image `filebrowser/filebrowser:v2.63.23`; three local templates: IngressRoute, TLS option, NFS PV+claim per export |
| Workload | `filebrowser-58d94b7b7d-s4v9h`, 1/1 on `nuc0`, 4 days old, running as root |
| Sources | `/srv/various` (`/mnt/storage/nfs/stash`), `/srv/audio` (`/mnt/storage/nfs/audio`), `/srv/torrent` (`/mnt/ultrafaststorage/nfs`) |
| Route | `filebrowser.kub.home.ashudev.com`, `websecure`, `certResolver: myresolver`, middlewares `lan-only` + `security`; no authentik |
| DNS | the name resolves publicly to `80.14.57.209`; `lan-only` is the only thing keeping it off the internet |
| authentik | blueprint `app-filebrowser.yaml` defines a **forward-auth** provider, application `filebrowser` and group `filebrowser-editors`; the provider is attached to the outpost by hand and nothing uses it |
| Upstream | archived 2026-09-01 (`docs/status.md`) |

The forward-auth provider cannot serve this design: a gate in front of the app answers the sign-in
wall to share links too, and every browser that passes it is the same user to the application. Native
OIDC is also the repo's stated preference (AGENTS.md §4).

## 3. Goals, non-goals, anti-goals

**Goals**

- The maintained fork, pinned by chart version and image tag.
- Native OIDC through authentik; per-user identity inside FileBrowser, so the editor is a real user
  and not a second anonymous browser behind a gate.
- One public file link that needs no account, for Discord.
- One folder for the editor, granted by scope, not by a separate instance.
- No password form in the app: one identity, one login (operator, 2026-11-19).

**Non-goals**

- Sync clients, office editing, calendars, contacts: this is a file browser, not Nextcloud.
- Per-folder policy in authentik. Folders are scoped inside FileBrowser and recorded in the runbook.
- Replacing NFS, moving the three existing exports, or migrating the old database (abandoned by
  decision on 2026-11-14).
- A second public hostname, or a second authentik instance.

**Anti-goals**

- A forward-auth gate that makes share links require a login.
- A public route with no authentication in front of the file manager itself.
- A floating `latest` tag, or an image that drifts from the chart's expectation.
- A second, hidden way into the app that the operator forgets to close. Dropping the password method
  is deliberate; leaving it enabled while claiming the form is hidden would be the mistake.

## 4. Approaches

**A — own local chart (pattern A).** Full control of the Deployment: node pinning, probes, port,
security context and the `config.yaml`. Rejected by the operator; it also duplicates a chart that
already exists.

**B — the community chart, as a dependency of our wrapper (chosen, operator).** `Softwaredam/filebrowser-chart`
is the community Helm chart for FileBrowser Quantum, announced in the fork's own discussion #931 and
kept in step with each stable release. It exposes the app's `config.yaml` as Helm values, takes
existing PVCs as additional volumes, and reads secrets from existing Secrets. Our wrapper keeps the
IngressRoute, TLS option and NFS templates it already has.

**C — stay on the archived upstream and only add the gate.** Rejected: share links would need
accounts, the project receives no fixes, and it has no OIDC.

### 4.1 What approach B costs

The chart is community-maintained and thin. Two consequences are designed around, not ignored:

- **No scheduling fields.** Its Deployment has no `nodeSelector`, `affinity` or `tolerations` value, so
  the repo rule "every workload is pinned to a named node" cannot be met through values. The pod can
  land on any node, including `rbp2` (ARM64). §8 records the mitigation and the follow-up card.
- **A new admin password on every upgrade.** The chart generates one into its own Secret each render.
  It is inert here: the password method is off (§5.5), so nothing reads it. It stays in the rendered
  Secret because the chart has no switch for it — a secret with no consumer, not a login.

## 5. Decisions

### 5.1 Chart wiring

`cluster/helm/filebrowser/Chart.yaml` keeps its name and gains the upstream as a dependency:

```yaml
dependencies:
  - name: filebrowser-quantum
    version: 2.0.2-stable
    repository: oci://ghcr.io/softwaredam/helm-charts
```

`cluster/helm/filebrowser/charts/` and `Chart.lock` join the other wrapper charts in `.gitignore`
(commented "Built by ArgoCD at sync time"), and ArgoCD runs `helm dependency build` at sync. The
Application stays single-source and local (`path: cluster/helm/filebrowser`), so no Application shape
changes. Renovate covers the dependency and the image tag (two `# renovate:` annotations).

### 5.2 Image

`ghcr.io/gtsteffaniak/filebrowser:2.0.2-stable` — the `-stable` line, not `2.1.x` (beta) and not
`latest`. Chart version and image tag are the same string, which makes the pairing obvious in review.

### 5.3 What the chart renders, and the names that follow

The chart names every object `<release>-filebrowser-quantum`, and its Service listens on **8080**:

| Object | Name |
|---|---|
| Deployment, Service, ConfigMap, Secret | `filebrowser-filebrowser-quantum` |
| config claim | `filebrowser-filebrowser-quantum-config` (`/home/filebrowser/data`, holds `database.db`) |
| data claim | `filebrowser-filebrowser-quantum-data` (`/data`, unused by our sources) |

So `traefik.service` in our values becomes `filebrowser-filebrowser-quantum` and `traefik.port`
becomes `8080`. The IngressRoute template does not change.

### 5.4 Sources

Four sources, three of them the exports already mounted, one new:

| Source name | Mount | NFS export |
|---|---|---|
| `various` | `/srv/various` | `/mnt/storage/nfs/stash` |
| `audio` | `/srv/audio` | `/mnt/storage/nfs/audio` |
| `torrent` | `/srv/torrent` | `/mnt/ultrafaststorage/nfs` |
| `creator` | `/srv/creator` | `/mnt/storage/nfs/creator` **(new export, prerequisite)** |

The creator export does not exist on the NAS today: the provisioner carries books, plexmedia, audio,
qbittorrent-config, stash, syncthing, traefik and ultrafaststorage. The operator creates the directory
and the export before the first sync, or changes one value (`nfsVolumes[].path`) if the folder already
lives somewhere else — for example a subfolder of the stash export. Until the export exists the pod
fails to mount and does not start; that is the prerequisite, not a defect to work around at runtime.

Every source sets `defaultEnabled: false`. Quantum otherwise gives a brand-new OIDC user the default
scope on every source with `view` and `download` on, which would hand the editor the whole NAS. With it
off, access is granted per person (§5.6).

**Administrators are not exempt from this.** The fork's `MergeDefaultEnabledBackendScopes` never adds a
`defaultEnabled: false` source to a user's scopes, and `AdminSourceFilePermissions` only fills in
permissions on scopes the user already has. So the operator's own OIDC account, created fresh on first
sign-in, starts with **no sources** and is granted the four in §5.6 too. The alternative — enable the
defaults and set every permission false — would put four empty sources in the editor's sidebar and
expose source names for no benefit.

### 5.5 Authentication

In the app's `config.yaml`, rendered from values:

```yaml
auth:
  methods:
    password:
      enabled: false
      signup: false
    oidc:
      enabled: true
      issuerUrl: https://authentik.kub.home.ashudev.com/application/o/filebrowser/
      clientId: filebrowser
      clientSecret: <FILEBROWSER_OIDC_CLIENT_SECRET from Secret>
      scopes: openid email profile
      userIdentifier: preferred_username
      adminGroup: authentik Admins
      userGroups: [authentik Admins, filebrowser-editors]
```

- **Callback:** `https://filebrowser.kub.home.ashudev.com/api/auth/oidc/callback`, registered on the
  authentik provider as a strict redirect URI.
- **Group claim:** `groups` arrives with the `profile` scope, whose authentik mapping already emits
  group membership (the calibre blueprint relies on the same mapping). `adminGroup` makes the operator
  an administrator on sign-in; `userGroups` admits the editor's group and nobody else. The scope list
  deliberately omits a `groups` scope — authentik has no such scope and the claim does not need it.
  If group mapping ever fails, `userGroups` would refuse every login including the operator's; the
  recovery row in §7 covers it.
- **No password form** (operator, 2026-11-19). With OIDC as the only method, the app sends every
  unauthenticated browser straight to authentik, which is what was asked for. The cost is accepted:
  the operator's incident path is no longer a local password but a commit (§7). The chart's generated
  `FILEBROWSER_ADMIN_PASSWORD` is left unread.
- **Client secret:** Secret `filebrowser-oidc` key `CLIENTSECRET` in namespace `filebrowser`, injected
  with `extraEnv` as `FILEBROWSER_OIDC_CLIENT_SECRET`. The same value lives in the `authentik-sso`
  Secret as `FILEBROWSER_CLIENT_SECRET`, because authentik reads it with `!Env` at blueprint apply
  time and Secrets do not cross namespaces. Changing it needs a worker restart.
- **Share links** are unauthenticated by nature and stay that way, because nothing sits between Traefik
  and the app.

### 5.6 Accounts

**The editor.** The account does not exist yet (operator, 2026-11-19), so the work creates it in
authentik: a local user, member of `filebrowser-editors` and `homelab-guests`, with a generated
password the operator hands over. A local account is the only option that does not depend on the
editor joining the Discord guild or getting a Plex library share; authentik's identification stage
still accepts a local username, so the account works without touching either source.

On first OIDC sign-in the app creates the matching FileBrowser user. Because every source is
`defaultEnabled: false`, it starts with no access at all, and the operator grants one scope on
`creator` in **Settings → User management**:

| Permission | Value |
|---|---|
| view | true |
| download | true |
| create | true |
| modify | true |
| delete | false |

This is the design's call (an editor returns files, so upload is needed; delete is not). It is a
two-minute change if the answer is read-only. The grant is recorded in
`docs/reference/sso-applications.md` and the FileBrowser runbook, because it lives in the app's
database and not in git (the old card's "per-user folders" item).

**The operator does the same once**, on the same page: grant the four sources to their own OIDC
account after the first sign-in. As an administrator the permissions on each scope are full
automatically; the scopes themselves are never automatic (§5.4). Until that grant exists, an admin
sign-in shows an empty sidebar.

### 5.7 Exposure

The route becomes public: `websecure`, host unchanged, `certResolver: myresolver`, TLSOption
`filebrowser-tlsoption`, and **`security` as the only middleware**. `lan-only` and any
`authentik-forwardauth` are removed.

That is forced by the requirements: the editor signs in from outside, and a fan opens a share link
from Discord. It is also what the other public names already do. `filebrowser.kub.home.ashudev.com`
joins `PUBLIC_HOSTS` in `hack/check_exposure.py` and the public list in AGENTS.md §4. The app host is
the sign-in-protected surface, not the share page: the login is authentik's, on its own already-public
name.

### 5.8 Runtime and storage

- `securityContext`: `runAsUser: 1100`, `runAsNonRoot: true`, `fsGroup: 1100` — the repo's `PUID`
  for LinuxServer images, and the identity that already writes to these exports. Verify by uploading
  into each source on the first sync; if the NAS refuses, the value is one line.
- `persistence`: both claims on `local-path`, 1 Gi each, `keep: true`.
- `resources`: requests `cpu: 1m`, `memory: 64Mi`; limit `memory: 256Mi`. The chart's default
  (300m/500m, 150Mi/300Mi) is airport-grade for a file browser.
- `http.trustProxyHeaders: true` and `http.externalUrl: https://filebrowser.kub.home.ashudev.com`, so
  share links are built with the right scheme and host.

### 5.9 authentik blueprint

`cluster/helm/authentik/files/app-filebrowser.yaml` keeps its name, group and bindings and replaces
the proxy provider:

- `authentik_providers_oauth2.oauth2provider` named `filebrowser`, `client_id: filebrowser`,
  `client_type: confidential`, `client_secret: !Env FILEBROWSER_CLIENT_SECRET`, grant types
  `authorization_code` + `refresh_token` (the trap the calibre blueprint records: blueprint-created
  providers start with no grant types), the redirect URI above, and the `openid`, `email` and `profile`
  scope mappings.
- The application keeps slug `filebrowser`, so the issuer URL stays
  `https://authentik.kub.home.ashudev.com/application/o/filebrowser/` and the dashboard tile and icon
  are unchanged.
- The "attach the provider to the outpost in the UI" step disappears: an OAuth2 provider needs no
  outpost.
- Bindings: `authentik Admins` and `filebrowser-editors`, unchanged.

## 6. What happens to the old objects

- The old Deployment, Service and Secret (`filebrowser`, image `v2.63.23`) are pruned by ArgoCD when
  the new chart renders; the Secret's `FB_*` keys are no longer read.
- The old claims `filebrowser-config` and `filebrowser-database` are local-path volumes holding the
  abandoned database. They are deleted by hand after the cutover (their contents were abandoned on
  2026-11-14).
- Card #285's `Terminating` pod in `media-stack` is unrelated to this sync and is cleared in the same
  PR's hand-over notes.
- The unused forward-auth provider, if the blueprint reconcile leaves it behind, is removed by hand
  once and checked with the authentik API.

## 7. Failure modes and recovery

| Failure | Effect | Recovery |
|---|---|---|
| authentik down | no sign-in at all; **existing share links still download**, because the app serves them itself | re-enable the password method in `values.yaml` (`password.enabled: true`, `signup: false`), commit, sync, sign in as the local admin from the chart's Secret; revert once authentik is back |
| OIDC client secret mismatch | app answers a callback error after authentik | fix either Secret, restart the worker, restart the pod |
| `groups` claim missing or renamed | `userGroups` refuses **every** OIDC login, the operator's included | same commit as the row above, then fix the authentik profile scope mapping, retry |
| Admin sees an empty sidebar | the account has no scopes yet (§5.6) | grant the four sources from User management |
| `creator` export missing | pod stuck in `ContainerCreating`/timeout mounting | create the export, or point `nfsVolumes[].path` at the real folder |
| NFS refuses uid 1100 | uploads fail with permission denied, reads fine | change `securityContext` to the owner's uid |
| Pod lands on `rbp2` (no scheduling fields) | slow, but works if the ARM image pulls | follow-up card: pin the chart or move to our own chart |
| Bad sync | app unreachable | `helm rollback` through Argo CD, or revert the commit |

The first row is the price of hiding the login. The operator still reaches the files themselves over
SMB/NFS on the NAS, and the recovery is git — but it is a commit, not a password field, and the
runbook says so.

## 8. Verification

Before merge, without a cluster:

1. `helm dependency build cluster/helm/filebrowser` then
   `helm template test cluster/helm/filebrowser -f cluster/helm/filebrowser/values.yaml` — renders the
   Deployment, Service, ConfigMap with the OIDC block, the four PV/claim pairs, the two local-path
   claims, the IngressRoute on port 8080 and the TLSOption.
2. `hack/check-exposure.sh` passes, with the public host allowlisted and the route ungated.
3. The ArgoCD reference checks in AGENTS.md §6 stay clean.

After sync:

4. Pod 1/1, no restarts; `curl -sI https://filebrowser.kub.home.ashudev.com/` answers, and a browser
   that has no session is redirected to authentik with no password form in between.
5. Operator signs in via authentik, is an administrator, grants their own account the four sources,
   and sees them.
6. Editor signs in with the authentik account the plan created, sees `creator` only, uploads and
   downloads one file, cannot delete.
7. A normal share link opens from a phone on mobile data with no account.
8. The password method is off in the rendered ConfigMap, and no local admin login is reachable.
9. Restart the pod; sources and the editor's scope survive (they live in the config claim).

## 9. Documentation

- `cluster/helm/filebrowser/README.md` (new): the fork decision, the chart dependency, the sources, the
  OIDC-only login with the recovery procedure, and why the route is public.
- `docs/reference/sso-applications.md`: FileBrowser moves from "proxy auth, not configured" to native
  OIDC, with the editor's account and grant recorded.
- `docs/runbooks/authentik-apps.md`: the FileBrowser section — the two Secrets, the callback URI, the
  worker restart, the editor's account, and the re-enable-password recovery.
- `docs/how-it-works/applications.md` and `docs/status.md`: the row for the app.
- `AGENTS.md` §4 public list; `docs/reference/routes.md`.
- `CHANGELOG.md`: one `Changed` entry.
- Cards #227 and #285 move to `Validation` with branch, PR and the verification output.

## 10. Follow-ups this design does not do

- Pin the pod's node. The community chart cannot; the options are an upstream pull request adding
  `nodeSelector`/`affinity`/`tolerations`, a fork of that chart, or the local chart in approach A.
  Filed as a card so the gap has an owner.
- Renovate coverage for the OCI dependency and the image.
- Cleanup of the old claims and Secret once the cutover holds.
