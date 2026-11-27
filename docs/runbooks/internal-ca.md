# The internal CA behind `*.kub.internal`

> **In one line.** One private root vouches for every internal name: Traefik serves its `*.kub.internal`
> leaf (with `*.kub.local` beside it in the SAN until the soak ends), and a host that is not the cluster
> gets a leaf of its own — while the root key is the one file that has to be backed up twice, because
> nothing regenerates it.
>
> **Use it when** a certificate expires or has to be reissued, a client must trust the root, or a host
> outside the cluster needs a name it can prove. **Verified** 2026-11-27: the SAN line and the leaf's
> file names read in `hack/internal-cert.sh`, and both zone wildcards in `02-my-wildcard-dns.conf`; the
> generator itself was run end to end on 2026-11-26 for the cluster leaf and a host leaf, each chain
> verified against the root, and the host leaf served by Caddy on 8443. **Kind:** reference
>
> **Sources** `hack/internal-cert.sh`, `ansible/pi/hosts-pi.sh`,
> `ansible/playbooks/templates/runners/Caddyfile`,
> `ansible/playbooks/templates/runners/pihole.settings`,
> `cluster/helm/monitoring/pihole-exporter/values.yaml`,
> `ansible/playbooks/templates/runners/02-my-wildcard-dns.conf`

Date: 2026-11-15

## What it is

`kube-system/local-cert` is the cluster's **default** certificate. Traefik serves it on the `websecure`
entry point to any TLS router that sets neither `secretName` nor `certResolver`. Since 2026-11-15 every
route in the `media-stack` namespace has fallen through to it — sonarr, radarr, lidarr, prowlarr, bazarr,
bazarr-anime, sonarr-anime, radarr-anime, tautulli, maintainerr, autobrr, qbittorrent and calibre — with
each `web` router redirecting to its `websecure` twin. Argo CD joined them on 2026-11-16: its server
router serves no certificate of its own either, and its `argocd.kub.internal` address must be the TLS one,
because the server compares the SSO return URL against it scheme included. Grafana followed on
2026-11-17 for the same reason: its `root_url` and the authentik redirect URI must carry the scheme the
browser uses, and the plain-HTTP router now only redirects.

It is a **private** certificate, and it has to be: public certificate authorities have been barred from
issuing for internal names (`.internal`, `.local`, `.localhost`) since 2015, so an internal name can
never be trusted by a fresh client without giving that client a root first. That root is what this
document is about.

| | |
|---|---|
| Root | `CN = ashulab internal CA` (`O = ashulab internal`), valid 2026-11-15 → 2036-11-12 |
| Root SHA-256 | `F6:B5:71:D0:CD:0B:B9:BF:5C:CD:93:38:04:1E:69:6B:1A:51:FD:3D:C3:72:71:83:38:2E:EF:B9:EB:50:7B:F7` |
| Leaf | `CN = kub.local`, SAN `*.kub.internal`, `*.kub.local`, `*.app.local`, `localhost`, `127.0.0.1`, valid 2026-11-15 → 2028-11-14 |
| Files | `~/.config/secrets/kub-local/` — `local-ca.pem`, `local-ca-key.pem`, `kub.local.pem`, `kub.local-key.pem` |
| Generator | `hack/internal-cert.sh` (OpenSSL, no third-party tool) |
| Clients trust | the **root**, `local-ca.pem`, installed once per client |

The common name and the file names still say `kub.local`; the names the leaf answers are in the SAN.
`*.kub.local` stays there, and the old zone's wildcard redirect stays in `traefik-gate`, until the soak
ends. The script prints the leaf's fingerprint when it signs one, and that value changes with every
renewal, so only the root's fingerprint above is a constant to compare against.

## Why this document exists

The certificate before this one expired on **2026-05-06** and nobody noticed for ten months. It had been
made by hand with mkcert on Windows; the CA *certificate* survived in two places, its private key in none,
so the trust every client already had could not be renewed. A new root had to be minted and installed
everywhere.

The lesson is narrow and worth writing down: the leaf is disposable, the root key is not.

## Backing up the root key

`local-ca-key.pem` is the only file here whose loss costs real work. Everything else can be regenerated
from it; without it, a new root means every client installs a new root again.

**Keep two copies in two failure domains, both encrypted, one of them offline.**

1. **Password manager** — a Bitwarden secure note holding the PEM of `local-ca-key.pem` *and* the PEM of
   `local-ca.pem`, with the root fingerprint above written in the body so a restored copy can be identified
   at a glance. Turn on *Master password re-prompt* for that item. Paste the text rather than attaching a
   file if the plan has no attachments.
2. **Offline copy** — the same two files, and the drill commands below, on an encrypted USB stick
   (`age`/`gpg` tarball) kept somewhere physical. Not the NAS on its own: a NAS backup shares the power
   supply, the file system and the operator with this workstation. Note that the documented offsite path
   is currently broken (AGENTS §7.3), so treat "no second copy yet" as the honest state until it works.

Never: in git, in chat, by email, or only in `~/.config/secrets` on this machine.

## Restore drill — a backup that has never been restored is a hope

Run this when the backup is created, and once a year. It needs nothing from the cluster.

```bash
mkdir -p /tmp/ca-drill && cd /tmp/ca-drill
# restore local-ca.pem and local-ca-key.pem from the backup into this directory
chmod 600 local-ca-key.pem

openssl x509 -in local-ca.pem -noout -fingerprint -sha256
# must print the root fingerprint from the table above

# sign a throwaway leaf with the restored key and prove the chain
openssl req -new -newkey rsa:2048 -nodes -keyout drill.key -out drill.csr -subj "/CN=drill.kub.internal" >/dev/null 2>&1
openssl x509 -req -in drill.csr -CA local-ca.pem -CAkey local-ca-key.pem -CAcreateserial \
  -days 1 -sha256 -out drill.crt \
  -extfile <(printf 'subjectAltName=DNS:drill.kub.internal\nbasicConstraints=critical,CA:FALSE\n') >/dev/null 2>&1
openssl verify -CAfile local-ca.pem drill.crt        # expect: drill.crt: OK
rm -rf /tmp/ca-drill
```

## Renewing the leaf

Do it before 2028-11-14. The root stays; every client keeps working.

```bash
hack/internal-cert.sh          # reuses local-ca.pem, signs a new leaf

kubectl -n kube-system delete secret local-cert --ignore-not-found
kubectl -n kube-system create secret tls local-cert --save-config \
  --cert=$HOME/.config/secrets/kub-local/kub.local.pem \
  --key=$HOME/.config/secrets/kub-local/kub.local-key.pem
```

**Delete then create, not `apply`.** The Secret in place is `Opaque` (it was made with
`--from-file`), and a Secret's `type` field is immutable, so `kubectl apply` with `type: kubernetes.io/tls`
is rejected with `field is immutable`. `--save-config` records the last-applied annotation so a later
`apply` does not warn about it.

Traefik picks the new Secret up without a restart. Confirm:

```bash
openssl s_client -connect 192.168.1.46:443 -servername calibre.kub.internal </dev/null 2>/dev/null \
  | openssl x509 -noout -issuer -dates
```

## If the root key is lost anyway

```bash
hack/internal-cert.sh --force-ca
```

That mints a new root; the leaf is signed by it and the Secret replaced as above. Every client then imports
the new `local-ca.pem`. Nothing else changes — the host names, the routes and the apps are unaffected.

## A leaf for a host that is not the cluster

The two edge Pis terminate TLS for two admin surfaces of their own, so each needs names the internal
root vouches for: the wg-easy panel behind Caddy on 8443, and Pi-hole's own panel and API on 443. They
get their own leaves, not a copy of the wildcard: the `*.kub.internal` key on an edge host would be a key
to every internal name, and the leaf is disposable by design.

```bash
hack/internal-cert.sh --host vpn-rbp0.kub.local
hack/internal-cert.sh --host vpn-rbp1.kub.local
hack/internal-cert.sh --host pihole-rbp0.kub.local
hack/internal-cert.sh --host pihole-rbp1.kub.local
```

The script reuses the CA in `~/.config/secrets/kub-local/` and never touches `kub.local.pem`, so issuing
one cannot disturb a cluster route. It writes `NAME.pem` and `NAME-key.pem`, prints the `scp` pair, and
each surface takes them its own way:

- **The VPN panel** reads `certs/tls.pem` and `certs/tls-key.pem`, named by the `Caddyfile`. Copy the
  `vpn-*` pair under those two names.
- **Pi-hole** reads one combined PEM from `webserver.tls.cert`; there is no separate key setting. Put
  the certificate first and its key after it:

  ```bash
  cat pihole-rbp0.kub.local.pem pihole-rbp0.kub.local-key.pem > pihole.pem
  ```

  Copy that to `~/dockers/certs/pihole.pem` on `rbp0` (and the `rbp1` pair to `rbp1`). `pihole.settings`
  names it through `FTLCONF_webserver_tls_cert`, and the compose mounts `./certs` read-only into the
  Pi-hole container. The panel reads the file as the image's `pihole` user, uid 1000, which is
  `vronteix` on these hosts: `hosts-pi.sh setup` hands `certs/` and the PEM to that account and keeps
  the Caddy pair root-only. A copy left `0600 root:root` is unreadable to Pi-hole, and it then refuses
  to start its web server at all rather than serve a self-signed pair, so 80 and 443 go dark.

`ansible/pi/hosts-pi.sh` requires all three files, writes none of them, and `backup` carries them off
the host. A leaf lasts two years, like the cluster's: re-issue it with the same command before it
expires and copy the new file the same way. A reflash restores the old pair with the rest of the
stack, so a leaf that expired before the reflash comes back expired.

The names are Pi-hole's to answer: `02-my-wildcard-dns.conf` maps `vpn-rbp0.kub.local`,
`vpn-rbp1.kub.local`, `pihole-rbp0.kub.local` and `pihole-rbp1.kub.local` to the two Pis, above the
`kub.internal` and `kub.local` wildcards that point at the router.

A replaced Pi-hole leaf also changes the pin in `hack/pihole/<host>.pem`: `hack/check_pihole_parity.py`
trusts each panel's own certificate and nothing else, so re-export both after the change and commit
them in the same one. The cluster's exporter does not pin: it dials `pihole-<host>.kub.local` and
points Go's `SSL_CERT_FILE` at this root, which the chart carries in a ConfigMap.

## Installing the root on a client

| Client | How |
|---|---|
| WSL / Linux | `sudo cp local-ca.pem /usr/local/share/ca-certificates/ashulab-internal-ca.crt && sudo update-ca-certificates` |
| Windows (Chrome, Edge) | `certutil.exe -addstore -user Root "C:\path\to\ashulab-internal-ca.crt"` — user store, no elevation |
| Firefox (any OS) | Settings → Certificates → View Certificates → Authorities → Import |
| macOS | Keychain Access → System → import the certificate → *Always Trust* |
| iOS / iPadOS | AirDrop the `.crt`, install the profile, then **Settings → General → About → Certificate Trust Settings** and switch it on |
| Android | Settings → Security → Encryption & credentials → Install a certificate → CA certificate |

A client that has not been given the root shows a warning; that is a missing trust install, not a broken
server.
