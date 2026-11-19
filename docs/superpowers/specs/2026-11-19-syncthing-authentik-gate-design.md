# syncthing behind the authentik gate, with no local login

> **Short design.** The operator asked on 2026-11-19 for the Syncthing WebUI to be protected by
> authentik only — no Syncthing login underneath — and picked the one-click switch over a
> chart-declared config edit.

## Why

`syncthing.kub.home.ashudev.com` is reachable by anything on the LAN, and the only protection is the
network: `lan-only` and nothing else. Syncthing's own GUI password would be the second layer, but it
is a shared secret nobody rotates, and the app has no native single sign-on (an upstream request
exists; nothing is shipped). Every other non-public app in the cluster is at least as protected as
its neighbours, and Syncthing is not.

## Goals

- A signed-out browser never reaches the WebUI; it is sent to authentik.
- Only the `authentik Admins` group passes; another authentik user gets 403.
- Syncthing itself asks for no password once the gate is in place, so the browser sees one prompt.
- The sync ports are untouched: 22000 TCP/UDP and 21027 UDP carry no HTTP and are not gated.

## Non-goals

- Native OIDC in Syncthing (it does not exist).
- Gating the sync protocol, the discovery protocol or the device pairing flow.
- Per-user identity inside Syncthing: the app has one config and one owner.

## Anti-goals — what would make this a failure even if it works

- A route that returns 200 to a signed-out browser.
- A gate that leaves the app unreachable when authentik is healthy — for example because the
  middleware order lets `lan-only` answer first and mask it, or because a WebSocket upgrade is
  refused.
- Removing the app login before the gate is proven, which would leave the WebUI open to anything that
  can reach port 8384.

## Constraints

- **Middleware order:** `lan-only` first, then `authentik-forwardauth`, matching qBittorrent.
- **The app login is the only access control below the gate.** With it gone, any process that can
  reach port 8384 inside the cluster has full admin. Today that is only the Traefik pods: the
  Service is ClusterIP and nothing else in the cluster addresses that host.
- **The switch is runtime state, not git.** `insecureAdminAccess` and the cleared credentials live in
  `/config/config.xml` on the `syncthing-config` claim. A rebuilt claim starts with no login and
  no switch again, so the step is written into the chart NOTES and README.
- **The GUI host check.** Traefik preserves the `Host` header and the route already answers today, so
  `insecureSkipHostcheck` is not needed. If the gate's redirect ever produces a host-mismatch page,
  that is the knob, and it gets a comment in the values.

## Approaches

1. **Gate at the edge; set the switch once in the GUI.** *(chosen)* Least machinery: one blueprint,
   one middleware list entry, one click. The cost is a documented repeat on a fresh claim, which is
   the same cost qBittorrent already carries for its auth-bypass preferences.
2. **Gate at the edge; an init container patches `config.xml`.** Declarative and survives a new
   claim, but it mutates the file the device identity lives in, needs a pinned tool image, and one
   bad run costs the identity and folder definitions. Rejected as more risk than the click it saves.
3. **Gate plus Syncthing's own password** (the qBittorrent shape). Rejected: the operator asked for
   one prompt, and a shared secret nobody rotates is the layer this change is meant to replace.

## How it is built

1. `cluster/helm/authentik/files/app-syncthing.yaml` — a `forward_single` proxy provider for
   `https://syncthing.kub.home.ashudev.com`, an application with the Syncthing icon, and a policy
   binding to `authentik Admins`, copying `app-qbittorrent.yaml`.
2. `cluster/helm/syncthing/values.yaml` — a `middlewares` list under `app.ingress`, and the TLS
   router reads it instead of hardcoding. TLS router: `lan-only`, `authentik-forwardauth`.
   The plain-HTTP router keeps its hardcoded `lan-only` and the redirect: adding a gate there would
   only add a round trip before the redirect.
3. `cluster/helm/authentik/files/outpost-embedded.yaml` — the provider added to the outpost's
   `providers` list and the blueprint to its `metaapplyblueprint` list. Without both, authentik
   creates the provider and the outpost ignores it, so the host answers **404** while authentik is
   healthy.
4. Documentation: the routes table row, the SSO matrix row, the design paragraph in the
   media-tools security page if it carries one, and the chart README section on what breaks if the
   gate is removed.

## What the operator does, once

Order matters — the gate is proven before the login is removed.

1. Merge, let ArgoCD apply the blueprint and the route.
2. In a browser, confirm a signed-out visit lands on authentik, that an admin gets the WebUI, and
   that a non-admin gets 403.
3. Then, in the WebUI: **Settings → GUI**, clear the user and password, enable **Insecure Admin
   Access**, save. The page reloads without a prompt behind authentik.
4. Confirm the port-forward path still works as break-glass: `kubectl -n syncthing port-forward
   svc/syncthing 8384:8384` reaches the GUI with no login, which is acceptable because `kubectl`
   access is already cluster admin.

## Break-glass and rollback

- **authentik down:** the route answers an error; the app keeps syncing (the sync protocol does not
  cross the gate). Recovery is port-forward, then fix authentik.
- **Wrong gate:** remove `authentik-forwardauth` from the values list and merge; the route falls back
  to `lan-only`. If the login was already cleared, the WebUI is then open to the LAN — so re-set a
  password first, or accept it for the minutes it takes to roll the fix.

## Verification

```bash
helm template test cluster/helm/syncthing | grep -A6 'kind: IngressRoute'
./hack/check-exposure.sh          # 68 routes, the syncthing row now carries a gate
```

Live: signed-out 302 to authentik, admin 200, non-admin 403, a file syncs while gated, and the
WebUI asks for nothing after the authentik sign-in.
