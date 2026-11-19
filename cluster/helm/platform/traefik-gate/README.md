# traefik-gate

One Traefik `Middleware`, `kube-system/lan-only`, that rejects a request with `403` when the
connecting address is outside the LAN or the WireGuard tunnel. It is the single gate every
non-public route in the cluster references.

## Why it exists

The home router forwards TCP 80 and 443 to the cluster's Traefik load balancer, and Traefik picks a
route by the `Host` header alone. The `*.kub.home.ashudev.com` zone answers the public address from
authoritative DNS, so those hostnames reach the cluster from the internet by name. A route without
this middleware is therefore a door left open, whether or not its hostname looks internal.

The public hostnames are exactly three, and they do not carry this gate:
`plex.kub.home.ashudev.com`, `seerr.kub.home.ashudev.com`, `ashuvidz.com`.

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
non-public route lacks the gate or a public route carries it.

## The trap this chart exists to avoid repeating

The CRD installed on this cluster (`middlewares.traefik.containo.us` v1alpha1) declares
`ipWhiteList`. Traefik v3 renamed it to `ipAllowList`, and the API server **silently prunes a field
it does not know** — the object is created with an empty `spec`, the allowlist enforces nothing, and
nothing reports an error. That happened once already, in the pgAdmin chart.

So: verify the live object after every change (`spec.ipWhiteList.sourceRange` must hold two
entries), and rename the field in this template and the CRDs together in W5, never before.
