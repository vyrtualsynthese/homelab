# traefik-gate

The cluster's shared Traefik objects for internal names. `lan-only` rejects a request with `403` when
the connecting address is outside the LAN or the WireGuard tunnel, and it is the single gate every
non-public route in the cluster references. `redirect-to-https` and the `internal-tls` policy are the
scheme redirect and the TLS settings each serving chart points at. `legacy-internal-redirect` and
`legacy-internal-redirect-tls` are the two routers that keep an old `*.kub.local` name alive: they
send it to its `*.kub.internal` replacement, through `redirect-to-internal-tls` for a name that serves
TLS and through `redirect-to-internal-plain` for `pgadmin` and `prometheus`, which have no
`websecure` router.

## Why it exists

The home router forwards TCP 80 and 443 to the cluster's Traefik load balancer, and Traefik picks a
route by the `Host` header alone. The `*.kub.home.ashudev.com` zone answers the public address from
authoritative DNS, so those hostnames reach the cluster from the internet by name. A route without
this middleware is therefore a door left open, whether or not its hostname looks internal.

The public hostnames are exactly five, and they do not carry this gate:
`plex.kub.home.ashudev.com`, `seerr.kub.home.ashudev.com`, `ashuvidz.com`, `dashy.ashudev.com`
(behind `authentik-forwardauth` instead) and `authentik.kub.home.ashudev.com` (the sign-in page of
the one before it). `hack/check_exposure.py`'s `PUBLIC_HOSTS` is the authority; this list restates it.

## The two ranges

| Value | Range | Where it comes from |
|---|---|---|
| `lanOnly.lanRange` | `192.168.1.0/24` | The four nodes are `192.168.1.27/.41/.46/.48`. Fixed. |
| `lanOnly.wireguardRange` | `10.8.0.0/24` | Read from wg-easy on `rbp0`: `docker exec wg-easy grep -i address /etc/wireguard/wg0.conf`. An address of `10.8.0.1/24` means the range is `10.8.0.0/24`. |

`wireguardRange` is empty in git on purpose: the template's `required` guard fails the render until
the operator fills it in, because guessing it either locks out remote access or leaves a hole.

## How a route uses it

Charts with a values-driven middleware list:

```yaml
  middlewares:
    - name: lan-only
      namespace: kube-system
```

That renders `kube-system-lan-only@kubernetescrd`. Charts with a core `Ingress` prepend the same
reference to their `traefik.ingress.kubernetes.io/router.middlewares` annotation. A bare `lan-only`
from another namespace does nothing, which is why the full reference is the only accepted form.

`hack/check-exposure.sh` enforces it: it renders every chart, reads every route, and fails when a
non-public route lacks the gate or a public route carries it. It also rejects the old
`ipWhiteList` field name, so a chart cannot drift back.

## The trap this chart exists to avoid repeating

The API server **silently prunes a field it does not know**: the object is created with an empty
`spec`, the allowlist enforces nothing, and nothing reports an error. That happened once already, in
the pgAdmin chart: the template wrote `ipAllowList` against a 2.9 CRD that declared only
`ipWhiteList`.

The field here is `ipAllowList`, the v3 name. It works because chart `27.0.2` was installed on
2026-11-21 — its CRDs declare both names in both API groups — and that install came before this
rename, never after it.

So: verify the live object after every change (`spec.ipAllowList.sourceRange` must hold all three
ranges), and check the installed CRD before renaming a field in this template.
