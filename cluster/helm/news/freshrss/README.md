# freshrss

FreshRSS is a self-hosted RSS and Atom feed reader: one place that collects the feeds, keeps the
articles it has already seen and serves them through a web UI and a Google Reader–compatible API.
The chart follows the contract in [`docs/charts/README.md`](../../../../docs/charts/README.md).

## Upstream

- Website: https://freshrss.org/
- Docs: https://freshrss.github.io/FreshRSS/
- Source: https://github.com/FreshRSS/FreshRSS
- Image: https://hub.docker.com/r/freshrss/freshrss

## How this chart is deployed here

Not with `helm install`. ArgoCD renders it from `cluster/argocd/projects/news/freshrss.yaml` using
the chart in this directory and `values.yaml` next to it. The release name is the Application name,
`news-freshrss`, so the objects are `news-freshrss` (Deployment, Service, ServiceAccount),
`news-freshrss-config` (claim) and the `news-freshrss-*` routing objects.

```bash
helm template news-freshrss cluster/helm/news/freshrss --namespace news
```

| Object | Purpose |
|---|---|
| Deployment `news-freshrss` | one replica, `Recreate`, pinned to `nuc0` |
| PersistentVolumeClaim `news-freshrss-config` | the claim holding the data root, including the SQLite database and the cached articles |
| Service `news-freshrss` | ClusterIP, port 80, port name `webui` |
| ServiceAccount `news-freshrss` | token not mounted; the pod talks to no Kubernetes API |
| IngressRoute `news-freshrss-public` (+ `-public-redirect`) | `freshrss.kub.home.ashudev.com`, TLS through `myresolver`, LAN allowlist only |
| Middleware `news-freshrss-redirect-https` | the plain-HTTP mirror sends the browser to TLS |
| Middleware `news-freshrss-security` | rendered because the cluster has it; **no route references it** (known debt) |
| TLSOption `news-freshrss-tlsoption` | the per-app cipher policy the live route already uses |

## What the values control

| Value | Purpose | Default |
|---|---|---|
| `image.repository`, `image.tag` | Image. The tag is pinned and never floats. | `freshrss/freshrss`, `1.30.4` |
| `strategy` | Must stay `Recreate` with a config claim. | `Recreate` |
| `service.port` | The container port, the Service port and every probe read this one value. | `80` |
| `storage.*` | The claim: class, size, mount path, or an existing claim. | `local-path`, `1Gi`, `/config` |
| `env` | Plain variables, rendered inline so a change rolls the pod. | `TZ`, `DATA_PATH`, `CRON_MIN`, the `OIDC_*` set |
| `extraEnv`, `envFrom` | Secret-backed variables. Here the OIDC client secret and crypto key. | two keys from `freshrss-oidc` |
| `probes.*` | TCP for startup/liveness, HTTP `/i/` for readiness — see below. | enabled |
| `resources` | A protection ceiling, **unmeasured**. | `10m`/`128Mi` request, `512Mi` limit |
| `nodeSelector` | Node pin. A `local-path` claim is bound to it. | `kubernetes.io/hostname: nuc0` |
| `ingress.*` | Host, API group, middlewares and TLS for the two IngressRoutes. | kept from the live routes |

## The image, and why the capabilities differ from other LinuxServer charts

This chart runs the **official** `freshrss/freshrss` image, not LinuxServer's. It has to: native
OIDC is implemented with the Apache module `mod_auth_openidc`, which only the official Debian image
(x86_64) carries. `nuc0` is x86_64.

The image's entrypoint starts as root, creates the data root, hands it to the `www-data` group with
`CHOWN`, then Apache drops to `www-data` with `SETUID`/`SETGID`. `allowPrivilegeEscalation: false`,
seccomp `RuntimeDefault`, and `drop: [ALL]` stay; four capabilities come back: `CHOWN`, `SETUID`,
`SETGID`, and **`DAC_OVERRIDE`**.

`DAC_OVERRIDE` is not decoration. The claim root was chowned to uid 1100 by the LinuxServer image
this chart used to run, and its mode is not guaranteed group-writable. Without `DAC_OVERRIDE`, root
is bound by those modes like any other user and the entrypoint cannot create `/config/data` on the
first start. Removing the capability means fixing the claim's ownership by hand first.

## The data root

`DATA_PATH=/config/data`, not the claim root. The entrypoint creates that directory as root, so it
owns it and can give it the group write that the Apache worker needs; the claim root, owned by 1100,
cannot be relied on for that.

The LinuxServer image kept its data at `/config/www/freshrss/data`, and that tree is still there. On
2026-11-19 it was copied into `/config/data` rather than abandoned, which is what made the swap keep
the subscriptions and the read state — see "Carrying the old data root over" below. Removing
`/config/www` afterwards frees a few megabytes and is a destructive hand operation, so the chart
does not do it.

Size: `1Gi`, not the 128Mi house default. FreshRSS keeps its SQLite database *and* the bodies of the
articles it has fetched under the data root, so the volume grows with the reading, not with the
settings. 1Gi is a starting point; the first thing to do after login is set an article retention
policy, then revisit the size against what it actually holds.

Node: `nuc0`. The Deployment used to carry `node-type: general`, the label retired with the node
taxonomy; `nuc0` is the node that label meant, and where the general catalogue lives.

## Sign-in

The app speaks OIDC itself. The provider is the blueprint `ashulab-app-freshrss` in
`cluster/helm/authentik/files/`, named `freshrss`, with the callback
`https://freshrss.kub.home.ashudev.com:443/i/oidc/`. The route carries no authentik middleware: the
app prompts by itself, and a gate in front would be a second prompt that still carries no identity.

Only `/i/` is behind the module, which is the upstream reference configuration. `/p/api/` keeps its
own login, so the phone app and `hack/freshrss_sync.py` work with an API password and no browser
session. The route still carries `kube-system-lan-only@kubernetescrd`: OIDC is not a substitute for
the LAN boundary.

## Probes

FreshRSS ships no health endpoint, so the chart asks two different questions:

- **Startup and liveness: a TCP connect on port 80.** This is the "is the web server still listening"
  question. An HTTP probe would turn a database problem into a restart loop, which never fixes a
  broken file on disk.
- **Readiness: `GET /i/`.** With OIDC enabled that path answers `302` to authentik, and a probe counts
  any 3xx as ready. It still boots Apache and PHP, so a dead worker fails here while the TCP check
  would pass. It no longer reads the user table on every pass. The probe carries the three
  `X-Forwarded-*` headers: Traefik sets them on every proxied request, and without them
  mod_auth_openidc warns three times per pass — 103,617 lines in the 24 h to 2026-11-22, 19 % of all
  the lines Loki stored.

`/` is deliberately not used anywhere: `index.php` is a 301 redirect to `p/`, which answers even
when the backend is broken.

## Logs

There is no log-level knob. FreshRSS writes its own log per user under the data root
(`data/users/<user>/log.txt`, shown in the UI under Administration → Logs), and the API writes its
own under the pseudo-user `_` (`data/users/_/log_api.txt`). Neither is an environment variable, so
the chart sets none — a `LOG_LEVEL` here would silently do nothing.

What that costs: PHP messages go to Apache's error log, which this image sends to stderr, so they do
reach the pod log and Loki — but with PHP's default reporting, every notice comes along, and Fluent
Bit ships every line with no filter. The feed-refresh cron enabled by `CRON_MIN` writes the output of
`actualize_script.php` to `/tmp/FreshRSS.log` **inside the container**, which Fluent Bit does not
read, so refresh failures are visible only with `kubectl exec`. That is debt, recorded below.

The OIDC warnings are not part of that debt any more: they were the probe's missing headers, not the
module misbehaving, and the readiness probe now sends them.

## Carrying the old data root over

Two Secrets must exist before the sync that rolls the Deployment and applies the blueprint, because
the blueprint reads `!Env`:

- `news/freshrss-oidc` with `client-secret` and `client-crypto-key`;
- `authentik-sso` with `FRESHRSS_CLIENT_SECRET`, the same client secret.

Then, once the pod is up, the old data root is copied into place. Three things bite:

```bash
kubectl -n news exec -i deploy/news-freshrss -- sh -s <<'SH'
set -u
mv /config/data /config/data.fresh-before-restore
cp -a /config/www/freshrss/data /config/data
# the directory name is the username, and under OIDC the username is the claim
mv /config/data/users/<old-user> /config/data/users/vyrtualsynthese
sed -i "s/'auth_type' => '[^']*'/'auth_type' => 'http_auth'/" /config/data/config.php
sed -i "s/'default_user' => '[^']*'/'default_user' => 'vyrtualsynthese'/" /config/data/config.php
chown -R www-data:www-data /config/data
SH
kubectl -n news rollout restart deploy/news-freshrss
```

- **`cp -a` exits 1 and still copies.** The old tree belongs to uid 1100 and the container has
  `CHOWN` but not `FOWNER`, so it can set the owner and then cannot set the mode on a file it no
  longer owns. One `Operation not permitted` line per file, on stderr, and a non-zero exit; a script
  with `set -e` stops there. The `chown` at the end is what makes the result usable.
- **The user directory is the username.** With `auth_type: http_auth` FreshRSS looks up the account
  named in `OIDC_REMOTE_USER_CLAIM`, so the old directory has to be renamed to the authentik
  username or the app answers "user does not exist". There is no provisioning on first sign-in.
- **`auth_type` must end at `http_auth`.** The wizard writes `form` unless it is told otherwise, and
  a copied `config.php` carries whatever the LinuxServer install had. Left on `form`, the app asks
  for its own password *after* authentik — two logins, only one of them OIDC.

Finish with an API password (Settings → Profile), `FRESHRSS_USER` / `FRESHRSS_API_PASSWORD` in
`~/.config/secrets/homelab.env`, then `python3 hack/freshrss_sync.py apply --prune` to make the
instance match the file (see `docs/news-feeds.md`). Delete `/config/data.fresh-before-restore` once
the restored root is proven.

## First start on an empty data root

The alternative, when there is nothing to carry over. Order matters:

1. Create both Secrets as above, and let ArgoCD sync the Deployment and the blueprint.
2. Open `https://freshrss.kub.home.ashudev.com/i/` from the LAN. authentik asks for the sign-in
   first. In the wizard set the default user to **`vyrtualsynthese`** — the exact OIDC identity; a
   different name gets no administrator rights — set the authentication type to **HTTP
   authentication**, and give the password field a random value it will not use. Finish the wizard
   in one pass. The wizard offers the authentication type; left on its default it is `form`, and the
   second login comes back. Administration → Authentication changes it afterwards.
3. Make sure the API's log directory exists. A fresh data root does not have it, and without it every
   API call answers an empty HTTP 500 (the logger cannot write; see `docs/news-feeds.md`):

   ```bash
   kubectl -n news exec deploy/news-freshrss -- mkdir -p /config/data/users/_
   kubectl -n news exec deploy/news-freshrss -- chown www-data:www-data /config/data/users/_
   ```

4. Set an API password in Settings, then update `FRESHRSS_USER` and `FRESHRSS_API_PASSWORD` in
   `~/.config/secrets/homelab.env`.
5. Replay the feed list: `python3 hack/freshrss_sync.py apply` (see `docs/news-feeds.md`).

## Known debt

- **No log level.** See above. Revisit if the app gains a knob, or if the refresh output matters
  more than the noise.
- **Refresh log is not shipped.** It lives in `/tmp/FreshRSS.log` in the pod, not on stdout. Fixing
  it needs a ConfigMap over the image's crontab, which is a change to how the image runs, not a
  chart rebuild.
- **`news-freshrss-security` is not referenced.** The header middleware renders and the cluster has
  it, but no route lists it. Attaching it changes what browsers get on a live route; that belongs
  with the routing work (W5/W7), not here.
- **Sizing is unmeasured.** Re-measure once there is a week of history:

  ```bash
  curl -sG http://prometheus.kub.local/api/v1/query --data-urlencode \
    'query=max_over_time(container_memory_working_set_bytes{namespace="news",container="freshrss"}[6d])/2^20'
  ```

- **The old LinuxServer tree is still in the claim.** `/config/www` holds the database and users
  directory the previous image wrote; `/config/data` is a copy of it and is what the app reads.
  Deleting `/config/www` is the operator's call, and only once the copy has proven itself.

## If the wizard wedges with "Error during context user init!"

Every URL under `/i/`, including the wizard itself, answers `500` with that one line. Nothing in the
container log explains it, because PHP writes its errors under the data root.

**Why it happens.** `p/i/index.php` loads `app/install.php` only while
`data/applied_migrations.txt` is **absent**. The installer's last step writes that file. If it is
reached before the admin account exists — the step is addressable directly (`/i/?step=5`, and the
stepper links allow it) — then every later request takes the "already installed" branch, boots the
app, and `FreshRSS_Context::initUser()` finds no user configuration to load. The wizard is
unreachable from that point, because the branch that would render it is skipped.

**Recovery.** Remove the half-written state; the data root is worthless until the wizard finishes, so
nothing real is lost:

```bash
kubectl -n news exec deploy/news-freshrss -- sh -c '
  rm -f  /config/data/applied_migrations.txt \
         /config/data/config.php &&
  rm -rf /config/data/users/_'
```

`/config/data` is `DATA_PATH`; `users/_` is the placeholder user the aborted run created. Reload
`/i/` and the wizard starts again at the language step.

**Then finish it in one pass** — language, checks, SQLite, the admin account matching the OIDC
identity. Do not stop between the database step and the admin step: the database step already writes
`data/config.php`, and that is what lets a stray visit to the last step wedge the install.

A completely fresh data directory works too and is less fiddly: delete the contents of `/config/data`
from a debug pod and let the pod restart.
