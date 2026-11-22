# Traefik certificates: two Let's Encrypt orders instead of fifteen — design

**Status:** accepted · **Date:** 2026-11-22 · **Board:** homelab (certificate load)
**Live basis:** chart `42.4.1` / Traefik `3.7.19`, one `Deployment` replica on `streaming`,
store `/data/acme.json` on `pvc-traefik-nfs`.

## Intent

Cut the number of certificates Traefik orders from Let's Encrypt from fifteen to two, without
changing which names answer, who may reach them, or what a client has to trust.

The trigger was the certificate store: 34 entries on 2026-11-22, eighteen of them for hosts no route
serves. Those were pruned the same day (`docs/runbooks/acme-store.md`), leaving sixteen: fifteen
Let's Encrypt certificates and the internal `*.kub.local` leaf. Fifteen per-host certificates is
still one order every four days on average, each with its own failure mode, its own expiry, and its
own re-issue cost when a name is added.

## Current state

| | |
|---|---|
| Live Let's Encrypt hostnames | 15 — 9 public, 6 LAN-only on `*.kub.home.ashudev.com` |
| Resolver | `myresolver`, ACME TLS-ALPN challenge, `/data/acme.json` |
| Issuance | one certificate per hostname; ~60-day renewal, so ~15 orders per cycle |
| Internal names | `*.kub.local`, private ashulab CA, `local-cert`, not part of this |

Names, from `docs/reference/routes.md`:

- `ashudev.com`, `www.ashudev.com`, `dashy.ashudev.com`
- `authentik`, `filebrowser`, `freshrss`, `kavita`, `koillection`, `n8n`, `plex`, `seerr`,
  `syncthing`, `thelounge` under `kub.home.ashudev.com`
- `ashuvidz.com`, `www.ashuvidz.com`

DNS delegation decides the mechanism: `ashudev.com` answers from `ns13/dns13.ovh.net` (OVH), while
`ashuvidz.com` still answers from AWS nameservers (`ns-901.awsdns-48.net` and friends).

## Goals

1. `ashudev.com` is served by **one** certificate: `ashudev.com`, `*.ashudev.com`,
   `*.kub.home.ashudev.com` and the bare `kub.home.ashudev.com`, obtained over DNS-01 through the
   OVH API. The bare subdomain is listed separately because `*` stands for one label and the dashy
   redirect route answers on it.
2. `ashuvidz.com` is served by **one** certificate: `ashuvidz.com`, `www.ashuvidz.com`, over the
   existing TLS-ALPN challenge (its zone is not on OVH yet).
3. The OVH credential in the cluster is a dedicated consumer key limited to
   `/domain/zone/ashudev.com/{record,record/*,refresh}` — not the unscoped Terraform key.
4. No route, gate, entry point or TLS option changes.
5. The old per-host certificates leave the store after the new ones are proven.

## Non-goals

- Moving `ashuvidz.com` to OVH, or converting its certificate to a wildcard. The zone move is its own
  change; when it lands, the resolver swap is one line.
- `*.kub.local` and the internal CA. It has no public role and no Let's Encrypt name.
- Any change to the public allowlist or the middlewares.

## Anti-goals

- **A private certificate for `*.kub.home.ashudev.com`.** It was considered and rejected: TVs and
  guest laptops cannot install the ashulab root, and those names are exactly the ones household
  devices open. Keeping a publicly trusted chain is the point.
- **Wildcard-only for everything.** `*.ashudev.com` does not cover `ashudev.com` and does not cover
  `kub.home.ashudev.com` (two labels), so the apex and the nested wildcard must be named explicitly
  in the same certificate. One certificate, three names.
- **`tls.domains` repeated on every route.** One mismatch between two routes creates a second
  certificate — the failure this design removes. The name list lives in one place per domain.

## Approaches considered

**A — DNS-01 wildcard for `ashudev.com`, SAN pair for `ashuvidz.com` (chosen).** 15 → 2 orders.
DNS-01 is the only challenge type Let's Encrypt accepts for a wildcard. `ashuvidz.com` stays on
TLS-ALPN until its delegation moves, with two names in one certificate via `tls.domains`.

**B — SAN grouping on every domain, no DNS credentials.** 15 → 3–4 orders, no new Secret, no OVH
scope. Rejected: it gives most of the benefit but keeps every new hostname a certificate change,
and the SAN list has to stay byte-identical across a dozen routes.

**C — move the six LAN-only names to the internal CA.** 15 → 9 orders. Rejected: it requires the
ashulab root on every client that opens Kavita, FreshRSS or The Lounge, including household devices.

## How it works

### The two resolvers

`values-v3.yaml` gains a second resolver beside the existing one. Both write to the same store; the
file is keyed by resolver name.

```yaml
envFrom:
  - secretRef:
      name: traefik-ovh
env:
  - name: OVH_PROPAGATION_TIMEOUT
    value: "300"
  - name: OVH_POLLING_INTERVAL
    value: "10"
certificatesResolvers:
  myresolver:                 # unchanged: TLS-ALPN, ashuvidz.com
    acme:
      email: valentin.ronteix@ashudev.com
      tlsChallenge: true
      storage: /data/acme.json
  ovhresolver:                # new: DNS-01, ashudev.com
    acme:
      email: valentin.ronteix@ashudev.com
      storage: /data/acme.json
      dnsChallenge:
        provider: ovh
        delayBeforeCheck: 60
```

The Secret `kube-system/traefik-ovh` carries `OVH_ENDPOINT`, `OVH_APPLICATION_KEY`,
`OVH_APPLICATION_SECRET`, `OVH_CONSUMER_KEY`. The consumer key is new and scoped; the application
key is the existing one. `OVH_PROPAGATION_TIMEOUT` and `OVH_POLLING_INTERVAL` are plain config, not
secret, so they live in `env`.

### Where the certificate request lives

One anchor route per domain, on the route that already serves the apex. It is the only route in its
domain that keeps `certResolver`; it also carries the whole name list:

```yaml
tls:
  certResolver: ovhresolver            # myresolver for ashuvidz
  domains:
    - main: ashudev.com
      sans:
        - "*.ashudev.com"
        - "*.kub.home.ashudev.com"
        - "kub.home.ashudev.com"   # the bare subdomain, which `*` does not cover
```

Every other route drops `certResolver` and keeps only its `tls.options`. Traefik serves those routes
from the certificate store by SNI, exactly as `*.kub.local` routes serve the default certificate
today. `www` drops the resolver for the same reason: it is covered by `*.ashudev.com`.

The anchor is the route `Host(`ashudev.com`)` in `cluster/helm/ashudev-website`, and `Host(
`ashuvidz.com`)` in `cluster/helm/ashuvidz-website`. Both charts already own the apex and its `www`
twin; the template gains a `domains` list and the `www` router stops rendering a resolver.

### What happens on the first upgrade

Traefik reloads, sees the anchor, and places two ACME orders. Each DNS-01 order writes a TXT record at
`_acme-challenge.ashudev.com` / `_acme-challenge.kub.home.ashudev.com`, refreshes the zone, waits,
validates, and deletes the record. TLS-ALPN for `ashuvidz.com` is unchanged in mechanism. The old
fifteen certificates remain valid and in the store; nothing goes down while the new ones issue. They
are pruned afterwards, once every name is proven to answer with the new certificate.

### Authorisation scope

The consumer key can only POST a record, POST a zone refresh and DELETE a record under
`ashudev.com`; it cannot read the zone, change delegation, touch storage, or reach another domain. It
is created with the existing key and validated once by the operator in a browser. A key that is
validated has an expiry date; renewing or replacing it is a Secret update and a pod restart, recorded
in `docs/secrets.md`.

## What changes

| File | Change |
|---|---|
| `cluster/infrastructure/traefik/values-v3.yaml` | second resolver, `envFrom`, propagation env |
| `cluster/helm/ashudev-website/{values.yaml,templates/ingressroute.yaml}` | anchor domains, `www` without resolver |
| `cluster/helm/ashuvidz-website/{values.yaml,templates/ingressroute.yaml}` | anchor domains (apex + `www`), `www` without resolver |
| `cluster/helm/{authentik,dashy,filebrowser,freshrss,kavita,koillection,n8n,plex,seerr,syncthing,thelounge}` | resolver removed from values or template |
| `docs/reference/routes.md` | TLS column says wildcard / SAN pair |
| `docs/how-it-works/network.md` | TLS section names the two certificates |
| `docs/runbooks/acme-store.md` | second prune entry, wildcard explanation |
| `docs/secrets.md` | `traefik-ovh` Secret and its four keys |
| `CHANGELOG.md` | one `Changed` entry |

## Verification

- `hack/check-charts.sh` (lint + render), `hack/check-app-version.sh`, `hack/check-exposure.sh`,
  `hack/check-config-keys.sh`, and `python -m pytest -q hack/tests`.
- Rendered output: exactly one route carries `certResolver: ovhresolver` with the three names, one
  carries `myresolver` with the two `ashuvidz.com` names, and no other route carries a resolver.
- After the live upgrade: `traefik_tls_certs_not_after` drops to 3 series (wildcard, SAN pair,
  internal); every one of the 15 names completes a handshake with its own name in
  `subjectAltName`; no ACME error in the router log; the wildcard's expiry is ~90 days out.
- After the prune: 3 entries in `acme.json`, byte-identical across a restart, and the removed names
  no longer renew.

## Risks and rollback

- **OVH propagation.** The zone is refreshed by the API and the challenge waits 60 s, with a 300 s
  propagation budget. A first attempt that times out is retried; Let's Encrypt allows five failed
  validations per hostname per hour.
- **One certificate for 13 names.** A renewal failure affects all of them at once, but renewal starts
  30 days before expiry and retries daily, so there is a month of margin. The private key is one file,
  mode `0600`, on the Retain NFS claim.
- **Rollback** is the old values file: `helm upgrade` back, the per-host routes regain `certResolver`
  and re-issue 15 certificates. The wildcard stays in the store as an unused entry until pruned, and
  does not block anything.
- The internal CA is untouched, so no client trust changes.

## Left to the builder

- Exact `domains` rendering in the two website templates (list of `main`/`sans`, matching the chart's
  existing `tls` block style).
- Whether the `www` router renders `tls: {}` or omits the block; either serves from the store.
- Whether the ashuvidz anchor names `www.ashuvidz.com` in `sans` or relies on the `www` router; the
  SAN is explicit, so the router can stay resolver-free.
