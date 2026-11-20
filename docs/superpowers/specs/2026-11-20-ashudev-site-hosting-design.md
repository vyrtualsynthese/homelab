# Move `ashudev.com` off GitHub Pages — design

**Date:** 2026-11-20
**Status:** awaiting approval
**Scope:** hosting only. The site's content, template and webpack build are untouched.

## Intent

`ashudev.com` is served by GitHub Pages today: the repository `ashudev-website` publishes the
`gh-pages` branch, and four apex `A` records in `infra-main/dns/ashudev.tf` point at GitHub's
addresses. The operator wants the site served from infrastructure they control.

Nothing about the site itself is wrong. It is a folder of finished files — `index.html`, one CSS
bundle, six JavaScript bundles and an `assets/` tree — because the webpack build writes `gh-pages`
and Pages serves it without a build of its own. The move is therefore a change of server, not a
change of site.

## Approaches considered

1. **A third nginx chart in this cluster** — the pattern `ashuvidz-website` and `docs-site` already
   use. Chosen.
2. **OVH Object Storage static hosting** — keeps the site off the house and stays with the provider
   the DNS just moved to. Rejected for this pass: the deploy becomes a bucket upload beside the
   charts, and a certificate on a custom domain there is an extra product to verify. It remains the
   fallback if home-hosted uptime turns out to be the wrong trade.
3. **Stay on Pages** — zero work, keeps working with OVH DNS. Rejected by the operator.

**Cloudflare Pages and Netlify were dropped on a technical fact, not a preference:** an apex domain
cannot be a `CNAME`, OVH's zone has no flattened record type, so either would have pulled DNS back
off OVH and undone the migration that finished today.

## What changes

### In this repository

A chart at `cluster/helm/ashudev-website`, shaped from `ashuvidz-website` (nginx-unprivileged, non-root,
content cloned by an init container and pulled by a sidecar every 60 s, `nodeSelector` on `nuc0`):

- `content.repository: https://github.com/vyrtualsynthese/ashudev-website.git`
- `content.ref: gh-pages` — the branch Pages publishes, so the bytes served are the bytes served
  today, and the cluster never runs a build.
- Two routers:
  - `ashudev.com` → nginx, `tls.certResolver: myresolver`.
  - `www.ashudev.com` → a `redirectRegex` middleware to `https://ashudev.com/<path>`, permanent.
    The router needs its own entry so Traefik requests a certificate for the `www` name; a `www`
    that resolves but has no router is the defect described under "Found on the way".
- An Application at `cluster/argocd/projects/ashudev.yaml` (CRLF, `CreateNamespace: true`), named
  `ashudev` so ArgoCD's release name stays short and `app.fullname` is `ashudev-nginx`.
- `hack/check_exposure.py`: `ashudev.com` and `www.ashudev.com` join `PUBLIC_HOSTS` — the site is
  meant to answer the internet, so the gate must not be the LAN.
- `CHANGELOG.md` entry.

### In `infra-main`, one pull request

In `infra-main/dns/ashudev.tf`:

- The four apex `A` records collapse to one: `80.14.57.209`, the cluster's public address.
- The four apex `AAAA` records are **deleted**, not repointed: `plex.kub.home.ashudev.com` has no
  `AAAA`, so the home connection has no usable IPv6, and a `AAAA` pointing nowhere is worse than
  none.
- `www` becomes a `CNAME` to `ashudev.com.` instead of `vyrtualsynthese.github.io.`.

No `MX`, `TXT`, `SPF` or `DKIM` record is touched. Apex `A`/`AAAA` and `MX` are different record
types on the same name; Google Workspace keeps receiving mail throughout, which is the one
constraint that cannot be traded.

## Order, and why

1. Merge the chart. The route and its certificate request exist; nothing resolves to them yet, so
   the live site does not change.
2. Merge the DNS change. Traefik's resolver uses the `TLS-ALPN-01` challenge (`tlsChallenge: true` in
   `traefik-config.yaml`), which needs the name to point at the cluster before a certificate can be
   issued. The first HTTPS request after the flip fails until the certificate lands, a matter of
   seconds; HTTP answers immediately.
3. Verify: `curl --resolve ashudev.com:443:80.14.57.209 https://ashudev.com/` returns 200 with a
   valid certificate, `www` redirects with 301, mail still delivers, then the same three checks
   without `--resolve`.
4. Keep Pages published for a week. Rolling back is the reverse DNS change, which is why the flip is
   its own commit.

## Found on the way, to be filed separately

- `www.ashuvidz.com` has an `A` record to `80.14.57.209` but no router and no certificate: it
  answers with `SSL: no alternative certificate subject name matches target host name`. The `www`
  name of that site is broken today.
- `ashuvidz.com` still carries the four GitHub Pages `AAAA` records, so an IPv6 client reaches
  GitHub while an IPv4 client reaches the cluster.

## Out of scope

- The site's content, its 87 MB of template assets, the webpack build, the missing `404.html`.
- Disabling Pages, which happens after the rollback week.
- `ashuvidz.com`'s two defects above.
