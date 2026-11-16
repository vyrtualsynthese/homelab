# The internal CA behind `*.kub.local`

Date: 2026-11-15

## What it is

`kube-system/local-cert` is the cluster's **default** certificate. Traefik serves it on the `websecure`
entry point to any TLS router that sets neither `secretName` nor `certResolver`. Since 2026-11-15 that is
every `kub.local` route in the `media-stack` namespace — sonarr, radarr, lidarr, prowlarr, bazarr,
bazarr-anime, sonarr-anime, radarr-anime, tautulli, maintainerr, autobrr, qbittorrent and calibre — with
each `web` router redirecting to its `websecure` twin. Argo CD joined them on 2026-11-16: its server
router serves no certificate of its own either, and its `argocd.kub.local` address must be the TLS one,
because the server compares the SSO return URL against it scheme included.

It is a **private** certificate, and it has to be: public certificate authorities have been barred from
issuing for internal names (`.local`, `.internal`, `.localhost`) since 2015, so a `.local` name can never
be trusted by a fresh client without giving that client a root first. That root is what this document is
about.

| | |
|---|---|
| Root | `CN = ashulab internal CA` (`O = ashulab internal`), valid 2026-11-15 → 2036-11-12 |
| Root SHA-256 | `F6:B5:71:D0:CD:0B:B9:BF:5C:CD:93:38:04:1E:69:6B:1A:51:FD:3D:C3:72:71:83:38:2E:EF:B9:EB:50:7B:F7` |
| Leaf | `CN = kub.local`, SAN `*.kub.local`, `*.app.local`, `localhost`, `127.0.0.1`, valid 2026-11-15 → 2028-11-14 |
| Leaf SHA-256 | `5E:37:B6:F4:01:17:16:C6:D3:AD:E4:E8:C7:0C:17:89:C0:51:CE:F4:CD:72:86:2C:EA:4B:3B:68:C5:0F:45:C8` |
| Files | `~/.config/secrets/kub-local/` — `local-ca.pem`, `local-ca-key.pem`, `kub.local.pem`, `kub.local-key.pem` |
| Generator | `hack/internal-cert.sh` (OpenSSL, no third-party tool) |
| Clients trust | the **root**, `local-ca.pem`, installed once per client |

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
openssl req -new -newkey rsa:2048 -nodes -keyout drill.key -out drill.csr -subj "/CN=drill.kub.local" >/dev/null 2>&1
openssl x509 -req -in drill.csr -CA local-ca.pem -CAkey local-ca-key.pem -CAcreateserial \
  -days 1 -sha256 -out drill.crt \
  -extfile <(printf 'subjectAltName=DNS:drill.kub.local\nbasicConstraints=critical,CA:FALSE\n') >/dev/null 2>&1
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
openssl s_client -connect 192.168.1.46:443 -servername calibre.kub.local </dev/null 2>/dev/null \
  | openssl x509 -noout -issuer -dates
```

## If the root key is lost anyway

```bash
hack/internal-cert.sh --force-ca
```

That mints a new root; the leaf is signed by it and the Secret replaced as above. Every client then imports
the new `local-ca.pem`. Nothing else changes — the host names, the routes and the apps are unaffected.

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
