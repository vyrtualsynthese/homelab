#!/bin/bash
# hosts-pi.sh — one script for the two edge Pis, because they are reflashed and
# not patched. A fresh image has no Ansible and no key, so the thing that puts
# the host back together cannot be Ansible.
#
#   render  <host>        print every file this script owns; change nothing
#   setup   <host>        write them, start the stack, print changed=N
#   verify  <host>        name every difference; exit 1 if there is one
#   backup  <dir> <host>  copy the state a reflash destroys, print the byte count
#   restore <dir> <host>  copy it back, then verify
#
# PI_ROOT points every verb at another directory, which is how the tests drive
# it without a Pi. It defaults to "/" — a real host.
#
# What this script never writes: pihole.env, wg-easy.env, .env. They hold the
# Pi-hole password and the WireGuard key, they are gitignored, and a script that
# wrote them would either commit a secret or invent one. It requires them and
# names the one that is missing.
#
# What it also does not invent: /boot/firmware/config.txt and cmdline.txt. After
# a reflash the image's own copies are right for the disk they were written to,
# and a remembered PARTUUID is a way to make a Pi unbootable from a distance.
# `backup` saves them, `restore` puts them back, and the repository keeps a copy
# once the operator has run one.
set -euo pipefail

HOSTS="rbp0 rbp1"
STACK_DIR="/home/vronteix/dockers"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATES="$(cd "$HERE/../playbooks/templates/runners" && pwd)"
ROOT="${PI_ROOT:-}"
ROOT="${ROOT%/}"
CHANGED=0

usage() {
    cat >&2 <<'USAGE'
usage: hosts-pi.sh render|setup|verify <host>
       hosts-pi.sh backup|restore <dir> <host>
hosts: rbp0 rbp1
USAGE
    exit 2
}

check_host() {
    local candidate="$1" known
    for known in $HOSTS; do
        if [ "$candidate" = "$known" ]; then
            return 0
        fi
    done
    echo "unknown host '$candidate'; this script knows: $HOSTS" >&2
    exit 2
}

compose_src() {
    echo "$TEMPLATES/$1/docker-compose.yml"
}

desired_hostname() {
    printf '%s\n' "$1"
}

desired_hosts() {
    printf '%s\n' \
        "127.0.0.1 localhost" \
        "127.0.1.1 $1" \
        "" \
        "::1 localhost ip6-localhost ip6-loopback" \
        "ff02::1 ip6-allnodes" \
        "ff02::2 ip6-allrouters"
}

require_env() {
    local missing=0 file
    for file in pihole.env wg-easy.env; do
        if [ ! -f "$ROOT$STACK_DIR/$file" ]; then
            echo "missing $ROOT$STACK_DIR/$file" >&2
            missing=1
        fi
    done
    if [ "$missing" -eq 1 ]; then
        echo "these hold secrets and are gitignored: write them once by hand" >&2
        exit 1
    fi
}

root_device() {
    if [ -r "$ROOT/proc/mounts" ]; then
        awk '$2 == "/" { print $1; exit }' "$ROOT/proc/mounts"
    else
        printf 'unknown (no %s)\n' "$ROOT/proc/mounts"
    fi
}

# One file, one counter of changes. Text is compared with its newlines trimmed,
# so a file written by an older run (or by hand) is a match when the lines are,
# and the write always ends the file with one newline.
write_text() {
    local dest="$1" content="$2"
    mkdir -p "$(dirname "$dest")"
    if [ -f "$dest" ] && [ "$(cat "$dest")" = "$content" ]; then
        return 0
    fi
    printf '%s\n' "$content" > "$dest"
    CHANGED=$((CHANGED + 1))
}

write_from_file() {
    local dest="$1" src="$2"
    mkdir -p "$(dirname "$dest")"
    if [ -f "$dest" ] && cmp -s "$src" "$dest"; then
        return 0
    fi
    cp "$src" "$dest"
    CHANGED=$((CHANGED + 1))
}

cmd_render() {
    local host="$1"
    echo "--- $(compose_src "$host")"
    cat "$(compose_src "$host")"
    echo "--- $STACK_DIR/docker-compose.yml (where the block above is written)"
    echo "--- /etc/hostname"
    desired_hostname "$host"
    echo "--- /etc/hosts"
    desired_hosts "$host"
    echo "--- $STACK_DIR/pihole.env and wg-easy.env"
    echo "required, never printed and never written: they hold the Pi-hole"
    echo "password and the WireGuard key, and both are gitignored"
}

cmd_setup() {
    local host="$1" current_name

    current_name=""
    if [ -f "$ROOT/etc/hostname" ]; then
        current_name="$(tr -d '[:space:]' < "$ROOT/etc/hostname")"
    fi
    if [ -n "$current_name" ] && [ "$current_name" != "$host" ]; then
        echo "this host calls itself '$current_name'; refusing to configure it as '$host'" >&2
        exit 1
    fi

    require_env

    write_from_file "$ROOT$STACK_DIR/docker-compose.yml" "$(compose_src "$host")"
    write_text "$ROOT/etc/hostname" "$(desired_hostname "$host")"
    write_text "$ROOT/etc/hosts" "$(desired_hosts "$host")"

    if [ -z "$ROOT" ]; then
        # --remove-orphans is what takes a service off a host when this template
        # stops declaring it: `up -d` starts what the file names and leaves
        # everything else running. Without the flag, the router this template no
        # longer declares would stay up on rbp0, dashboard on 8080 included.
        ( cd "$STACK_DIR" && docker compose up -d --remove-orphans >/dev/null )
        echo "stack: docker compose up -d --remove-orphans"
    else
        echo "stack: not started (PI_ROOT is set)"
    fi

    echo "changed=$CHANGED"
}

cmd_verify() {
    local host="$1" problems=0 file

    for file in pihole.env wg-easy.env; do
        if [ ! -f "$ROOT$STACK_DIR/$file" ]; then
            echo "MISSING $ROOT$STACK_DIR/$file"
            problems=1
        fi
    done

    if [ ! -f "$ROOT$STACK_DIR/docker-compose.yml" ]; then
        echo "MISSING $ROOT$STACK_DIR/docker-compose.yml"
        problems=1
    elif ! cmp -s "$(compose_src "$host")" "$ROOT$STACK_DIR/docker-compose.yml"; then
        echo "DIFFERS $ROOT$STACK_DIR/docker-compose.yml"
        diff -u "$ROOT$STACK_DIR/docker-compose.yml" "$(compose_src "$host")" || true
        problems=1
    fi

    if [ ! -f "$ROOT/etc/hostname" ]; then
        echo "MISSING $ROOT/etc/hostname"
        problems=1
    elif [ "$(tr -d '[:space:]' < "$ROOT/etc/hostname")" != "$host" ]; then
        echo "DIFFERS $ROOT/etc/hostname: $(cat "$ROOT/etc/hostname")"
        problems=1
    fi

    if [ ! -f "$ROOT/etc/hosts" ]; then
        echo "MISSING $ROOT/etc/hosts"
        problems=1
    elif [ "$(desired_hosts "$host")" != "$(cat "$ROOT/etc/hosts")" ]; then
        echo "DIFFERS $ROOT/etc/hosts"
        problems=1
    fi

    echo "root device: $(root_device)"

    if [ -z "$ROOT" ]; then
        local state
        state="$(cd "$STACK_DIR" && docker compose ps --format '{{.Name}} {{.State}}' 2>/dev/null || true)"
        if [ -n "$state" ]; then
            printf '%s\n' "$state"
            if printf '%s\n' "$state" | grep -qv ' running$'; then
                echo "NOT RUNNING: a service above is not in state running"
                problems=1
            fi
        fi
    fi

    if [ "$problems" -ne 0 ]; then
        exit 1
    fi
    echo "ok: $host matches the repository"
}

cmd_backup() {
    local dir="$1" host="$2" bytes

    mkdir -p "$dir"

    # Only meaningful on the Pi itself: the point is to keep the copy off the
    # disk that is about to be overwritten. PI_ROOT is a test or a mounted copy.
    if [ -z "$ROOT" ] && [ "$(stat -c %d "$dir")" = "$(stat -c %d "$STACK_DIR")" ]; then
        echo "refusing: $dir is on the same filesystem as $STACK_DIR" >&2
        exit 1
    fi

    mkdir -p "$dir/dockers"
    cp -a "$ROOT$STACK_DIR/." "$dir/dockers/"

    mkdir -p "$dir/etc"
    for file in hostname hosts; do
        if [ -f "$ROOT/etc/$file" ]; then
            cp -a "$ROOT/etc/$file" "$dir/etc/$file"
        fi
    done

    mkdir -p "$dir/boot"
    for file in config.txt cmdline.txt; do
        if [ -f "$ROOT/boot/firmware/$file" ]; then
            cp -a "$ROOT/boot/firmware/$file" "$dir/boot/$file"
        fi
    done

    # The boot order and the EEPROM release live in the board, not on the disk,
    # but they are the two facts a rebuilt Pi is checked against.
    if [ -z "$ROOT" ] && command -v rpi-eeprom-config >/dev/null 2>&1; then
        rpi-eeprom-config > "$dir/bootloader.txt" || true
    fi

    printf 'host: %s\nroot device: %s\n' "$host" "$(root_device)" > "$dir/host.txt"
    if [ -z "$ROOT" ]; then
        ( cd "$STACK_DIR" && docker compose ps > "$dir/docker-ps.txt" 2>&1 ) || true
    fi

    bytes="$(du -sb "$dir" | cut -f1)"
    echo "backed up $host to $dir ($bytes bytes)"
    echo "check: the Pi-hole database alone is more than 100 MB"
}

cmd_restore() {
    local dir="$1" host="$2"

    mkdir -p "$ROOT$STACK_DIR" "$ROOT/boot/firmware" "$ROOT/etc"
    cp -a "$dir/dockers/." "$ROOT$STACK_DIR/"
    for file in hostname hosts; do
        if [ -f "$dir/etc/$file" ]; then
            cp -a "$dir/etc/$file" "$ROOT/etc/$file"
        fi
    done
    for file in config.txt cmdline.txt; do
        if [ -f "$dir/boot/$file" ]; then
            cp -a "$dir/boot/$file" "$ROOT/boot/firmware/$file"
        fi
    done

    cmd_verify "$host"
}

main() {
    local verb="${1:-}"
    [ -n "$verb" ] || usage
    shift

    case "$verb" in
        render)
            [ "$#" -eq 1 ] || usage
            check_host "$1"
            cmd_render "$1"
            ;;
        setup)
            [ "$#" -eq 1 ] || usage
            check_host "$1"
            cmd_setup "$1"
            ;;
        verify)
            [ "$#" -eq 1 ] || usage
            check_host "$1"
            cmd_verify "$1"
            ;;
        backup|restore)
            [ "$#" -eq 2 ] || usage
            check_host "$2"
            "cmd_$verb" "$1" "$2"
            ;;
        *)
            usage
            ;;
    esac
}

main "$@"
