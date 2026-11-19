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
| ConfigMap | `thelounge` | `config.js`, mounted read-only with `subPath` |
| ServiceAccount | `thelounge` | its token is not mounted; the app never calls the API |
| IngressRoute | `thelounge-public` | `websecure`, `lan-only` then `authentik-forwardauth` |
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

## The gate

The Lounge has no OIDC support, so there is nothing native to wire to authentik. Its one built-in
identity mechanism is LDAP, which needs an LDAP outpost that this cluster does not run. The gate is
therefore the shared `kube-system-authentik-forwardauth` middleware, bound by the blueprint
`cluster/helm/authentik/files/app-thelounge.yaml` to `authentik Admins`.

The consequence is a second sign-in: the outpost proves the browser is signed in, but it cannot carry
that identity into the app, which keeps its own accounts. Turning on the LDAP path later is a
follow-up card, not part of this rebuild.

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

`config.js` is a file the app reads at startup, so it lives in a ConfigMap and is mounted with
`subPath`. A `subPath` mount does not see ConfigMap updates, so the Deployment carries a checksum of
the ConfigMap: editing `config.js` and syncing rolls the pod, and the new file is read.

The app runs as UID 1000, the image's own `node` user. The claim is new and local-path creates new
volumes world-writable (owned `1100:1100`), so no `chown` is needed. Do not set `fsGroup`: local-path
is hostPath-backed and the kubelet does not apply it.

## Values that matter

- `ingressRoute.middlewares` — the gate. Removing `authentik-forwardauth` leaves `lan-only` and the
  app's own password form.
- `storage.size` — `128Mi`. The previous 20 GiB was a guess; this holds `users.json`, logs and
  settings.
- `nodeSelector` — `nuc0`. The claim binds to the node the pod schedules on, and there is no failover.
