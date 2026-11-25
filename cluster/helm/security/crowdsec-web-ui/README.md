# crowdsec-web-ui

[CrowdSec Web UI](https://github.com/TheDuffman85/crowdsec-web-ui) — a React/Node dashboard over the
LAPI's alerts and decisions, with the buttons to undo a wrong ban. It is here as the second view of
the CrowdSec stack that does not need Prometheus, Loki or Grafana, for the days the monitoring
stack is the thing that is broken. The Grafana dashboard is still the trend view; this is the
actionable one.

## What renders

Six objects: Deployment, Service, ServiceAccount, config claim, the TLS `IngressRoute` and its
plain-HTTP redirect mirror. There is no ConfigMap: every setting is an
environment variable in the Deployment, so a change rolls the pod by itself.

## Before the first sync

Two Secrets exist by hand, because neither can be issued from git.

1. **A machine credential in the LAPI.** The UI is a *watcher*: a CrowdSec account that can read
   alerts and decisions, and add or delete decisions. `cscli` has no environment-variable
   equivalent of a bouncer key for machines, so this is one command against the running LAPI pod
   with an admin kubeconfig (the agent's shells are read-only and answer `403`):

   ```bash
   kubectl -n security exec deploy/crowdsec-lapi -- \
     cscli machines add crowdsec-web-ui --password "$(openssl rand -hex 32)" -f /dev/null
   ```

   `-f /dev/null` matters: without it `cscli` rewrites the container's own
   `local_api_credentials.yaml`. Keep the printed password.

2. **The Secret the chart reads:**

   ```bash
   kubectl -n security create secret generic crowdsec-web-ui \
     --from-literal=lapi-password='<the password from step 1>' \
     --from-literal=oidc-client-secret='<the authentik provider's client secret>'
   ```

   Sync the `authentik` Application first: its blueprint creates the provider and names
   `CROWDSEC_CLIENT_SECRET`, a key of the `authentik-sso` Secret. The value has to be the same
   string in both places, because Secrets do not cross namespaces.

A missing Secret is a `CreateContainerConfigError`, not a failed sync; a missing machine credential
is worse, because the pod is `Running` and the UI is simply empty.

## OIDC

The app authenticates by itself; the route carries no `authentik-forwardauth`. Client id
`crowdsec-web-ui`, issuer `https://authentik.kub.home.ashudev.com/application/o/crowdsec-web-ui/`,
callback `https://crowdsec.kub.local/api/auth/oidc/callback`. The callback is built from the
`Host` and `X-Forwarded-*` headers, so the provider's registered URI and this host must match
exactly — a mismatch is a login loop with nothing in the log.

Only members of `authentik Admins` get an account (`unmatchedRole: deny`). The app also has a
read-only role, for a group added to `CONFIG_AUTH_OIDC_READ_ONLY_GROUPS_0` when one is wanted.

**Break-glass.** Password login is off. Set `CONFIG_AUTH_PASSWORD_LOGIN_ENABLED: "true"`, restart the
Deployment, and the password form returns; the first sign-in creates a local administrator.

## Probes

Liveness and readiness share `/api/health`, and that is deliberate: the endpoint says the HTTP
listener is up, not that the LAPI answers. Restarting the UI because the LAPI is down would lose the
sync state and change nothing. The startup probe covers the first boot, when the SQLite schema is
created.

## Logs

The server documents no log-level variable, so container logs stay at its default, which includes
one line per request. The record an operator acts on is the audit log: every decision add, delete
and cleanup is written as an `[audit]` JSON line, and the decisions themselves stay in the LAPI's
SQLite. If upstream adds a level knob, it goes in `values.yaml` next to `env` and the note there
goes away.

## Storage

`/app/data` holds `state.db` (users, settings, the mirrored decisions), `statistics.db` (long-term
aggregates) and `cache.db` (rebuildable). The claim is `1Gi` rather than the house `128Mi` because it
mirrors a 168h alert lookback. It is a `sqlite` row on `nuc0` in the backup matrix; `cache.db` is
excluded there.
