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

Two Secrets exist by hand, because neither can be issued from git. Generate both in the shell that
uses them and print them first: `--password "$(openssl rand -hex 32)"` is expanded by your shell and
never printed by `cscli`, and `-f /dev/null` discards the file it would have written — the machine
then exists with a password nobody knows.

```bash
set -euo pipefail
LAPI_PW=$(openssl rand -hex 32)
OIDC_SECRET=$(openssl rand -hex 32)
printf 'LAPI machine password: %s\nOIDC client secret:   %s\n' "$LAPI_PW" "$OIDC_SECRET"

# 1. The machine credential: a *watcher*, i.e. a CrowdSec account that can read
#    alerts and decisions and add or delete them. `cscli` has no env-var
#    equivalent of a bouncer key for machines, so this is one command against the
#    running LAPI with an admin kubeconfig (the agent's shells answer 403).
#    `-f /dev/null` keeps cscli from rewriting the container's own
#    local_api_credentials.yaml.
kubectl -n security exec deploy/crowdsec-lapi -- \
  cscli machines delete crowdsec-web-ui --ignore-missing
kubectl -n security exec deploy/crowdsec-lapi -- \
  cscli machines add crowdsec-web-ui --password "$LAPI_PW" -f /dev/null

# 2. The authentik half. The blueprint reads !Env CROWDSEC_CLIENT_SECRET when it
#    is applied, so the key must exist before the worker restarts.
kubectl -n authentik patch secret authentik-sso --type merge \
  -p "$(jq -n --arg s "$OIDC_SECRET" '{stringData:{"CROWDSEC_CLIENT_SECRET":$s}}')"
kubectl -n authentik rollout restart deployment/authentik-worker

# 3. The Secret the chart reads.
kubectl -n security create secret generic crowdsec-web-ui \
  --from-literal=lapi-password="$LAPI_PW" \
  --from-literal=oidc-client-secret="$OIDC_SECRET" \
  --dry-run=client -o yaml | kubectl apply -f -
```

The value has to be the same string in both places, because Secrets do not cross namespaces.

A missing Secret is a `CreateContainerConfigError`, not a failed sync; a missing machine credential
is worse, because the pod is `Running` and the UI is simply empty.

## OIDC

The app authenticates by itself; the route carries no `authentik-forwardauth`. Client id
`crowdsec-web-ui`, issuer `https://authentik.kub.home.ashudev.com/application/o/crowdsec-web-ui/`,
callback `https://crowdsec.kub.internal/api/auth/oidc/callback`. The callback is built from the
`Host` and `X-Forwarded-*` headers, so the provider's registered URI and this host must match
exactly — a mismatch is a login loop with nothing in the log.

Only members of `authentik Admins` get an account (`unmatchedRole: deny`). The app also has a
read-only role, for a group added to `CONFIG_AUTH_OIDC_READ_ONLY_GROUPS_0` when one is wanted.

**Break-glass.** Password login is off. Set `CONFIG_AUTH_PASSWORD_LOGIN_ENABLED: "true"`, restart the
Deployment, and the password form returns; the first sign-in creates a local administrator.

## Metrics

The sidebar's **Metrics** tab reads the LAPI's own Prometheus listener at
`http://crowdsec-service:6060/metrics`, not Prometheus. The upstream crowdsec chart renders that port
on `crowdsec-service` because `lapi.metrics.enabled` is true, so the tab keeps working while the
monitoring stack is down — the reason this UI exists. Prometheus is unchanged: it still scrapes the
pod IP by annotation. The listener has no access control of its own, so it stays on the pod network
and no route names it.

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
