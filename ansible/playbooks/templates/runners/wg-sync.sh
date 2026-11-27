#!/bin/bash
# Written by ansible/pi/hosts-pi.sh; an edit here is reverted on the next setup.
# One wg-easy database backs both edge Panels. The authority keeps it, the
# standby copies it every minute so it can answer with peers added while it was
# not the one carrying the address, and this script is the only writer of that
# database other than the Panel itself.
#
# Two rules are what make an unattended run every minute safe:
#
# - It refuses to overwrite a database that was edited on this host. The stamp
#   holds one line per table, hashed, as the database stood after the last
#   install this script made. A line that moved means somebody changed the local
#   copy, and their change is named on stderr rather than erased: the operator
#   decides, and WG_SYNC_FORCE=1 is that decision.
# - It does nothing while this host holds the shared address. Restarting the
#   Panel here is what drops the tunnel, so a run in that state reports healthy
#   and leaves everything alone.
#
# The series are how a standby that stopped updating becomes visible. The
# timestamp moves only on a run that left the database current, so a host that
# fails every minute keeps counting from the last run that worked.
set -euo pipefail

WG_ROOT="${WG_ROOT:-}"
WG_ROOT="${WG_ROOT%/}"
STACK_DIR="/home/vronteix/dockers"
DB="$WG_ROOT$STACK_DIR/config/wg-easy.db"
KEY="$WG_ROOT/etc/wg-sync/id_ed25519"
STAMP="$WG_ROOT@WG_SYNC_DIR@/last-installed"
# The copy lives beside the snapshot and is removed on every exit that did not
# install it: it holds every peer's private key.
DL="$WG_ROOT@WG_SYNC_DIR@/incoming.db"
PROM="$WG_ROOT@TEXTFILE_DIR@/wg-vpn-sync.prom"
VRRP_STATE="$WG_ROOT@VRRP_STATE_FILE@"
AUTHORITY="@WG_SYNC_AUTHORITY@"
WG_SYNC_FORCE="${WG_SYNC_FORCE:-0}"

# The tables the Panel writes and the column each is ordered by. They are one
# list on purpose: a line added here is a line in the stamp, and the five cannot
# drift apart. A peer added, a key rotated or an interface renamed moves exactly
# one line, and that table's name is what the operator reads when the two hosts
# disagree.
TABLES="clients_table:id hooks_table:id interfaces_table:name user_configs_table:id users_table:id"

# `<table> <sha256>` per line, over the rows in a fixed order: the same bytes on
# both hosts for the same content, which is what makes a stamp portable.
fingerprint() {
    local db="$1" pair table key line

    for pair in $TABLES; do
        table="${pair%%:*}"
        key="${pair##*:}"
        if ! line="$(sqlite3 -readonly "$db" "select * from $table order by $key;" |
            sha256sum)"; then
            return 1
        fi
        printf '%s %s\n' "$table" "${line%% *}"
    done
}

# The line of one table from a fingerprint held in a variable.
fp_line() {
    local table="$1" fingerprint="$2"
    sed -n "s/^$table //p" <<<"$fingerprint"
}

# The tables whose line differs, one name per line. The caller sends it to
# stderr: it is a message for a human, not for the next command.
name_differences() {
    local left="$1" right="$2" pair table

    for pair in $TABLES; do
        table="${pair%%:*}"
        if [ "$(fp_line "$table" "$left")" != "$(fp_line "$table" "$right")" ]; then
            printf '%s\n' "$table"
        fi
    done
}

# Both series go out on every run that reaches this function: a run that stayed
# silent would look exactly like a run that never happened, and the alert reads
# `ok`. On a failure the timestamp is read back from the file rather than stamped
# with the clock, because the staleness rule counts from it: a fresh one would
# hide a sync that has been failing all along.
publish() {
    local ok="$1" diverged="$2" timestamp=""
    mkdir -p "$(dirname "$PROM")"
    if [ "$ok" = 1 ]; then
        timestamp="$(date +%s)"
    elif [ -f "$PROM" ]; then
        timestamp="$(sed -n '/^wg_vpn_sync_timestamp_seconds /{s///;p;q;}' "$PROM")"
    fi
    {
        [ -z "$timestamp" ] ||
            printf 'wg_vpn_sync_timestamp_seconds %s\n' "$timestamp"
        printf 'wg_vpn_sync_ok %s\n' "$ok"
        printf 'wg_vpn_sync_diverged %s\n' "$diverged"
    } > "$PROM.tmp"
    chmod 0644 "$PROM.tmp"
    mv -f "$PROM.tmp" "$PROM"
}

# One exit for every path that leaves the database as it was. The fetched copy is
# removed first, whatever the reason: it is the one file here that holds every
# peer's key.
fail() {
    local diverged="$1"
    shift
    rm -f "$DL"
    echo "sync: $*" >&2
    publish 0 "$diverged"
    exit 1
}

# Both directories are this script's to create. The timer runs it as root on a
# host where the sync has not run yet, and a series that silently never appears
# is the failure the alert is watching.
mkdir -p "$(dirname "$DL")" "$(dirname "$PROM")"

# The host that holds the address is the host clients are using. Fetching is
# harmless, but the swap stops the Panel, and that drops every tunnel this host
# is carrying. So it defers, and reports healthy: the copy it has is as current
# as it needs to be while the authority is the one being read.
if [ -f "$VRRP_STATE" ] &&
    [ "$(tr -d '[:space:]' < "$VRRP_STATE")" = master ]; then
    publish 1 0
    exit 0
fi

# The authority hands out one file through one restricted key: the account's
# authorized_keys runs `cat` on the snapshot and nothing else, so a range of
# addresses and a command are the whole of what this connection can do.
if ! ssh -i "$KEY" -o BatchMode=yes -o StrictHostKeyChecking=accept-new \
    "wg-sync@$AUTHORITY" > "$DL"; then
    fail 0 "could not fetch the database from wg-sync@$AUTHORITY"
fi

# A zero-byte answer is what a failed copy looks like once the redirect has
# already created the file, and an integrity check on a torn one prints anything
# but ok. Neither may take the place of the database.
if [ ! -s "$DL" ] ||
    [ "$(sqlite3 -readonly "$DL" 'pragma integrity_check;' 2>/dev/null || true)" != ok ]; then
    fail 0 "what $AUTHORITY sent is empty, or not a sound database"
fi

local_fp="$(fingerprint "$DB")" ||
    fail 0 "could not read $DB; is wg-easy's database where the stack puts it?"

stamp_fp=""
if [ -s "$STAMP" ]; then
    stamp_fp="$(cat "$STAMP")"
fi

# Two refusals, both about the same thing: this host's copy is not the one the
# stamp describes, so writing over it could erase a peer the operator added here.
# A host that has never installed is the same case with nothing to compare, and
# it is the first tick after the account was made.
if [ -z "$stamp_fp" ] && [ "$WG_SYNC_FORCE" != 1 ]; then
    fail 1 "no stamp at $STAMP: this host has never installed the authority's database" \
        "and the local copy may hold peers that would be lost"
fi
if [ -n "$stamp_fp" ] && [ "$local_fp" != "$stamp_fp" ] &&
    [ "$WG_SYNC_FORCE" != 1 ]; then
    {
        echo "sync: $DB differs from the stamp at $STAMP; these tables moved:"
        name_differences "$local_fp" "$stamp_fp"
        echo "sync: refusing to overwrite this host's copy. WG_SYNC_FORCE=1 installs anyway."
    } >&2
    rm -f "$DL"
    publish 0 1
    exit 1
fi

snapshot_fp="$(fingerprint "$DL")" ||
    fail 0 "the database $AUTHORITY sent could not be read"

# Nothing anywhere has changed: no table differs between the local copy, the
# stamp and what arrived. Comparing all three is what keeps a forced run able to
# repair a local edit while the authority itself stands still.
if [ -n "$stamp_fp" ] && [ "$snapshot_fp" = "$stamp_fp" ] &&
    [ "$local_fp" = "$stamp_fp" ]; then
    rm -f "$DL"
    publish 1 0
    exit 0
fi

COMPOSE=(docker compose -f "$STACK_DIR/docker-compose.yml")

# The Panel holds the database open and writes to it, so it is stopped before the
# file under it is replaced. The window is a few seconds on a host no client is
# using: this host does not hold the address, which the defer above established.
if ! "${COMPOSE[@]}" stop wg-easy; then
    fail 0 "could not stop wg-easy; the database was not replaced"
fi

# 0600 root: a fixture runs as a normal account with no root to give the file to,
# and WG_ROOT is set under that seam alone.
if [ -n "$WG_ROOT" ]; then
    installed=0
    install -m 0600 "$DL" "$DB" || installed=1
else
    installed=0
    install -m 0600 -o root -g root "$DL" "$DB" || installed=1
fi
if [ "$installed" -ne 0 ]; then
    "${COMPOSE[@]}" start wg-easy || true
    fail 0 "could not install the fetched database over $DB"
fi

# A Panel that does not come back is the one failure the operator has to see now:
# the file is in place and the tunnels are down until somebody starts it.
if ! "${COMPOSE[@]}" start wg-easy; then
    fail 0 "installed the database but wg-easy did not start; the Panel is down"
fi

# The stamp is the fingerprint of the fetched file, taken before the swap, not a
# re-read of the database afterwards: `install` copies the bytes that were just
# checked and fingerprinted, and its exit code says whether that landed, so a
# second read could only prove what the first already did. Temp-then-mv: a
# half-written stamp would refuse every run.
printf '%s\n' "$snapshot_fp" > "$STAMP.tmp"
chmod 0644 "$STAMP.tmp"
mv -f "$STAMP.tmp" "$STAMP"

rm -f "$DL"
publish 1 0
