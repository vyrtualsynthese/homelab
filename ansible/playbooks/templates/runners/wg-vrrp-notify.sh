#!/bin/bash
# Written by ansible/pi/hosts-pi.sh; an edit here is reverted on the next setup.
# keepalived calls this with the transition it made — master|backup|fault|stop.
# The state file is read by the failover path; the series is what Prometheus
# scrapes, so one Pi carrying the shared address and the other not is visible.
set -eu

WG_ROOT="${WG_ROOT:-}"
WG_ROOT="${WG_ROOT%/}"
STATE_FILE="$WG_ROOT@VRRP_STATE_FILE@"
TEXTFILE_DIR="$WG_ROOT@TEXTFILE_DIR@"
SERIES="$TEXTFILE_DIR/wg-vpn-vrrp.prom"

case "${1:-}" in
    master|backup|fault|stop) state="$1" ;;
    *)
        echo "usage: $0 master|backup|fault|stop" >&2
        exit 2
        ;;
esac

mkdir -p "$(dirname "$STATE_FILE")" "$TEXTFILE_DIR"

printf '%s\n' "$state" > "$STATE_FILE.tmp"
mv "$STATE_FILE.tmp" "$STATE_FILE"

if [ "$state" = master ]; then
    value=1
else
    value=0
fi
printf 'wg_vpn_vrrp_master %s\n' "$value" > "$SERIES.tmp"
chmod 0644 "$SERIES.tmp"
mv "$SERIES.tmp" "$SERIES"
