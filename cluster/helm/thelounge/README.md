# thelounge

The Lounge, a web IRC client that keeps its connections to the networks while no browser is open.

Built against the contract in [`docs/charts/README.md`](../../../docs/charts/README.md); the reference
shape is [`cluster/helm/media-stack/autobrr`](../media-stack/autobrr).

## What it deploys

| Object | Name | Notes |
|---|---|---|
| Deployment | `thelounge` | one replica, `Recreate`, pinned to `nuc0` |
| Service | `thelounge` | one port, `9000` |
| PersistentVolumeClaim | `thelounge-config` | `local-path`, `128Mi` |
| ConfigMap | `thelounge` | `config.js`; an init container copies it into the claim as UID 1000 |
| ServiceAccount | `thelounge` | its token is not mounted; the app never calls the API |
| IngressRoute | `thelounge-public` | `websecure`, `lan-only` then `security` |
| IngressRoute | `thelounge-public-redirect` | `web`, `lan-only` then the HTTPS redirect |
| TLSOption | `thelounge-tlsoption` | unchanged from the previous chart |

The `-public` suffix on both routes is historical. Neither route answers the internet: `lan-only` is
the first middleware on both.

## The claim is new, so the state is empty

The chart used to mount `thelounge-volume`, a `longhorn-static` claim that has been `Terminating`
since the Longhorn failure on 2026-08-28. Its contents — the user database and every configured
network — were abandoned by decision on 2026-11-13 and are **not migrated**. This chart creates
`thelounge-config` instead.

The old claim and its volume are cleared by hand after the first sync; the loop is in
[`docs/cleanup/longhorn-disposition.md`](../../../docs/cleanup/longhorn-disposition.md). Deleting
them is what makes the card done, and it cannot happen before the pod that holds the mount is gone.

After the sync, re-create the accounts and the networks in The Lounge, then restart the pod once and
confirm they survive it. That restart is the test: everything The Lounge keeps lives in
`/var/opt/thelounge`, which is the claim.

## Signing in

The Lounge has no OIDC support, so there is nothing to wire through a proxy provider. Its one built-in
identity mechanism is LDAP, and the cluster now runs authentik's LDAP outpost for it:
`authentik-ldap` in namespace `authentik`, port `3389`, created by
`cluster/helm/authentik/files/app-thelounge-ldap.yaml` and
`cluster/helm/authentik/templates/ldap-outpost.yaml`.

`config.js` binds directly as the person — `baseDN` with `primaryKey: "cn"` and no `searchDN` — so
authentik validates their own password and the application binding decides who may sign in. The route
therefore carries `lan-only` and `security` only: the forward-auth gate is gone, because a gate in
front of an app that authenticates is a second prompt that carries no identity.

Two consequences worth knowing:

- **The local passwords stop working.** The LDAP plugin replaces the local login; accounts, networks
  and history are keyed by username and survive, but each person signs in with their authentik
  password.
- **MFA is appended to the password.** No device is enrolled today and the provider's `mfa_support`
  is off, which is the only state that works while nothing is enrolled. When somebody enrols, flip
  `mfa_support` on the provider in the same change, and they type `password;123456`.

## Probes and the missing log level

The image has no health endpoint. `/` answers the login page with a 200 whether or not an IRC network
is reachable, so it is not a health signal. Liveness is a TCP connect on the web port — the process is
either listening or it is not.

Readiness is off: no endpoint reports the state of the upstream IRC connections, and a TCP check
would ask the same question as liveness, so it would only delay a restart. The single replica runs
`Recreate`, so there is no traffic-draining step a readiness probe could gate.

There is no log-level variable either. Output follows the `debug` object in `config.js` (both keys
off), so the container prints warnings and errors, and per-network history is a file in the claim.
Recorded here so the next reader does not look for a knob that does not exist.

## Configuration

`config.js` is a file the app reads at startup, so its source is a ConfigMap. It is not mounted over
the app's home: an init container copies it into the claim as UID 1000 before the app starts. The
reason is The Lounge's own check — `verifyFileOwner()` compares the owner of `<home>/config.js` with
the UID the process runs as and warns when they differ, and every file a ConfigMap volume provides is
owned by root. The copy is what keeps the file in git and the start free of that warning. It uses the
app's own image, so there is no second tag to pin or pull.

The copy runs at every pod start, so the ConfigMap always wins: a change made with the CLI inside the
claim does not survive the next pod. The Deployment carries a checksum of the ConfigMap, so editing
`config.js` and syncing rolls the pod; without that annotation an edit would wait for a restart nobody
planned.

The app runs as UID 1000, the image's own `node` user. The claim is a local-path volume created
world-writable (owned `1100:1100`), so no `chown` is needed. Do not set `fsGroup`: local-path is
hostPath-backed and the kubelet does not apply it.

## Values that matter

- `ingressRoute.middlewares` — `lan-only` and `security`. The app authenticates itself over LDAP, so
  there is no forward-auth gate; re-adding `authentik-forwardauth` would only put a second prompt in
  front of the form.
- `storage.size` — `128Mi`. The previous 20 GiB was a guess; this holds `users.json`, logs and
  settings.
- `nodeSelector` — `nuc0`. The claim binds to the node the pod schedules on, and there is no failover.
