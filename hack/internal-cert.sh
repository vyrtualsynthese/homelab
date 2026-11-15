#!/usr/bin/env bash
# Issue the cluster's default TLS certificate: the private `*.kub.local` one.
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
#
# Nothing here is committed: keys never enter the repository. Replacing the Secret
# takes effect at once, without a Traefik restart:
#
#   kubectl -n kube-system create secret tls local-cert \
#     --cert=DIR/kub.local.pem --key=DIR/kub.local-key.pem \
#     --dry-run=client -o yaml | kubectl apply -f -
#
# Why this script exists: `kube-system/local-cert` was last signed by hand with
# mkcert and expired on 2026-05-06, which went unnoticed for ten months. Run this
# again before the date it prints; the `local-cert` expiry is a candidate for a
# Prometheus rule.
set -euo pipefail

OUT="${HOME}/.config/secrets/kub-local"
FORCE_CA=0
CA_DAYS=3650
LEAF_DAYS=730
# One label only: `*.kub.local` covers calibre.kub.local, not a.b.kub.local.
SAN="DNS:*.kub.local,DNS:*.app.local,DNS:localhost,IP:127.0.0.1"

while [ $# -gt 0 ]; do
  case "$1" in
    --out) OUT="${2:?--out needs a directory}"; shift 2 ;;
    --force-ca) FORCE_CA=1; shift ;;
    -h|--help) sed -n '2,32p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
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
LEAF_CSR="kub.local.csr"

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
openssl req -new -newkey rsa:2048 -sha256 -nodes \
  -keyout "$LEAF_KEY" -out "$LEAF_CSR" \
  -subj "/CN=kub.local" >/dev/null 2>&1

cat > leaf.ext <<EOF
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=${SAN}
authorityKeyIdentifier=keyid,issuer
subjectKeyIdentifier=hash
EOF

openssl x509 -req -in "$LEAF_CSR" -CA "$CA_CRT" -CAkey "$CA_KEY" \
  -CAcreateserial -days "$LEAF_DAYS" -sha256 -extfile leaf.ext \
  -out "$LEAF_CRT" >/dev/null 2>&1

rm -f "$LEAF_CSR" leaf.ext "$CA_CRT".srl 2>/dev/null || true
chmod 600 "$CA_KEY" "$LEAF_KEY"
chmod 644 "$CA_CRT" "$LEAF_CRT"

echo
echo "leaf   : $OUT/$LEAF_CRT"
openssl x509 -in "$LEAF_CRT" -noout -subject -dates -ext subjectAltName | sed 's/^/         /'
echo "root   : $OUT/$CA_CRT   (install this once on every client)"
echo
echo "==> replace the Secret (Traefik reloads it without a restart):"
printf '    kubectl -n kube-system create secret tls local-cert \\\n      --cert=%s/%s --key=%s/%s \\\n      --dry-run=client -o yaml | kubectl apply -f -\n' \
  "$OUT" "$LEAF_CRT" "$OUT" "$LEAF_KEY"
echo
echo "==> verify what the cluster now offers:"
printf '    openssl s_client -connect 192.168.1.46:443 -servername calibre.kub.local \\\n      </dev/null 2>/dev/null | openssl x509 -noout -issuer -dates\n'
