# freshrss

FreshRSS is a self-hosted RSS and Atom feed reader: one place that collects the feeds, keeps the
articles it has already seen and serves them through a web UI and a Google Reader–compatible API.
The chart follows the contract in [`docs/charts/README.md`](../../../../docs/charts/README.md).

## Upstream

- Website: https://freshrss.org/
- Docs: https://freshrss.github.io/FreshRSS/
- Source: https://github.com/FreshRSS/FreshRSS
- Image: https://github.com/linuxserver/docker-freshrss

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
| PersistentVolumeClaim `news-freshrss-config` | the config, including the SQLite database and the cached articles |
| Service `news-freshrss` | ClusterIP, port 80, port name `webui` |
| ServiceAccount `news-freshrss` | token not mounted; the pod talks to no Kubernetes API |
| IngressRoute `news-freshrss-public` (+ `-public-redirect`) | `freshrss.kub.home.ashudev.com`, TLS through `myresolver`, LAN allowlist only |
| Middleware `news-freshrss-redirect-https` | the plain-HTTP mirror sends the browser to TLS |
| Middleware `news-freshrss-security` | rendered because the cluster has it; **no route references it** (known debt) |
| TLSOption `news-freshrss-tlsoption` | the per-app cipher policy the live route already uses |

## What the values control

| Value | Purpose | Default |
|---|---|---|
| `image.repository`, `image.tag` | Image. The tag is pinned and never floats. | `lscr.io/linuxserver/freshrss`, `1.30.4-ls332` |
| `strategy` | Must stay `Recreate` with a config claim. | `Recreate` |
| `service.port` | The container port, the Service port and every probe read this one value. | `80` |
| `storage.*` | The config claim: class, size, mount path, or an existing claim. | `local-path`, `1Gi`, `/config` |
| `env` | Plain variables, rendered inline so a change rolls the pod. | `PUID`/`PGID` 1100, `TZ=Europe/Paris` |
| `extraEnv`, `envFrom` | Secret-backed variables. Empty here. | `[]` |
| `probes.*` | TCP for startup/liveness, HTTP `/i/` for readiness — see below. | enabled |
| `resources` | A protection ceiling, **unmeasured**. | `10m`/`128Mi` request, `512Mi` limit |
| `nodeSelector` | Node pin. A `local-path` claim is bound to it. | `kubernetes.io/hostname: nuc0` |
| `ingress.*` | Host, API group, middlewares and TLS for the two IngressRoutes. | kept from the live routes |

## Hardening, and the one capability the other LinuxServer charts do not add

`allowPrivilegeEscalation: false`, seccomp `RuntimeDefault`, and `drop: [ALL]` with four capabilities
put back: `CHOWN`, `SETUID` and `SETGID` for the s6 init, plus **`DAC_OVERRIDE`**.

That fourth one is not decoration and not copy-paste. This image's nginx and php-fpm **masters** start
as root and then read and write files under `/config` that the init has chowned to `abc` (1100) — the
TLS key the image generates in `/config/keys`, and `/config/log/php/error.log`. With `drop: [ALL]` and
no `DAC_OVERRIDE`, root is subject to those file modes like any other user, and the first start on
2026-11-19 failed exactly there:

```
ERROR: failed to open error_log (/config/log/php/error.log): Permission denied (13)
[emerg] cannot load certificate key "/config/keys/cert.key": ... Permission denied
```

The pod sat `Running`, never ready, with the startup probe answering `connection refused` — nothing
was listening, because nginx had exited. `kavita`, `plex`, `tautulli` and the other LinuxServer charts
do not need this: their apps run as `abc` and never ask root to read a 1100-owned file. Keep the
capability while those two masters run as root; removing it means running the whole image as 1100,
which this image does not support.


## The claim this chart creates

The chart mounts the claim it creates — that is the whole point of this rebuild. Before it, the
Deployment mounted `freshrss-volume` while nothing in the `news` namespace created a claim of that
name, and the pod sat `Pending` for 77 days. The claim is now `news-freshrss-config`, derived from
the release name by `app.configClaimName`.

**First start is empty.** The old volume was on `longhorn-static` and died with the Longhorn
storage in 2026-08, and its PV has since been cleared from the cluster (checked through the
read-only API on 2026-11-19: no PV and no PVC named `freshrss-volume` exists anywhere). There is no
export to restore either: the backup job has not run since 2024 (see `docs/audit/ansible-hosts.md`),
so the subscriptions and the articles in them are gone. The instance comes up at the install
wizard.

Size: `1Gi`, not the 128Mi house default. FreshRSS keeps its SQLite database *and* the bodies of the
articles it has fetched under `/config`, so the volume grows with the reading, not with the
settings. 1Gi is a starting point; the first thing to do after login is set an article retention
policy, then revisit the size against what it actually holds.

Node: `nuc0`. The Deployment used to carry `node-type: general`, the label retired with the node
taxonomy; `nuc0` is the node that label meant, and where the general catalogue lives.

## Probes

FreshRSS ships no health endpoint, so the chart asks two different questions:

- **Startup and liveness: a TCP connect on port 80.** This is the "is PHP-FPM still listening"
  question. An HTTP probe would turn a database problem into a restart loop, which never fixes a
  broken file on disk.
- **Readiness: `GET /i/`, the login page.** Serving that page boots PHP, runs any pending database
  migration and reads the user table, so it fails when the SQLite file under `/config` is unusable —
  the one dependency that can fail while the process is still up. A failing readiness takes the pod
  out of the Service; it does not restart it.

`/` is deliberately not used anywhere: `index.php` is a 301 redirect to `p/`, which answers even
when the backend is broken.

## Logs

There is no log-level knob. FreshRSS writes its own log per user into `/config`
(Administration → Logs), and PHP errors reach the container's stderr at the image's `php.ini`
default. Neither is an environment variable, so the chart sets none — a `LOG_LEVEL` here would
silently do nothing.

What that costs: PHP's default is to report everything, and Fluent Bit ships every line to Loki with
no filter. The feed-refresh cron the image runs every 15 minutes writes the output of
`actualize_script.php` to `/tmp/FreshRSS.log` **inside the container**, which Fluent Bit does not
read, so refresh failures are visible only with `kubectl exec`. That is debt, recorded below.

## Known debt

- **No log level.** See above. Revisit if the app gains a knob, or if the refresh output matters
  more than the noise.
- **Refresh log is not shipped.** It lives in `/tmp/FreshRSS.log` in the pod, not on stdout. Fixing
  it needs a ConfigMap over the image's crontab, which is a change to how the image runs, not a
  chart rebuild.
- **`news-freshrss-security` is not referenced.** The header middleware renders and the cluster has
  it, but no route lists it. Attaching it changes what browsers get on a live route; that belongs
  with the routing work (W5/W7), not here.
- **Sizing is unmeasured.** The pod has not run since 2026-08-09, and the six days Prometheus keeps
  hold no sample for it. Re-measure once it has a week of history:

  ```bash
  curl -sG http://prometheus.kub.local/api/v1/query --data-urlencode \
    'query=max_over_time(container_memory_working_set_bytes{namespace="news",container="freshrss"}[6d])/2^20'
  ```

- **The `FreshRSS-AutoTTL` extension is gone.** The old chart ran an `alpine/git` init container
  that cloned it from `https://server/repo` — a placeholder that never resolved, so the init could
  only fail. It was removed; the extension can be installed from FreshRSS's own extension manager
  if it is wanted back.

## After the sync

1. ArgoCD syncs `news-freshrss`; the pending pod is replaced and the claim binds to `nuc0`.
2. The claim binds on first schedule, not before. If it ever binds to the wrong node, delete the
   claim and then the pod — deleting the claim alone leaves the pod in the scheduler's queue keyed
   on the old claim.
3. Open `https://freshrss.kub.home.ashudev.com/i/` from the LAN: the install wizard asks for the
   database (SQLite, the default) and the admin account. A first-run setup does **not** survive the
   container being replaced before the wizard is finished — finish it in one go.
4. Set the article retention policy, then re-import the OPML if a copy turns up outside the cluster.
5. `freshrss.kub.home.ashudev.com` is reachable only from the LAN: the route carries
   `kube-system-lan-only`, and nothing in front of the app asks for a login beyond FreshRSS's own.

## If the wizard wedges with "Error during context user init!"

Every URL under `/i/`, including the wizard itself, answers `500` with that one line. Nothing in the
container log explains it, because PHP writes its errors to `/config/log/php/error.log`.

**Why it happens.** `p/i/index.php` loads `app/install.php` only while
`data/applied_migrations.txt` is **absent**. The installer's last step writes that file. If it is
reached before the admin account exists — the step is addressable directly (`/i/?step=5`, and the
stepper links allow it) — then every later request takes the "already installed" branch, boots the
app, and `FreshRSS_Context::initUser()` finds no user configuration to load. The wizard is
unreachable from that point, because the branch that would render it is skipped.

**Recovery.** Remove the half-written state; the claim is worthless until the wizard finishes, so
nothing real is lost:

```bash
kubectl -n news exec deploy/news-freshrss -- sh -c '
  rm -f  /config/www/freshrss/data/applied_migrations.txt \
         /config/www/freshrss/data/config.php &&
  rm -rf /config/www/freshrss/data/users/_'
```

`/config/www/freshrss/data` is the image's `DATA_PATH`, symlinked to `/app/www/data` by its s6 init;
`users/_` is the placeholder user the aborted run created. Reload `/i/` and the wizard starts again at
the language step.

**Then finish it in one pass** — language, checks, SQLite, admin account. Do not stop between the
database step and the admin step: the database step already writes `data/config.php`, and that is
what lets a stray visit to the last step wedge the install.

A completely fresh claim works too and is less fiddly, at the cost of a scale-down and a sync: delete
the `news-freshrss-config` claim and the pod, and ArgoCD recreates both.
