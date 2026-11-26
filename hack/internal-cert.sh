#!/usr/bin/env bash
# Issue the cluster's default TLS certificate: the private `*.kub.local` one —
# and, with `--host`, a leaf for a machine that is not the cluster.
#
# Traefik serves `kube-system/local-cert` (through the default TLSStore) to any
# `websecure` router that sets neither `secretName` nor `certResolver`. A `.local`
# name can never hold a publicly trusted certificate — the CA/Browser Forum banned
# public CAs from issuing for internal names in 2015 — so this is a private CA.
# The root it prints has to be installed on each client once; after that, renewals
# signed by the same root need no client action.
#
#   hack/internal-cert.sh                first run: create the CA, sign a leaf
#   hack/internal-cert.sh                later run: reuse the CA, sign a fresh leaf
#   hack/internal-cert.sh --force-ca     new CA — every client must re-install it
#   hack/internal-cert.sh --out DIR      where to write (default: below)
#   hack/internal-cert.sh --host NAME    a leaf for one host that is not the
#                                        cluster's; same CA, its own name and key
#
# Nothing here is committed: keys never enter the repository. Replacing the Secret
# takes effect at once, without a Traefik restart — but it has to be deleted and
# recreated, not applied in place: the existing object is `Opaque` and a Secret's
# `type` field is immutable, so `kubectl apply` fails with "field is immutable".
#
#   kubectl -n kube-system delete secret local-cert --ignore-not-found
#   kubectl -n kube-system create secret tls local-cert --save-config \
#     --cert=DIR/kub.local.pem --key=DIR/kub.local-key.pem
#
# Why this script exists: `kube-system/local-cert` was last signed by hand with
# mkcert and expired on 2026-05-06, which went unnoticed for ten months, and the
# CA that could have renewed it is gone (only its certificate survived). Run this
# again before the date it prints; the `local-cert` expiry is a candidate for a
# Prometheus rule.
#
# **Back up `local-ca-key.pem`.** It is the one file here whose loss costs real
# work: without it no renewal is possible, and a new root has to be installed on
# every client again. A password manager entry is the right place; the full
# procedure, including the restore drill, is docs/runbooks/internal-ca.md.
set -euo pipefail

OUT="${HOME}/.config/secrets/kub-local"
FORCE_CA=0
HOST_NAME=""
CA_DAYS=3650
LEAF_DAYS=730
# One label only: `*.kub.local` covers calibre.kub.local, not a.b.kub.local.
SAN="DNS:*.kub.local,DNS:*.app.local,DNS:localhost,IP:127.0.0.1"

while [ $# -gt 0 ]; do
  case "$1" in
    --out) OUT="${2:?--out needs a directory}"; shift 2 ;;
    --force-ca) FORCE_CA=1; shift ;;
    --host) HOST_NAME="${2:?--host needs a name}"; shift 2 ;;
    -h|--help) sed -n '2,37p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

command -v openssl >/dev/null || { echo "openssl is required" >&2; exit 1; }

umask 077
mkdir -p "$OUT"
cd "$OUT"

CA_KEY="local-ca-key.pem"
CA_CRT="local-ca.pem"
LEAF_KEY="kub.local-key.pem"
LEAF_CRT="kub.local.pem"
LEAF_CSR="leaf.csr"

# Sign one leaf with the CA in this directory. $1 is the common name, $2 the
# subjectAltName list, $3 and $4 the certificate and key to write. One function
# for both leaves because the extensions are the whole contract, and a host leaf
# that drifted from the cluster one would be a second recipe to get wrong.
sign_leaf() {
  local cn="$1" san="$2" crt="$3" key="$4"

  openssl req -new -newkey rsa:2048 -sha256 -nodes \
    -keyout "$key" -out "$LEAF_CSR" \
    -subj "/CN=$cn" >/dev/null 2>&1

  cat > leaf.ext <<EOF
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=$san
authorityKeyIdentifier=keyid,issuer
subjectKeyIdentifier=hash
EOF

  openssl x509 -req -in "$LEAF_CSR" -CA "$CA_CRT" -CAkey "$CA_KEY" \
    -CAcreateserial -days "$LEAF_DAYS" -sha256 -extfile leaf.ext \
    -out "$crt" >/dev/null 2>&1

  # OpenSSL 3 writes the serial next to the CA as local-ca.srl (the older
  # local-ca.pem.srl is removed too, so a leftover from either naming goes).
  rm -f "$LEAF_CSR" leaf.ext "${CA_CRT%.pem}.srl" "$CA_CRT".srl 2>/dev/null || true
  chmod 600 "$key" "$CA_KEY"
  chmod 644 "$crt" "$CA_CRT"
}

# A host leaf: same root, one name, its own key. It never touches the cluster's
# `*.kub.local` pair, so issuing one cannot break a route that already works.
if [ -n "$HOST_NAME" ]; then
  if [ ! -f "$CA_KEY" ] || [ ! -f "$CA_CRT" ]; then
    echo "$OUT holds no CA: run this script once without --host first" >&2
    exit 1
  fi
  HOST_CRT="$HOST_NAME.pem"
  HOST_KEY="$HOST_NAME-key.pem"
  echo "==> reusing the existing CA: $OUT/$CA_CRT"
  echo "==> signing the leaf for: $HOST_NAME"
  sign_leaf "$HOST_NAME" "DNS:$HOST_NAME" "$HOST_CRT" "$HOST_KEY"

  echo
  echo "leaf   : $OUT/$HOST_CRT"
  openssl x509 -in "$HOST_CRT" -noout -subject -dates -ext subjectAltName | sed 's/^/         /'
  echo "root   : $OUT/$CA_CRT   (the root every client already trusts)"
  echo
  echo "==> put the pair on the host that serves the name:"
  printf '    ssh vronteix@<host> mkdir -p ~/dockers/certs\n    scp %s/%s vronteix@<host>:~/dockers/certs/tls.pem\n    scp %s/%s vronteix@<host>:~/dockers/certs/tls-key.pem\n' \
    "$OUT" "$HOST_CRT" "$OUT" "$HOST_KEY"
  echo "    (the key never enters git, chat or a ticket; the copy in ~/dockers is"
  echo "     what 'backup' carries off the host)"
  exit 0
fi

if [ -f "$CA_KEY" ] && [ "$FORCE_CA" = 0 ]; then
  echo "==> reusing the existing CA: $OUT/$CA_CRT"
  echo "    (signing a new leaf with the same root keeps every client working;"
  echo "     --force-ca makes a new root that each client must re-install)"
else
  echo "==> creating a new CA: $OUT/$CA_CRT"
  openssl req -x509 -newkey rsa:3072 -sha256 -days "$CA_DAYS" -nodes \
    -keyout "$CA_KEY" -out "$CA_CRT" \
    -subj "/O=ashulab internal/CN=ashulab internal CA" \
    -addext "basicConstraints=critical,CA:TRUE,pathlen:0" \
    -addext "keyUsage=critical,keyCertSign,cRLSign" \
    -addext "subjectKeyIdentifier=hash" >/dev/null 2>&1
fi

echo "==> signing the leaf for: ${SAN#DNS:}"
sign_leaf "kub.local" "$SAN" "$LEAF_CRT" "$LEAF_KEY"

echo
echo "leaf   : $OUT/$LEAF_CRT"
openssl x509 -in "$LEAF_CRT" -noout -subject -dates -ext subjectAltName | sed 's/^/         /'
echo "root   : $OUT/$CA_CRT   (install this once on every client)"
echo
echo "==> replace the Secret (Traefik reloads it without a restart):"
printf '    kubectl -n kube-system delete secret local-cert --ignore-not-found\n    kubectl -n kube-system create secret tls local-cert --save-config \\\n      --cert=%s/%s --key=%s/%s\n' \
  "$OUT" "$LEAF_CRT" "$OUT" "$LEAF_KEY"
echo "    (delete then create: the existing Secret is Opaque, and a Secret's type is immutable)"
echo
echo "==> verify what the cluster now offers:"
printf '    openssl s_client -connect 192.168.1.46:443 -servername calibre.kub.local \\\n      </dev/null 2>/dev/null | openssl x509 -noout -issuer -dates\n'
