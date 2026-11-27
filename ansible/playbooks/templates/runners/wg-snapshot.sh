#!/bin/bash
# Written by ansible/pi/hosts-pi.sh; an edit here is reverted on the next setup.
# wg-easy keeps every peer, private key included, in one SQLite file. A plain
# copy taken while the panel writes can tear, so `.backup` is what reads a
# consistent image; the integrity check is what proves it before that image
# takes the place of the last good one. The two series are how an absence of a
# fresh snapshot becomes visible, and only a run that produced a usable copy
# moves the timestamp, so a host failing every minute keeps counting from the
# last image that worked.
set -euo pipefail

WG_ROOT="${WG_ROOT:-}"
WG_ROOT="${WG_ROOT%/}"
STACK_DIR="/home/vronteix/dockers"
DB="$WG_ROOT$STACK_DIR/config/wg-easy.db"
OUT="$WG_ROOT@WG_SYNC_DIR@/wg-easy.db"
PROM="$WG_ROOT@TEXTFILE_DIR@/wg-vpn-snapshot.prom"

# The series go out both when the snapshot worked and when it did not: a run that
# stayed silent would look exactly like a run that never happened. On a failure
# the timestamp is read back from the file rather than stamped with the clock:
# the staleness rule counts from it, so a fresh one would hide a snapshot that
# has been failing all along.
publish() {
    local ok="$1" timestamp=""
    mkdir -p "$(dirname "$PROM")"
    if [ "$ok" = 1 ]; then
        timestamp="$(date +%s)"
    elif [ -f "$PROM" ]; then
        timestamp="$(sed -n '/^wg_vpn_snapshot_timestamp_seconds /{s///;p;q;}' "$PROM")"
    fi
    {
        [ -z "$timestamp" ] ||
            printf 'wg_vpn_snapshot_timestamp_seconds %s\n' "$timestamp"
        printf 'wg_vpn_snapshot_ok %s\n' "$ok"
    } > "$PROM.tmp"
    chmod 0644 "$PROM.tmp"
    mv -f "$PROM.tmp" "$PROM"
}

# Both directories are this script's to create. The timer runs it as root on a
# host where the sync has not yet run, and a metric that silently never appears
# is the failure the alert is watching.
mkdir -p "$(dirname "$OUT")" "$(dirname "$PROM")"

# A missing database is the first tick on a fresh host, and sqlite3 answers one
# by creating an empty file and exiting 0, while an integrity check on nothing
# prints "ok". Publishing that would replace the last good snapshot with an
# empty one and lose the peer set silently, so the source is checked before the
# copy is asked for and the copy is checked after.
if [ ! -s "$DB" ]; then
    publish 0
    exit 1
fi

# One argument on purpose: sqlite3 reads a dot command and its filename as one
# line. The copy lands beside the live file, never on top of it, and a run that
# gives up here removes it: the file holds every peer's key.
if ! sqlite3 "$DB" ".backup '$OUT.tmp'"; then
    rm -f "$OUT.tmp"
    publish 0
    exit 1
fi

if [ ! -s "$OUT.tmp" ] ||
    [ "$(sqlite3 "$OUT.tmp" 'pragma integrity_check;' 2>/dev/null || true)" != ok ]; then
    rm -f "$OUT.tmp"
    publish 0
    exit 1
fi

chmod 0640 "$OUT.tmp"
# Only the sync account may read the copy: it holds every peer's key. A chown
# that fails on the real host leaves root:root 0640, which the sync cannot read,
# so the run has not produced the thing the cluster needs and says so. A test
# fixture runs as a normal user with no wg-sync group, where chown can only fail;
# WG_ROOT is set under that seam alone, and the 0640 mode is what it settles for.
if [ -z "$WG_ROOT" ]; then
    if ! chown root:wg-sync "$OUT.tmp"; then
        rm -f "$OUT.tmp"
        publish 0
        exit 1
    fi
fi
mv -f "$OUT.tmp" "$OUT"

publish 1
