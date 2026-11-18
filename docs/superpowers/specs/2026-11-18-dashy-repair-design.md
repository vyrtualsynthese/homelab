# Dashy repair: a start page in git, behind the gate, admins only

> **Card** 229 · **Branch** `feature/dashy-repair` · **Written** 2026-11-18 · **Size:** small change

## Intent

Dashy is the start page: the one page that links to every application. Today it is the least
trustworthy page in the cluster. Its route carries `kube-system-authentik`, which sets identity
headers and authenticates nothing, and `hack/check-exposure.sh` passes only because the chart emits
`lan-only` and the live object predates that rule. Its image is `latest`, pulled 82 days ago. Its
contents live in a Secret named `dashy-conf` that was created by hand and exists nowhere in git.

The operator wants it rebuilt from scratch with every application on it, and protected by authentik,
admins only for the moment.

## What exists today

| Piece | State |
|---|---|
| Chart | wrapper, `selfhosted-helmcharts/dashy` 1.0.0 pinned (published 2024-07-07, appVersion 3.1.1) |
| Image | `ghcr.io/lissy93/dashy:latest`; upstream is 4.9.19 |
| Routes | four `IngressRoute`s, 569–571 days old, all on `dashy.ashudev.com` and `kub.home.ashudev.com`; no `lan-only` live |
| Config | Secret `dashy-conf`, key `conf`, mounted at `/app/user-data/conf.yml`; never in git, unreadable through the read-only RBAC |
| Placement | `node-type: general\|small` (retired label), lands on `nuc0` |

## Goals

- One config file, in git, that defines the whole dashboard.
- One address, internal, behind `lan-only` and the forward-auth gate, admins only.
- No floating tag; node pinned; probes and memory limit set.
- `hack/check-exposure.sh` passes; the Application is Synced with no manual step.

## Non-goals

- Widgets, status checks, API keys. The page is links only.
- Widening access past `authentik Admins`. A second binding later is a one-line change.
- The six other live routes with no gate (`freshrss`, `n8n`, `koillection`, `syncthing`, `thelounge`,
  and Dashy itself). They are chart-first and sync closes them; that is not this card.
- Any change to `selfhosted-helmcharts`; the dependency stays pinned where it is.

## Anti-goals

- A route whose only "protection" is the headers middleware.
- A dashboard definition that lives only in the cluster.
- `latest`.

## Constraints

- Traefik v2 CRDs only (`traefik.containo.us/v1alpha1`); no `networking.k8s.io` Ingress.
- Every non-public route carries `kube-system-lan-only@kubernetescrd`.
- The chart must render with `helm template` from a clean checkout.
- Internal hostnames use the cluster's default certificate (`kube-system/local-cert`) and the
  `internal-tls` TLSOption; no `certResolver`, no `secretName`.
- Chart files are LF (`cluster/argocd/**` is the CRLF exception).

## Approaches

**How the config gets into the cluster**

1. **`cluster/helm/dashy/files/conf.yml`, rendered into the `dashy-conf` Secret by a chart template**
   *(chosen)*. The whole dashboard is a reviewable diff, the upstream chart's `static.existingSecret`
   mount keeps working unchanged, and the file stays a Secret so a credential added later fails a
   review instead of landing in a ConfigMap. Credentials, when they come, belong in
   `env.existingSecret` and override values from here.
2. Inline through the upstream chart's `static.configMapContent`. The template actually renders
   `toJson` of that string, which writes a JSON-quoted blob rather than the YAML, so this path is
   broken as shipped. Rejected.
3. Keep creating the Secret by hand. Rejected: it is the reason this card exists.

**The address**

- **`dashy.kub.local`** *(chosen)*: internal certificate, `lan-only`, then the gate. This is the
  `docs.kub.local` shape exactly, and an admin-only page has no business on a name that reads public.
- Keep `dashy.ashudev.com` with its Let's Encrypt certificate and the same gate. Rejected: fewer
  edits, but a public-looking name for a page only one account can open.

## The design

### 1. Chart

- `values.yaml`: `image.tag: "4.9.19"`; `nodeSelector: kubernetes.io/hostname: nuc0`; keep the
  measured requests (10m CPU, 256Mi) and limit (1Gi); keep `probs` but confirm Dashy 4.x answers `/`
  with a 200 once the config is in place; `static.existingSecret: dashy-conf` unchanged.
- New `templates/secret-config.yaml`: renders Secret `dashy-conf`, key `conf`, from `.Files.Get
  "files/conf.yml"`, with a `helm.sh/resource-policy: keep`-free, plain ownership so ArgoCD updates
  it. The upstream chart renders the Deployment, so a checksum cannot be computed into its pod
  template from here: the restart is an explicit bump of the `dashy.kub.local/config` annotation in
  `values.yaml`, in the same commit as a config change.
- The four current templates collapse to three: the `websecure` router on `dashy.kub.local`
  (`lan-only`, `authentik-forwardauth`, `security`, `internal-tls`), the plain-HTTP mirror on the
  same host (`lan-only` then redirect), and the `kub.home.ashudev.com` redirect pointed at the new
  name. `dashy.ashudev.com` disappears entirely.

### 2. Identity

New `cluster/helm/authentik/files/app-dashy.yaml`, copied from `app-docs.yaml`: a proxy provider in
`forward_single` mode with `external_host: https://dashy.kub.local`, an application `dashy`, and one
policy binding to `authentik Admins`. Add it to `outpost-embedded.yaml` twice — the
`metaapplyblueprint` list and the outpost's provider list — or the host answers 404. Expect the
first sync to roll the outpost file back and apply it again, as the comment in that file explains.

### 3. Dashboard content

One `conf.yml`, sections by purpose, every application that has a web page:

| Section | Items |
|---|---|
| Media | Plex, Seerr, Sonarr, Sonarr (anime), Radarr, Radarr (anime), Lidarr, Prowlarr, Bazarr, Bazarr (anime), qBittorrent, autobrr, Maintainerr, Tautulli |
| Books and reading | Calibre-Web Automated, Kavita, Koillection, FreshRSS |
| Home and tools | FileBrowser, Syncthing, The Lounge, n8n, Vikunja, Docs |
| Infrastructure | Argo CD, Authentik, Grafana, Prometheus, Loki, pgAdmin, Traefik |
| Elsewhere | ashuvidz.com, forum.ashuvidz.com |

Thirty-three entries, taken from `docs/reference/routes.md`; headless components (cross-seed,
unpackerr, flaresolverr, recyclarr, exporters) and the `Pending` applications with no route
(`minecraft-server`, `discourse` beyond its public forum) have no page and are excluded. Hosts
served over plain HTTP (`prometheus`, `loki`, `pgadmin`, `traefik`) get `http://` URLs. Icons come from the
`homarr-labs/dashboard-icons` CDN already used by the authentik blueprints, with `icon: favicon`
where no named glyph is worth pinning.

### 4. Documents

`reference/routes.md`, `reference/sso-applications.md`, `secrets.md` (the `dashy-conf` row now says
the chart owns it), and `how-it-works/applications.md` (the retired-label list drops `dashy`).

### 5. Verification

`helm template` on the chart; `helm dependency build` first because the dependency is remote;
`hack/check-exposure.sh` (expect both Dashy hosts clean); the authentik blueprint applied and
`dashy.kub.local` answering a redirect to the provider rather than the dashboard.

## Left to the builder

- Icon mapping per entry, and the section order.
- Whether the `kub.home.ashudev.com` redirect is worth keeping; the design keeps it.
- The exact `pageInfo` and `appConfig` (theme, layout, icon size, language).
