# Security

> **In one line.** Four things keep this homelab closed: only a named list of hostnames answers the
> internet, every other route is gated to the LAN, one identity provider decides who a person is, and
> no secret value is ever written into git.
>
> **Use it when** you are opening a route, wiring an app to sign-in, or working out whether something
> is exposed · **Verified** 2026-11-22 · **Kind:** explanation
>
> **Sources** `hack/check_exposure.py`, `hack/check-exposure.sh`, `docs/how-it-works/network.md`,
> `docs/how-it-works/identity.md`, `docs/secrets.md`, `kubectl/mcp-readonly-rbac.yaml`

The model is small on purpose. Every request arrives through the same router, from the same address,
whether it came from the house or from the internet, so the router cannot tell them apart: what keeps
an internal application internal is a route that says so. Everything below is a consequence of that.

**One front door.** The house router forwards 80 and 443 to one cluster address, and Traefik picks a
route by the `Host` header alone. A hostname therefore answers the internet whether or not its name
resolves publicly. The list of names allowed to answer is in `hack/check_exposure.py`, and every other
route must carry `kube-system-lan-only`.

**One gate.** `kube-system-authentik-forwardauth` asks authentik about every request and is the
fallback for an app that cannot speak OpenID Connect itself. It is a gate, not an identity: every
browser that passes it arrives as the same visitor, which is why native sign-in is preferred.

**One identity.** authentik holds one account per person and the groups that decide what it may open.
Group membership is an access decision and is deliberately not in git.

**No secret in git.** A page names a Secret, its key and where its value came from. Values live in the
cluster and in the password manager, never in a file here.

## The pages

| Page | Answers |
|---|---|
| [What enforces what](enforcement.md) | each guarantee, what holds it, and the command that proves it |
| [The critical points](critical-points.md) | the handful of things that take the homelab with them if lost, and where their copies are |
| [The security baseline, 2026-11-24](security-baseline-2026-11-24.md) | what crosses each of the six boundaries today, what that costs, and the smallest fix |
| [Secrets](../secrets.md) | every Secret a chart expects, its keys, its consumer, where the value comes from |
| [Wire an application to authentik](../runbooks/authentik-apps.md) | the procedure for native OIDC, and the traps |
| [The internal CA](../runbooks/internal-ca.md) | the `*.kub.internal` certificate, its root and the restore drill |
| [The certificate store](../runbooks/acme-store.md) | the public certificates, the resolver and pruning |

## What this section does not cover

How a request travels — the router, the middlewares, the redirect twins — is
[network.md](../how-it-works/network.md). The groups, the sign-in sources and break-glass are
[identity.md](../how-it-works/identity.md). Which hostname carries which gate, as a table, is
[reference/routes.md](../reference/routes.md), and it is generated from the charts so it cannot lag.

What to do when one of the critical points is gone is the [Backups](../backup/README.md)
section's job: recovery is a backup question, not a security one.
