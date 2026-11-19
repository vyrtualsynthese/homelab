# Dashy on a public address, still behind the gate

> **Card** 254 · **Branch** `feature/dashy-public` · **Written** 2026-11-19 · **Size:** small change

## Intent

The start page should answer from anywhere at `https://dashy.ashudev.com`, protected by the authentik
gate and limited to `authentik Admins` as it is today. The other Dashy addresses — `dashy.kub.local`
and `kub.home.ashudev.com` — stop being separate doors and only redirect to that one address.

This replaces the 2026-11-18 decision in `2026-11-18-dashy-repair-design.md`, which chose to keep
Dashy internal and drop the public name. The rest of that design stands: the config lives in
`cluster/helm/dashy/files/conf.yml`, the chart owns the Secret, the image is pinned.

## What exists today

| Piece | State in git |
|---|---|
| `dashy.ashudev.com` | no router at all; the name is unresolvable outside the LAN by design |
| `dashy.kub.local` | `websecure` (lan-only, authentik-forwardauth, security, internal certificate) and a plain-HTTP mirror |
| `kub.home.ashudev.com` | `web` and `websecure` routers, both lan-only, both redirecting to `https://dashy.kub.local` |
| authentik blueprint | proxy provider `dashy`, `external_host: https://dashy.kub.local`, bound to `authentik Admins` |
| authentik route | `authentik.kub.home.ashudev.com` carries `lan-only` on both routers |

The live object still carries `kube-system/authentik` and `security` with no `lan-only`: the
Application is OutOfSync, so a sync is the first thing this change lands.

## Goals

- `https://dashy.ashudev.com` serves the dashboard behind the forward-auth gate, admins only.
- `dashy.kub.local` and `kub.home.ashudev.com` redirect to it, over TLS, from the LAN.
- The browser can finish the sign-in from the internet: the authentik sign-in page answers public
  requests.
- `hack/check-exposure.sh` passes, and `docs/reference/routes.md` describes the result.

## Non-goals

- Any application hostname other than Dashy's own. The ~32 entries in `conf.yml` keep their current
  routes, gates and certificates. The operator asked for the Dashy addresses only.
- Widening the page past `authentik Admins`.
- Widgets or API keys in `conf.yml`.

## Anti-goals

- A public route whose gate is `kube-system-authentik` (headers that authenticate nothing).
- A public address that dead-ends at a sign-in page the internet cannot reach.
- A gate removed to "make it work".

## Constraints

- Traefik v2 CRDs only (`traefik.containo.us/v1alpha1`); no `networking.k8s.io/v1` Ingress.
- `hack/check_exposure.py` fails a route that gates a public host and fails a public host that is not
  on its allowlist, so both the route and the allowlist move together.
- Public names use `certResolver: myresolver`; internal names use the default `kube-system/local-cert`
  with the `internal-tls` TLSOption.
- The authentik proxy provider's `external_host` must match the browser's scheme and host, because the
  outpost sets the session cookie's `Secure` flag and builds its redirects from it.
- authentik builds every redirect from one base URL, `AUTHENTIK_WEB__BASE_URL`; there is no second
  public name for the outpost without changing that URL and re-checking every application.

## Approaches

**A — open the authentik route (chosen).** Drop `lan-only` from `authentik.kub.home.ashudev.com` and
keep `security`. One line per router. The sign-in page of a public page has to be public; this is the
shape every forward-auth deployment has. Cost, accepted by the operator: the sign-in page and its
flows answer the internet from now on.

**B — a second public name for the outpost.** Rejected on fact: authentik's redirects all come from
the single base URL above, so a second name means changing it and re-verifying every application's
issuer and redirect URI, with no extra reach.

**C — card 254 as written, internal only.** Rejected by the operator: it is the opposite of the
request. Recorded because it is what the previous design chose.

## The design

### 1. `cluster/helm/dashy` routes

Four routers become six, all on `traefik.containo.us/v1alpha1`:

| Router | Entry point | Host | Middlewares | TLS |
|---|---|---|---|---|
| `dashy-public` | `websecure` | `dashy.ashudev.com` | `authentik-forwardauth`, `security` | `certResolver: myresolver` |
| `dashy-public-redirect` | `web` | `dashy.ashudev.com` | `redirect-https` | — |
| `dashy-local` | `websecure` | `dashy.kub.local` | `lan-only`, `redirect-to-dashy` | `internal-tls` |
| `dashy-local-redirect` | `web` | `dashy.kub.local` | `lan-only`, `redirect-to-dashy` | — |
| `kub-home` | `websecure` | `kub.home.ashudev.com` | `lan-only`, `redirect-to-dashy` | `certResolver: myresolver` |
| `kub-home-redirect` | `web` | `kub.home.ashudev.com` | `lan-only`, `redirect-to-dashy` | — |

- No `lan-only` on the public routers: the checker treats gating a public host as a violation, and
  the gate is the forward-auth middleware.
- The redirect routers point at the no-op service (`noop@internal`), so nothing is proxied.
- `redirect-to-dashy` matches both retired hosts in one regex and replaces the host, keeping the path:
  `^https?://(?:dashy\.kub\.local|kub\.home\.ashudev\.com)/(.*)` → `https://dashy.ashudev.com/$1`,
  `permanent: true`.
- `dashy.kub.local` keeps TLS on `websecure` (a browser has to finish the handshake before it can be
  redirected), so the internal certificate and `internal-tls` option stay on that router.

### 2. Identity

- `cluster/helm/authentik/files/app-dashy.yaml`: `external_host` and `meta_launch_url` become
  `https://dashy.ashudev.com`. Mode stays `forward_single`; the policy binding stays
  `authentik Admins`.
- `cluster/helm/authentik/files/outpost-embedded.yaml` needs no edit: it already lists
  `ashulab-app-dashy`, and the blueprint name does not change.

### 3. authentik route

- `cluster/helm/authentik/templates/ingressroute.yaml`: remove `kube-system-lan-only@kubernetescrd`
  from both routers on `authentik.kub.home.ashudev.com`. Keep `security` and `authentik-tlsoption` on
  `websecure`, and `redirect-https` on `web`.

### 4. Exposure allowlist

- `hack/check_exposure.py`: add `dashy.ashudev.com` and `authentik.kub.home.ashudev.com` to
  `PUBLIC_HOSTS`, with a comment saying why the identity provider is public.
- Its unit tests read fixtures, not the real charts; add a fixture only if a new rule is introduced
  (none is).

### 5. Documents

- `docs/reference/routes.md`: move Dashy and authentik out of the internal table into Public, and
  rewrite the redirect note (`kub.home.ashudev.com` and `dashy.kub.local` now redirect to
  `dashy.ashudev.com`).
- `docs/reference/sso-applications.md`: Dashy's host column becomes `dashy.ashudev.com`.
- `AGENTS.md` §4: the public list becomes `plex`, `seerr`, `ashuvidz.com`, `dashy.ashudev.com` and
  `authentik.kub.home.ashudev.com` (the sign-in page for the gate).
- `docs/status.md`: the Dashy row records the public address.

### 6. Verification

- `helm template` on `cluster/helm/dashy` and `cluster/helm/authentik` (the latter needs
  `helm dependency build` first).
- `hack/check-exposure.sh` exits 0.
- From the LAN: `curl -sI --resolve dashy.ashudev.com:443:<public IP>` answers a redirect to the
  authentik outpost, not the dashboard.
- On the LAN, `dashy.kub.local` and `kub.home.ashudev.com` answer `301` to `https://dashy.ashudev.com/`.
- Signed in as an admin, the dashboard loads from the public address; signed in as a non-admin, it is
  refused.
- A request from off the LAN (operator's phone on mobile data) reaches the sign-in page and the
  dashboard, which is the whole point.

## Left to the builder

- The exact regex escaping and router names, against the CRD schema.
- Whether the plain-HTTP routers keep `redirect-to-dashy` directly or bounce through `redirect-https`;
  direct is one hop fewer.
- The wording of the comment that explains why `authentik.kub.home.ashudev.com` is on the public list.

## Risks

- The authentik sign-in page becomes internet-facing. authentik has its own rate limiting and flow
  policies; nothing in this repo loosens them. If abuse appears, the next step is authentik's
  brute-force stage settings, not a firewall rule that would break the gate.
- The live Application is OutOfSync. After the merge, a sync is what closes card 254's original gap;
  until it runs, the live route keeps the old middleware list.
