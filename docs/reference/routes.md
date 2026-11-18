# Routes: every hostname, its gate and its certificate

> **In one line.** Traefik answers the 34 hostnames in this table: four are public, twenty-six are
> internal over TLS, four answer plain HTTP, and two more names exist only to redirect; the gate
> column — not the address — is what decides who gets in.
>
> **Use it when** you need to know whether a hostname is reachable from outside, what stands in
> front of an application before its own login, or which certificate a browser will be shown ·
> **Verified** 2026-11-18 · **Kind:** living page

Read from the live cluster on 2026-11-17 through the read-only Kubernetes MCP: every
`traefik.containo.us/v1alpha1` `IngressRoute` in every namespace, plus the `Middleware` objects in
`kube-system`. The three concept layers behind the columns are
[how-it-works/network.md](../how-it-works/network.md); who signs in where is
[reference/sso-applications.md](sso-applications.md).

Columns: **Host** as the router matches it; **Application** as you would name it to a person;
**Namespace** for `kubectl`; **Gate** the middlewares that run before the request reaches the app;
**TLS** what answers the handshake; **Public?** whether
[`hack/check_exposure.py`](../../hack/check_exposure.py) allows the host to answer the internet.

`internal CA` means the `*.kub.local` leaf of the ashulab CA, served by default with no
`certResolver` on the route. `Let's Encrypt` means the route asks for its own certificate, whether
the hostname is public or a `*.kub.home.ashudev.com` name the LAN resolves to `192.168.1.46`.

## Public

These four are on the allowlist and are meant to answer the internet. None is gated: what sits on
them sets response headers and, for Plex and Seerr, a rate limit of 100 requests per second on
average. Neither of those is authentication.

| Host | Application | Namespace | Gate | TLS | Public? |
|---|---|---|---|---|---|
| `ashuvidz.com` | ashuvidz website (nginx) | `ashuvidz` | none — `security` headers | Let's Encrypt | yes |
| `forum.ashuvidz.com` | Discourse | `discourse` | none — `security` headers | Let's Encrypt | yes |
| `plex.kub.home.ashudev.com` | Plex | `media-stack` | none — `security` headers, rate limit (two `websecure` routers) | Let's Encrypt | yes |
| `seerr.kub.home.ashudev.com` | Seerr | `media-stack` | none — `security` headers, rate limit | Let's Encrypt | yes |

## Internal over TLS

Twenty-six hostnames. Seventeen use the internal CA on a `*.kub.local` name; nine carry their
own certificate on a `*.kub.home.ashudev.com` or `*.ashudev.com` name.

| Host | Application | Namespace | Gate | TLS | Public? |
|---|---|---|---|---|---|
| `argocd.kub.local` | Argo CD | `argocd` | `lan-only` | internal CA | no |
| `authentik.kub.home.ashudev.com` | Authentik | `authentik` | `lan-only` | Let's Encrypt | no |
| `autobrr.kub.local` | autobrr | `media-stack` | `lan-only` | internal CA | no |
| `bazarr.kub.local` | Bazarr | `media-stack` | forward-auth; `lan-only` alone on `/api` | internal CA | no |
| `bazarr-anime.kub.local` | Bazarr (anime) | `media-stack` | forward-auth; `lan-only` alone on `/api` | internal CA | no |
| `calibre.kub.local` | Calibre-Web Automated | `media-stack` | `lan-only` | internal CA | no |
| `dashy.kub.local` | Dashy | `dashy` | `lan-only` then forward-auth, admins only | internal CA | no |
| `docs.kub.local` | the docs site | `docs` | `lan-only` then forward-auth | internal CA | no |
| `filebrowser.kub.home.ashudev.com` | FileBrowser | `filebrowser` | `lan-only`; the app does its own sign-in | Let's Encrypt | no |
| `freshrss.kub.home.ashudev.com` | FreshRSS | `news` | **none** | Let's Encrypt | no |
| `grafana.kub.local` | Grafana | `monitoring` | `lan-only` | internal CA | no |
| `kavita.kub.home.ashudev.com` | Kavita | `media-stack` | `lan-only` | Let's Encrypt | no |
| `koillection.kub.home.ashudev.com` | Koillection | `koillection` | **none** | Let's Encrypt | no |
| `lidarr.kub.local` | Lidarr | `media-stack` | forward-auth; `lan-only` alone on `/api` | internal CA | no |
| `maintainerr.kub.local` | Maintainerr | `media-stack` | forward-auth; `lan-only` alone on `/api` | internal CA | no |
| `n8n.kub.home.ashudev.com` | n8n | `n8n` | **none** | Let's Encrypt | no |
| `prowlarr.kub.local` | Prowlarr | `media-stack` | forward-auth; `lan-only` alone on `/api` | internal CA | no |
| `qbittorrent.kub.local` | qBittorrent | `media-stack` | forward-auth; the app's own login is bypassed for the pod network; no `/api` exemption | internal CA | no |
| `radarr.kub.local` | Radarr | `media-stack` | `lan-only`; OIDC in the app, `/api` on `lan-only` alone | internal CA | no |
| `radarr-anime.kub.local` | Radarr (anime) | `media-stack` | `lan-only`; OIDC in the app, `/api` on `lan-only` alone | internal CA | no |
| `sonarr.kub.local` | Sonarr | `media-stack` | forward-auth; `lan-only` alone on `/api` | internal CA | no |
| `sonarr-anime.kub.local` | Sonarr (anime) | `media-stack` | forward-auth; `lan-only` alone on `/api` | internal CA | no |
| `syncthing.kub.home.ashudev.com` | Syncthing | `syncthing` | **none** | Let's Encrypt | no |
| `tautulli.kub.local` | Tautulli | `media-stack` | `lan-only`; no gate, its own login | internal CA | no |
| `thelounge.kub.home.ashudev.com` | The Lounge | `thelounge` | **none** | Let's Encrypt | no |
| `vikunja.kub.local` | Vikunja | `vikunja` | `lan-only` | internal CA | no |

## Internal without TLS

Four routes serve plain HTTP. The LAN allowlist or a password is all that stands in front of them.

| Host | Application | Namespace | Gate | TLS | Public? |
|---|---|---|---|---|---|
| `loki.kub.local` | Loki, query frontend | `monitoring` | `lan-only` | none | no |
| `pgadmin.kub.local` | pgAdmin | `databases` | `lan-only` | none | no |
| `prometheus.kub.local` | Prometheus | `monitoring` | `lan-only` | none | no |
| `traefik.kub.local` | Traefik dashboard (`/dashboard`, `/api`) | `kube-system` | basic auth | none | no |

## What the table does not list

**Redirect twins.** Every host in the first two tables also has a `web` router on the same name that
only redirects to HTTPS, carrying `lan-only` and nothing else — `filebrowser` excepted, which has no
plain-HTTP route at all. The four rows in the last table are served on `web` itself. Three hostnames
exist only to redirect and are not rows in any table: `kub.home.ashudev.com` to Dashy and
`discourse.kub.home.ashudev.com` to Forum.

**`/api` exemptions.** Ten hosts — `bazarr`, `bazarr-anime`, `lidarr`, `maintainerr`, `prowlarr`,
`radarr`, `radarr-anime`, `sonarr`, `sonarr-anime`, `tautulli` — carry a second rule on
`Host(...) && PathPrefix(/api)` at `priority: 100` with the sign-in removed, because an API client
sends a key, not a browser session. `maintainerr` checks no key of its own, so on that host `lan-only`
is the only control on `/api`.

**Five routes carry no `lan-only`.** They are the five rows whose gate reads **none**. All five are
chart-first: the charts already emit `kube-system-lan-only@kubernetescrd`, the live objects predate
the rule, and a sync closes the gap. `hack/check-exposure.sh` sees all five and passes.

**Two routers on Plex.** `plex.kub.home.ashudev.com` is claimed twice, both on `websecure`:
`plex-public` (headers and rate limit) and a leftover `plex-media-public` from an older release with
no middleware. Equal rules, so what a browser gets is not settled by git.

**The Traefik dashboard hostname disagrees with itself.** The live router answers `traefik.kub.local`;
the only copy in git, `cluster/infrastructure/traefik/traefik-config.yaml`, sets
`` Host(`traefik.app.local`) ``. That file is applied by hand, no Application references it, and it
carries a mis-indented key, so neither spelling is authoritative until it is fixed and applied.

## Regenerating this list

The generator, run where the human kubeconfig is (the MCP server is read-only and shows summaries
only):

```bash
kubectl get ingressroute.traefik.containo.us -A -o json \
| jq -r '.items[] | .metadata as $m | .spec as $s | $s.routes[]
    | [ $m.namespace, $m.name,
        (.match | capture("Host\\(`(?<h>[^`]+)`\\)").h),
        ($s.entryPoints | join(",")),
        ([.middlewares[]?.name | sub("@kubernetescrd$"; "") | sub("^kube-system-"; "")] | join(" ")),
        ($s.tls.certResolver // "-"),
        ($s.tls.options.name // "-") ]
    | @tsv' | sort -k3,3
```

The filter prints the **first** `Host(`…`)` of each matcher — a rule matching two names shows one —
and it prints no rule priority, so a hostname served by two routers appears twice and looks like one
row too many.

Compare the result with `hack/check-exposure.sh`, which renders the charts rather than reading the
cluster: a row that differs between the two is drift, and the chart is the version that gets
reviewed. Names and empty cells explain themselves; `-` means "not set".
