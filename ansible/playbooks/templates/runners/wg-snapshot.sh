#!/bin/bash
# Written by ansible/pi/hosts-pi.sh; an edit here is reverted on the next setup.
# wg-easy keeps every peer, private key included, in one SQLite file. A plain
# copy taken while the panel writes can tear, so `.backup` is what reads a
# consistent image; the integrity check is what proves it before that image
# takes the place of the last good one. The two series are how an absence of a
# fresh snapshot becomes visible.
set -euo pipefail

WG_ROOT="${WG_ROOT:-}"
WG_ROOT="${WG_ROOT%/}"
STACK_DIR="/home/vronteix/dockers"
DB="$WG_ROOT$STACK_DIR/config/wg-easy.db"
OUT="$WG_ROOT@WG_SYNC_DIR@/wg-easy.db"
PROM="$WG_ROOT@TEXTFILE_DIR@/wg-vpn-snapshot.prom"

# The series go out both when the snapshot worked and when it did not: a run that
# stayed silent would look exactly like a run that never happened.
publish() {
    local ok="$1"
    mkdir -p "$(dirname "$PROM")"
    printf 'wg_vpn_snapshot_timestamp_seconds %s\nwg_vpn_snapshot_ok %s\n' \
        "$(date +%s)" "$ok" > "$PROM.tmp"
    chmod 0644 "$PROM.tmp"
    mv -f "$PROM.tmp" "$PROM"
}

# Both directories are this script's to create. The timer runs it as root on a
# host where the sync has not yet run, and a metric that silently never appears
# is the failure the alert is watching.
mkdir -p "$(dirname "$OUT")" "$(dirname "$PROM")"

# One argument on purpose: sqlite3 reads a dot command and its filename as one
# line. The copy lands beside the live file, never on top of it.
if ! sqlite3 "$DB" ".backup '$OUT.tmp'"; then
    publish 0
    exit 1
fi

if [ "$(sqlite3 "$OUT.tmp" 'pragma integrity_check;' 2>/dev/null || true)" != ok ]; then
    rm -f "$OUT.tmp"
    publish 0
    exit 1
fi

chmod 0640 "$OUT.tmp"
# Only the sync account may read the copy: it holds every peer's key. A test
# fixture runs as a normal user with no wg-sync group, where chown can only fail,
# so a failure here is tolerated: the mode is what this script guarantees and the
# ownership is setup's job.
chown root:wg-sync "$OUT.tmp" 2>/dev/null || true
mv -f "$OUT.tmp" "$OUT"

publish 1
