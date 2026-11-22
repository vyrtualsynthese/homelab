# What enforces what

> **In one line.** Every guarantee this homelab claims has something holding it up and a command that
> shows whether it still holds — and the three that have no command say so.
>
> **Use it when** you want to check a claim rather than trust it, or you are changing something a
> guarantee rests on · **Verified** 2026-11-22 · **Kind:** reference
>
> **Sources** `hack/check_exposure.py`, `hack/check-exposure.sh`, `hack/check-app-version.sh`,
> `hack/check_docs.py`, `kubectl/mcp-readonly-rbac.yaml`, `docs/how-it-works/network.md`,
> `docs/reference/sso-applications.md`, `docs/decisions/2026-11-21-sovereign-eu-providers-only.md`

Run the commands from the repository root on the operator's machine, with the normal kubeconfig.
Each one prints nothing, or a short list, when the guarantee holds.

| Guarantee | What holds it | The command that proves it |
|---|---|---|
| Only the allowlist answers the internet | `PUBLIC_HOSTS` in `hack/check_exposure.py`, checked against every chart's rendered routes | `hack/check-exposure.sh` |
| Every non-public route is gated to the LAN | the same checker: a non-public host must carry `kube-system-lan-only` | `hack/check-exposure.sh` |
| The public list is the one we think it is | the same file, one list, and the generated [routes table](../reference/routes.md) | `grep -n "PUBLIC_HOSTS" -A 25 hack/check_exposure.py` |
| One gate, and it is authentik | `kube-system-authentik-forwardauth`, applied by the `traefik-gate` chart | `kubectl get middleware -n kube-system` |
| Internal names carry the internal CA | the default `kube-system/local-cert` and the `internal-tls` TLSOption | `kubectl get tlsstore,tlsoption -n kube-system` |
| Public names carry Let's Encrypt | the two resolvers of the certificate store, and the apex routes that request them | `kubectl get certificate,secret -n kube-system \| grep -c kubernetes-tls` |
| No secret value is written in a page | `secret_problems` in `hack/check_docs.py` | `python3 hack/check_docs.py --only secrets` |
| Every image is pinned to a version | the `image.tag` in each chart, and the `appVersion` beside it | `hack/check-app-version.sh` |
| Agents read the cluster and cannot write it | the `view` role plus read-only objects, bound to `kube-system/opencode-mcp` | `kubectl auth can-i --list --as=system:serviceaccount:kube-system:opencode-mcp -n kube-system` |
| The docs cannot drift from their sources | `hack/check-docs.sh` | `hack/check-docs.sh --base origin/develop` |

## The claims with no command

Three guarantees are held by a decision and a habit, not by a script. Saying so is the point of this
page: a claim with no proof is one to re-read, not one to trust.

- **Providers are European and in the EU.** OVHcloud, Scaleway and the rest were chosen by
  [decision](../decisions/2026-11-21-sovereign-eu-providers-only.md), and nothing checks that a new
  service is inside that rule. Adding an account is a review, and the record names what is still open.
- **The secrets inventory is complete.** [`secrets.md`](../secrets.md) is compiled from the charts,
  and the read-only account cannot read Secrets, so a chart that grows a new key is caught by a person
  reading the diff, not by a check. A missing Secret shows as `CreateContainerConfigError`.
- **Group membership matches who should have access.** authentik holds it and git does not, by design.
  Reviewed when someone joins or leaves, and at no other time.

## What is not a control

- `kube-system-authentik` is a headers middleware, not a gate. A route naming it and nothing else is
  protected by `lan-only` and no user check.
- `lan-only` alone is a network boundary, not authentication: it trusts the router's address. The
  charts that use it say why beside the middleware list, and `maintainerr`'s `/api` exemption is the
  thinnest case in the repository.
- The Traefik dashboard's basic-auth is a password on one route; the file that holds it is not in git.

The request path, the redirect twins and the `/api` exemptions are
[network.md](../how-it-works/network.md); who signs in where is
[the SSO matrix](../reference/sso-applications.md).
