# Secrets

> **In one line.** Every Kubernetes Secret the in-repo charts expect to find already in the cluster,
> where it lives, who reads it and where its value comes from — never the value itself — then the
> PostgreSQL instance and the per-application database passwords.
>
> **Use it when** a pod is stuck in `CreateContainerConfigError`, a rebuild needs to know which Secret
> to create first, or a credential is being rotated · **Verified** 2026-11-22 · **Kind:** reference
>
> **Sources** `docs/runbooks/last-resort.md`, `docs/security/critical-points.md`, `cluster/helm/`

**Never a value.** Not a password, not a token, not an encoded substitute for one. Values live in
the cluster and in the operator's password manager, nowhere else. Secrets are not in git and must
not be added to it.

State: read from the charts on 2026-11-17; the custody of the three rebuild credentials below was
confirmed with the operator on 2026-11-22 (card #461). This file is the whole inventory: every Secret
a chart expects, then the PostgreSQL instance and the per-application database passwords.

## Application secrets expected by the charts

> Compiled from the charts on 2026-11-17; not verified against the cluster, because the read-only
> account cannot read Secrets. The chart that mounts or references a Secret is the authority on its
> keys.

| Secret | Namespace | Keys and consumer |
|---|---|---|
| `sonarr`, `sonarr-anime`, `radarr`, `radarr-anime`, `lidarr`, `prowlarr` | `media-stack` | `secret` → exportarr APIKEY, and unpackerr's per-instance API key |
| `bazarr`, `bazarr-anime` | `media-stack` | `secret` → exportarr APIKEY (only when `exportarr.enabled`; off until exportarr v3) |
| `autobrr-oidc` | `media-stack` | `client-secret` → `AUTOBRR__OIDC_CLIENT_SECRET`, the authentik provider's client secret |
| `freshrss-oidc` | `news` | `client-secret` → `OIDC_CLIENT_SECRET`, the authentik provider's client secret; `client-crypto-key` → `OIDC_CLIENT_CRYPTO_KEY`, the module's own encryption key |
| `plex` | `media-stack` | `plex_claim` |
| `qbittorrent-exporter` | `media-stack` | `username`, `password` |
| `cross-seed` | `media-stack` | mounted as a file (`conf.secretName`) |
| `recyclarr` | `media-stack` | mounted as a file |
| `filebrowser-oidc` | `filebrowser` | `CLIENTSECRET` → `FILEBROWSER_OIDC_CLIENT_SECRET`, the authentik OAuth2 provider's client secret |
| `vikunja-oidc` | `vikunja` | `CLIENTSECRET` → `VIKUNJA_AUTH_OPENID_PROVIDERS_AUTHENTIK_CLIENTSECRET`, the authentik OAuth2 provider's client secret; the same value is `VIKUNJA_CLIENT_SECRET` in `authentik-sso` |
| `koillection` | `koillection` | `DB_NAME`, `DB_HOST`, `DB_USER`, `DB_PASSWORD` |
| `authentik-env` | `authentik` | `envFrom` |
| `dashy-conf` | `dashy` | rendered by `cluster/helm/dashy/templates/secret-config.yaml` from `files/conf.yml`; holds no credentials |
| `postgres-secrets` | `databases` | Bitnami PostgreSQL `existingSecret` |
| `pgadmin` | `databases` | external chart `existingSecret` |
| `renovate` | `renovate` | external chart `existingSecret` |
| `pihole-exporter` | `monitoring` | `key` |
| `mktxp-conf` | `monitoring` | mounted as a file |
| `grafana` | `monitoring` | `admin-user`, `admin-password` — required; the chart reads it via `admin.existingSecret` |
| `local-cert` | `kube-system` | Traefik default TLS cert (`TLSStore`) |
| `traefik-dashboard` | `kube-system` | `users` — the htpasswd line(s) for the dashboard's basic auth |
| `traefik-ovh` | `kube-system` | `OVH_ENDPOINT`, `OVH_APPLICATION_KEY`, `OVH_APPLICATION_SECRET`, `OVH_CONSUMER_KEY` → `envFrom` on the Traefik pod; the consumer key is scoped to `/domain/zone/{ashudev.com,ashuvidz.com}/{record,record/*,refresh}` |
| `n8n` | `n8n` | `encryption-key` → `N8N_ENCRYPTION_KEY`, the key n8n encrypts stored credentials with |
| `backup-restic` | `backup` | `RESTIC_PASSWORD`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` (restic's names for any S3 store's pair; the values are OVH's) → `envFrom` in every job of `cluster/helm/backup` |

- **`traefik-dashboard` is keyed `users`, and the key name is the whole contract.** The Middleware
  [`traefik-dashboard`](../cluster/infrastructure/traefik/dashboard/auth.yaml) reads this Secret, and
  Traefik only looks for `users`, holding `user:hash` lines in htpasswd format (bcrypt preferred). A
  Secret that carries its hash under any other key — `auth` was the mistake, live from 2023 to
  2026-11-22 — gives a middleware with **no users**, which answers `401` to every request, correct
  password included. That looks like a working password prompt, which is why it survived so long.
  Recreate it and prove it in one sitting:

```bash
htpasswd -nbB vronteix '<password>' \
  | kubectl -n kube-system create secret generic traefik-dashboard \
      --from-file=users=/dev/stdin --dry-run=client -o yaml | kubectl apply -f -
curl -sk -o /dev/null -w '%{http_code}\n' -u 'vronteix:<password>' \
  --resolve traefik.kub.local:443:192.168.1.46 https://traefik.kub.local/dashboard/   # 200
```

`traefik-ovh` holds the OVH API credential the DNS-01 resolver uses. The application key is the
existing one; the consumer key was created for this purpose and is limited to creating and deleting
TXT records under `ashudev.com` and `ashuvidz.com` and refreshing those two zones, so a copy of it
cannot change delegation, storage, mail or another domain. It is validated once in the OVH browser,
and the four values are kept in `~/.config/secrets/homelab.env` outside git. Rotating it is a Secret
update and a pod restart.

`n8n` is the one application Secret that must exist **before** its first sync: the deployment reads
`encryption-key` without `optional`, so a missing Secret is `CreateContainerConfigError`, not a pod
that regenerates the key and silently makes every stored credential unreadable. Generate it once with
`openssl rand -hex 32` and keep it in the password manager; changing it later costs the credentials.

## The restic repositories (`backup` namespace)

`backup-restic` is created by hand, once, before the first backup job runs:

```bash
kubectl -n backup create secret generic backup-restic \
  --from-literal=RESTIC_PASSWORD='<value>' \
  --from-literal=AWS_ACCESS_KEY_ID='<value>' \
  --from-literal=AWS_SECRET_ACCESS_KEY='<value>'
```

`AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` are the variable names restic's S3 backend reads;
the credentials behind them are the OVH S3 user declared in `infra-main/ovh/backup.tf`, scoped to
`arn:aws:s3:::ashudev-homelab-backup` and nothing else. Read them with `tofu output -raw
access_key_id` and `-raw secret_access_key` in that stack. They are not the state backend's pair and
must never be: that one reaches the OpenTofu state. The restic password is not an S3 credential at
all — it encrypts the repository, and losing it loses the backups.

Three credentials are **not** Kubernetes Secrets, and a rebuild needs all three; the fourth row is a
Secret that does not exist yet.

| Credential | Why losing it matters | Where it is kept | Failure domain |
|---|---|---|---|
| the restic repository password | every repository is ciphertext under it; without it the bucket is noise, not a backup | Bitwarden, item `homelab-backup-last-resort` (Homelab folder, master-password re-prompt on), written 2026-11-22 | cloud vault |
| the OVH S3 pair for the bucket | the bucket cannot be opened, though its contents are intact. The cluster's copy is the `backup-restic` Secret, which dies with the cluster | the same vault item, and derivable: `tofu output -raw access_key_id` in `infra-main/infra-main/ovh` reprints it from the OVH API key | cloud vault, and re-derivable |
| the K3s server join token (`/var/lib/rancher/k3s/server/token`) | an etcd snapshot is encrypted with a key derived from it, so restoring onto a rebuilt host with a different token produces an unreadable snapshot | the same vault item, field `K3S_SERVER_TOKEN` | cloud vault |
| `k3s-etcd-snapshot-s3-config` | reserved: the Secret the official K3s upload reads, with keys `bucket`, `endpoint`, `access-key`, `secret-key`. Created by the K3s 1.31 upgrade (W5), **not** by the restic work | does not exist yet | — |

**The first three rows above share one failure domain.** Until 2026-11-22 Bitwarden held neither the
repository password nor a K3s token, and the note that read like one ("Kluster Backup codes") is the
retired AWS and syncthing setup whose passwords open nothing. The Bitwarden master password and its
2FA recovery codes are the root of the chain: they are printed on paper and kept offline, and that
page is the only copy (2026-11-22, card #461), so the whole table above is one page away from being
unreachable. A second copy that outlives both the OVH account and the vault is card #535, frozen
pending a second EU provider.

The procedure that consumes these, with the cluster assumed gone, is
[`runbooks/last-resort.md`](runbooks/last-resort.md).

## The PostgreSQL instance (`databases` namespace)

| Secret | Namespace | Keys | Consumer | Where the value comes from |
|---|---|---|---|---|
| `postgres-superuser` | `databases` | `postgres-password` | The `postgres` container (`POSTGRES_PASSWORD`) and `Job/postgres-bootstrap`; pgAdmin only when the operator types it into the connection prompt by hand | Generated once with `openssl rand -hex 24` before the first sync and kept in the operator's password manager. It initialises `PGDATA`: changing it later does nothing until the role password is changed with `ALTER ROLE`. It is never mounted into pgAdmin, and the pre-registered admin server entry is configured to prompt for it rather than save it. |
| `pg-backup` | `databases` | `backup-password` | `Job/postgres-bootstrap` (sets the role's password) and `CronJob/postgres-dump` (dumps as that role) | Generated at the same time as the superuser password, with the same command. No human types it again; it is pasted from the password manager if the Secret has to be recreated. |
| `pg-exporter` | `databases` | `exporter-password` | The `exporter` sidecar of the `postgres` StatefulSet, through `DATA_SOURCE_PASS` | Generated at the same time, same command. It authenticates `postgres_exporter` over TCP for Prometheus only. |
| `pgadmin` | `databases` | `password` | pgAdmin's own web login (`pgadmin.existingSecret` in the wrapper chart) | Chosen by the operator when the wrapper chart is first synced. The login email is **not** a secret: it is `pgadmin.env.email` in `cluster/helm/databases/pgadmin/values.yaml`. |

`pgadmin-config` is not a Secret but it holds secrets: pgAdmin encrypts the database passwords it
is told to save, and `serverDefinitions` in the wrapper chart pre-registers a server entry
("postgres (read-only)") that logs in as `backup`, whose password the operator may save. The claim
therefore contains encrypted copies of database credentials, and the W4 backup of the `nuc0` config
volumes inherits them. Treat that claim as sensitive even though nothing in it looks like a
password.

All four must exist before ArgoCD syncs the `databases` Applications for the first time. Creating
them is one command each, with the value read from the password manager, for example:

```bash
kubectl -n databases create secret generic postgres-superuser \
  --from-literal=postgres-password='<value>'
```

The key names are not decoration: every consumer above refers to them by name, and a Secret with
the right name and a different key still fails to start its pod.

## Application databases, created on demand

Until the board (2026-11-15) there was no application database, and none is created in advance. Each
one is created when its application is rebuilt, by
[`runbooks/postgres-add-database.md`](runbooks/postgres-add-database.md), which also records the
date and the application in that runbook's log. The `vikunja-db` row at the end of the table below is
the first one created that way.

A Kubernetes Secret belongs to one namespace, so a database password necessarily exists twice: once
beside the instance that creates the role, once beside the application that consumes it.

| Secret | Namespace | Keys it must hold | Consumer | Where the value comes from |
|---|---|---|---|---|
| `pg-<app>` | `databases` | `password` | Whoever runs `ALTER ROLE` or inspects the database; the role itself already holds a hash of it | Generated by the runbook with `openssl rand -hex 24` and copied into the application's Secret below, so both hold the same value. |
| `authentik-env` | `authentik` | `AUTHENTIK_SECRET_KEY`, `AUTHENTIK_BOOTSTRAP_PASSWORD`, `AUTHENTIK_POSTGRESQL__PASSWORD`, `AUTHENTIK_POSTGRESQL__HOST`, plus the SMTP password when email is enabled. Every key is exported into the container with `envFrom` | authentik server and worker | Generated by the database runbook and the phase 1 rebuild. The bootstrap password creates `akadmin` on first start only; change it in the UI after the first login. The secret key signs sessions and cookies. |
| `authentik-sso` | `authentik` | `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET`, `PLEX_TOKEN`, `GRAFANA_CLIENT_SECRET`, `PGADMIN_CLIENT_SECRET`, `KAVITA_CLIENT_SECRET`, `ARGOCD_CLIENT_SECRET`, `CALIBRE_CLIENT_SECRET`, `RADARR_CLIENT_SECRET`, `RADARR_ANIME_CLIENT_SECRET`, `FRESHRSS_CLIENT_SECRET`, `FILEBROWSER_CLIENT_SECRET`, `VIKUNJA_CLIENT_SECRET`. Exported into the authentik server and worker with `envFrom`; the blueprint `!Env` tags read it | authentik server and worker | Source and provider client secrets, created by the operator. The same values are copied into each application's own Secret (`grafana-oidc`, `pgadmin-oidc`, `freshrss-oidc`, `filebrowser-oidc`, `vikunja-oidc`) or its UI (`calibre`, pasted into Calibre-Web's OAuth settings because it stores it in `app.db`), because Secrets do not cross namespaces |
| `authentik-ldap` | `authentik` | `AUTHENTIK_TOKEN` — the `ashulab-ldap` outpost's token | The LDAP outpost Deployment `authentik-ldap` | Created by the operator from the outpost's generated token; the runbook holds the one-liner |
| `grafana-oidc` | `monitoring` | `GF_AUTH_GENERIC_OAUTH_CLIENT_SECRET` | Grafana chart `envFromSecret`; `grafana.ini` reads it with `$__env{}` | Copy of `GRAFANA_CLIENT_SECRET` from `authentik-sso` |
| `pgadmin-oidc` | `databases` | `config_local.py` | Mounted at `/pgadmin4/config_local.py` by the chart | Generated file holding `OAUTH2_CONFIG`, with the client secret copied from `authentik-sso` |
| `koillection` | `koillection` | `DB_NAME`, `DB_HOST`, `DB_USER`, `DB_PASSWORD` | The application pod's `env` | Existing Secret. `DB_HOST` is where the hostname moves; the file is also the documented rotation point for the password. |
| `vikunja-db` | `vikunja` | `VIKUNJA_DATABASE_PASSWORD` | The board pod's environment; the key name is Vikunja's own variable, so the chart needs no mapping | Created by [`runbooks/vikunja-database.md`](runbooks/vikunja-database.md) with `openssl rand -base64 24`, so the value is 32 characters. Deliberately **no** `databases/pg-vikunja` copy: this Secret is the only one, and losing it is repaired with `ALTER ROLE` plus a new Secret. |

Only the password keys hold secrets; `DB_NAME`, `DB_HOST` and `DB_USER` live in
the Secret because the chart reads them there, not because they are sensitive.

Rotation is therefore two writes and one statement: update both Secrets, then
`ALTER ROLE <app> PASSWORD '<new value>'`, then restart the application pod. The procedure, in
order and with the checks at each step, is
[`running-it/rotate-a-secret.md`](running-it/rotate-a-secret.md). Changing only one of the two
copies is the failure mode: the application keeps working until its next restart, then fails
authentication for no visible reason.

## What goes wrong without them

| Symptom | Cause |
|---|---|
| Pod `CreateContainerConfigError`, event `secret "…" not found` | The Secret is missing, was created in the wrong namespace, or the key inside it is renamed. |
| `Job/postgres-bootstrap` runs and fails, exporter sidecar never ready | The Secrets exist with the expected keys, so the pod starts, and the SQL fails: the server did not accept connections within the 300 s wait, or the superuser password does not match the one that initialised `PGDATA`. A missing Secret or key never gets this far — its pod waits in `CreateContainerConfigError`. A Job that has already failed stays failed — its name is content-addressed and it has no TTL, so a sync does not re-run it; delete it and sync: `kubectl -n databases delete job -l app=postgres-bootstrap`. |
| `password authentication failed for user "backup"` in the dump Job's logs | `pg-backup` holds a different value from the one the role was created with. |
| Dump Job fails and `/dumps` stays empty | Not a Secret problem. A dump is due — the `vikunja` database exists — so a red Job means one database listed on the instance is unreachable, or the `backup` role lacks `CONNECT` on it. The CronJob exits non-zero rather than reporting a false success, which is the point: read its logs for the database name that failed, and fix that grant, don't hunt a Secret. |

## Related

- The chart: `cluster/helm/databases/postgres/`.
- The design: [`superpowers/specs/2026-11-13-postgresql-design.md`](superpowers/specs/2026-11-13-postgresql-design.md)
  §6.5 (roles and databases), §6.6 (secrets).
- Offsite copies of the dumps are W4 and do not exist yet; this file is an inventory, not a backup.
