#!/bin/bash
# hosts-pi.sh — one script for the two edge Pis, because they are reflashed and
# not patched. A fresh image has no Ansible and no key, so the thing that puts
# the host back together cannot be Ansible.
#
#   render  <host>        print every file this script owns; change nothing
#   setup   <host>        install the engine, write them, start the stack, print changed=N
#   verify  <host>        name every difference; exit 1 if one needs an apply
#   backup  <dir> <host>  copy the state a reflash destroys, print the byte count
#   restore <dir> <host>  copy it back, then verify
#
# PI_ROOT points every verb at another directory, which is how the tests drive
# it without a Pi. It defaults to "/" — a real host.
#
# What this script never writes: pihole.env. It holds the Pi-hole password, it
# is gitignored, and a script that wrote it would either commit a secret or
# invent one. It requires it and names it when it is missing. The WireGuard
# side has no environment file since wg-easy 15: its settings live in the
# panel's own database under the stack directory, which `backup` copies.
#
# What it also does not invent: /boot/firmware/config.txt and cmdline.txt. After
# a reflash the image's own copies are right for the disk they were written to,
# and a remembered PARTUUID is a way to make a Pi unbootable from a distance.
# `backup` saves them, `restore` puts them back, and the repository keeps a copy
# once the operator has run one.
set -euo pipefail

HOSTS="rbp0 rbp1"
STACK_DIR="/home/vronteix/dockers"

# The engine and the compose plugin, from Docker's own repository. Pinned by
# band, because nothing in this repository bumps an apt pin: an exact version
# ages silently, which is what 28.3.2 on both Pis was. Measured 2026-11-25 in
# the current release's suite for arm64: docker-ce 5:29.10.1, containerd.io
# 2.4.2, docker-compose-plugin 5:8.0.
DOCKER_PIN="5:29.*"
CONTAINERD_PIN="2.4.*"
COMPOSE_PIN="5:8.*"
DOCKER_KEYRING="/etc/apt/keyrings/docker.asc"
DOCKER_SOURCES="/etc/apt/sources.list.d/docker.sources"
DOCKER_GROUP_USER="vronteix"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATES="$(cd "$HERE/../playbooks/templates/runners" && pwd)"
ROOT="${PI_ROOT:-}"
ROOT="${ROOT%/}"
CHANGED=0
CHANGED_THIS_WRITE=0

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

wg_udp_port() {
    case "$1" in
        rbp0) printf '51821\n' ;;
        rbp1) printf '51822\n' ;;
    esac
}

desired_compose() {
    sed "s/@WG_UDP_PORT@/$(wg_udp_port "$1")/g" "$TEMPLATES/docker-compose.yml"
}

desired_hostname() {
    printf '%s\n' "$1"
}

# /etc/hosts is left to cloud-init, which rewrites it from its own template on
# every boot (`manage_etc_hosts: true` in these images). A script that owned it
# would be overwritten by the next boot and would then report a difference
# nothing could settle; the rendered file names the host and localhost, which is
# all this stack needs from it.

require_env() {
    local missing=0 file
    for file in pihole.env; do
        if [ ! -f "$ROOT$STACK_DIR/$file" ]; then
            echo "missing $ROOT$STACK_DIR/$file" >&2
            missing=1
        fi
    done
    if [ "$missing" -eq 1 ]; then
        echo "it holds a secret and is gitignored. After a reflash, 'restore' is" >&2
        echo "what brings it back from the backup; on a fresh host, write it once" >&2
        echo "by hand." >&2
        exit 1
    fi
}

# The release's own suite name, read from the running image. A reflash onto the
# next LTS then names its own suite instead of pointing apt at a codename that
# no longer exists; an unreadable file stops the run rather than writing an
# empty suite into a repository line.
suite_name() {
    local codename=""
    if [ -r /etc/os-release ]; then
        codename="$(. /etc/os-release && printf '%s' "${UBUNTU_CODENAME:-${VERSION_CODENAME:-}}")"
    fi
    if [ -z "$codename" ]; then
        echo "cannot read a release codename from /etc/os-release; not writing an apt suite without one" >&2
        exit 1
    fi
    printf '%s\n' "$codename"
}

# The version of a package that is actually installed, or empty when it is not.
# `dpkg-query -W` also prints a package that was removed and left its config
# behind (`deinstall ok config-files`), which is not an install.
installed_version() {
    local status version
    read -r status version <<< "$(dpkg-query -W -f='${db:Status-Abbrev} ${Version}' "$1" 2>/dev/null || true)"
    if [ "$status" = "ii" ]; then
        printf '%s' "$version"
    fi
}

# The band is a shell glob on purpose: 5:29.10.1-1~ubuntu.26.04 matches 5:29.*
# with its epoch and packaging suffix, and 5:30.0 does not. Do not quote $2.
version_in_band() {
    case "$1" in
        ${2}) return 0 ;;
        *) return 1 ;;
    esac
}

# The four packages and their bands, one pair per line.
engine_pairs() {
    printf '%s\n' \
        "docker-ce $DOCKER_PIN" \
        "docker-ce-cli $DOCKER_PIN" \
        "containerd.io $CONTAINERD_PIN" \
        "docker-compose-plugin $COMPOSE_PIN"
}

# Every package installed and inside its band. This is about the packages only;
# engine_repo_present is the other half of the converged state.
engine_versions_in_band() {
    local pkg band version
    while read -r pkg band; do
        version="$(installed_version "$pkg")"
        [ -n "$version" ] || return 1
        version_in_band "$version" "$band" || return 1
    done < <(engine_pairs)
}

engine_repo_present() {
    [ -f "$DOCKER_KEYRING" ] && [ -f "$DOCKER_SOURCES" ]
}

# The group is its own step, not a tail of the install: a run interrupted
# between apt and usermod leaves an engine that is already converged, and the
# next run has to finish the group rather than return early.
ensure_docker_group() {
    case " $(id -nG "$DOCKER_GROUP_USER" 2>/dev/null || true) " in
        *" docker "*) ;;
        *)
            usermod -aG docker "$DOCKER_GROUP_USER"
            CHANGED=$((CHANGED + 1))
            echo "engine: $DOCKER_GROUP_USER joined the docker group"
            ;;
    esac
}

# Docker's repository and signing key, written only when they are missing or
# changed. Nothing here restarts a container: it is what a later apt run reads.
# Sets ENGINE_SUITE for the caller's message.
prepare_docker_repo() {
    local suite tmp

    suite="$(suite_name)"
    ENGINE_SUITE="$suite"
    install -d -m 0755 "$(dirname "$DOCKER_KEYRING")"
    tmp="$(mktemp)"
    if ! curl -fsSL --max-time 30 https://download.docker.com/linux/ubuntu/gpg -o "$tmp"; then
        rm -f "$tmp"
        echo "cannot download Docker's signing key from download.docker.com" >&2
        exit 1
    fi
    if [ -f "$DOCKER_KEYRING" ] && cmp -s "$tmp" "$DOCKER_KEYRING"; then
        rm -f "$tmp"
    else
        install -m 0644 "$tmp" "$DOCKER_KEYRING"
        rm -f "$tmp"
        CHANGED=$((CHANGED + 1))
    fi

    # Both Pis are arm64 boards, so the architecture is pinned with the suite
    # rather than read: the repository mixes amd64, arm64, armhf and more.
    write_abs "$DOCKER_SOURCES" "Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $suite
Components: stable
Architectures: arm64
Signed-By: $DOCKER_KEYRING"
}

# A fresh image has no runtime; that is what this installs. An engine that is
# already there is named and left alone: upgrading it restarts the daemon that
# answers the house's DNS, and on these hosts a reflash is what moves it.
ensure_engine() {
    local pkg band version missing=0 outside=0

    if [ -n "$ROOT" ]; then
        echo "engine: not touched (PI_ROOT is set)"
        return 0
    fi
    if [ "$(id -u)" -ne 0 ]; then
        echo "the engine install needs root; run this with sudo" >&2
        exit 1
    fi

    if engine_versions_in_band; then
        if engine_repo_present; then
            echo "engine: the pinned $DOCKER_PIN / $COMPOSE_PIN set is installed"
        else
            # The packages are right and only the repository line is gone, which
            # is what a later `apt-get update` needs; no package is touched.
            echo "engine: the packages are in band; restoring Docker's repository line"
            prepare_docker_repo
        fi
        ensure_docker_group
        return 0
    fi

    while read -r pkg band; do
        version="$(installed_version "$pkg")"
        if [ -z "$version" ]; then
            missing=1
        elif ! version_in_band "$version" "$band"; then
            outside=1
            echo "engine: NOTE $pkg $version is outside the pinned $band; this script does not upgrade an engine in place"
        fi
    done < <(engine_pairs)

    if [ "$missing" -eq 0 ]; then
        ensure_docker_group
        return 0
    fi
    if [ "$outside" -eq 1 ]; then
        echo "engine: refusing: a package above is installed outside its pin while another is missing; reflash, or align the pins" >&2
        exit 1
    fi

    prepare_docker_repo

    # Pinned to the bands above. A suite that carries a newer major only fails
    # here, naming both the suite and the pins, rather than silently moving.
    if ! DEBIAN_FRONTEND=noninteractive apt-get update -qq ||
       ! DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
           "docker-ce=$DOCKER_PIN" "docker-ce-cli=$DOCKER_PIN" \
           "containerd.io=$CONTAINERD_PIN" "docker-compose-plugin=$COMPOSE_PIN"; then
        echo "the engine install failed: check that the network reaches download.docker.com, that it carries the $ENGINE_SUITE suite, and that the pins above still match it" >&2
        exit 1
    fi
    CHANGED=$((CHANGED + 1))
    echo "engine: installed $DOCKER_PIN / $COMPOSE_PIN (suite $ENGINE_SUITE)"

    ensure_docker_group
}

# One line per difference the engine can show. A version outside its band is a
# NOTE, not a failure: setup does not upgrade an engine in place, so there is
# nothing for the operator to fix from here — the reflash is what moves it.
engine_drift() {
    local pkg band version problems=0

    if [ -n "$ROOT" ]; then
        echo "engine: not checked (PI_ROOT is set)"
        return 0
    fi

    while read -r pkg band; do
        version="$(installed_version "$pkg")"
        if [ -z "$version" ]; then
            echo "MISSING package $pkg"
            problems=1
        elif ! version_in_band "$version" "$band"; then
            echo "engine: NOTE $pkg $version is outside the pinned $band; this script does not upgrade an engine in place"
        fi
    done < <(engine_pairs)

    # Our repository paths are only expected under our own pin: a host carrying
    # an older engine may hold its repository somewhere else, and setup would
    # not rewrite that either.
    if engine_versions_in_band && ! engine_repo_present; then
        echo "MISSING Docker's repository line or signing key"
        problems=1
    fi

    if ! command -v docker >/dev/null 2>&1; then
        echo "MISSING the docker command"
        problems=1
    elif ! docker compose version >/dev/null 2>&1; then
        echo "MISSING the docker compose plugin"
        problems=1
    fi

    case " $(id -nG "$DOCKER_GROUP_USER" 2>/dev/null || true) " in
        *" docker "*) ;;
        *)
            echo "DIFFERS $DOCKER_GROUP_USER is not in the docker group"
            problems=1
            ;;
    esac

    return "$problems"
}

root_device() {
    if [ -r "$ROOT/proc/mounts" ]; then
        awk '$2 == "/" { print $1; exit }' "$ROOT/proc/mounts"
    else
        printf 'unknown (no %s)\n' "$ROOT/proc/mounts"
    fi
}

# A bind mount whose source is missing makes Docker create a directory where the
# file should be. Writing into it fails with an error that names the path but not
# the cause, so say the cause and stop. Nothing is deleted: that directory is
# where a future file would live, and it may hold something.
refuse_directory() {
    if [ -d "$1" ]; then
        echo "$1 is a directory: Docker creates one when a bind mount's source is missing." >&2
        echo "Remove it and run setup again; this script deletes nothing." >&2
        exit 1
    fi
}

# A file is written only when its bytes differ: the same comparison `verify`
# makes, so a file `setup` leaves alone is one `verify` accepts. The write always
# ends the file with exactly one newline.
write_abs() {
    local dest="$1" content="$2"
    mkdir -p "$(dirname "$dest")"
    refuse_directory "$dest"
    CHANGED_THIS_WRITE=0
    if [ -f "$dest" ] && cmp -s <(printf '%s\n' "$content") "$dest"; then
        return 0
    fi
    printf '%s\n' "$content" > "$dest"
    CHANGED=$((CHANGED + 1))
    CHANGED_THIS_WRITE=1
}

cmd_render() {
    local host="$1"
    echo "--- $STACK_DIR/docker-compose.yml"
    desired_compose "$host"
    echo "--- $STACK_DIR/etc-pihole-updatelists/pihole-updatelists.conf"
    cat "$TEMPLATES/pihole-updatelists.conf"
    echo "--- $STACK_DIR/etc-dnsmasq.d/02-my-wildcard-dns.conf"
    cat "$TEMPLATES/02-my-wildcard-dns.conf"
    echo "--- engine, installed by setup on a real host (not under PI_ROOT)"
    echo "repository: https://download.docker.com/linux/ubuntu, suite read from /etc/os-release"
    echo "signed by: $DOCKER_KEYRING"
    echo "pinned: docker-ce=$DOCKER_PIN docker-ce-cli=$DOCKER_PIN containerd.io=$CONTAINERD_PIN docker-compose-plugin=$COMPOSE_PIN"
    echo "--- /etc/hostname"
    desired_hostname "$host"
    echo "--- /etc/hosts"
    echo "left to cloud-init, which rewrites it on every boot"
    echo "--- $STACK_DIR/pihole.env"
    echo "required, never printed and never written: it holds the Pi-hole"
    echo "password, and it is gitignored"
    echo "--- $STACK_DIR/config/wg-easy.db"
    echo "wg-easy 15's own database: panel account, host, port, DNS, peers. Not"
    echo "written here, and copied by 'backup' with the rest of the stack"
}

cmd_setup() {
    local host="$1" current_name pihole_changed=0

    current_name=""
    if [ -f "$ROOT/etc/hostname" ]; then
        current_name="$(tr -d '[:space:]' < "$ROOT/etc/hostname")"
    fi
    if [ -n "$current_name" ] && [ "$current_name" != "$host" ]; then
        echo "this host calls itself '$current_name'; refusing to configure it as '$host'" >&2
        exit 1
    fi

    require_env

    ensure_engine

    write_abs "$ROOT$STACK_DIR/docker-compose.yml" "$(desired_compose "$host")"
    # Both files are bind-mounted by pihole, and a bind mount is resolved when
    # the container starts: a file written under a running pihole is invisible
    # to it until the container restarts. Hence the flag, and the recreate below.
    write_abs "$ROOT$STACK_DIR/etc-pihole-updatelists/pihole-updatelists.conf" \
        "$(cat "$TEMPLATES/pihole-updatelists.conf")"
    pihole_changed="$CHANGED_THIS_WRITE"
    write_abs "$ROOT$STACK_DIR/etc-dnsmasq.d/02-my-wildcard-dns.conf" \
        "$(cat "$TEMPLATES/02-my-wildcard-dns.conf")"
    if [ "$CHANGED_THIS_WRITE" -eq 1 ]; then
        pihole_changed=1
    fi
    write_abs "$ROOT/etc/hostname" "$(desired_hostname "$host")"

    if [ -z "$ROOT" ]; then
        # --remove-orphans is what takes a service off a host when this template
        # stops declaring it: `up -d` starts what the file names and leaves
        # everything else running. Without the flag, the router this template no
        # longer declares would stay up on rbp0, dashboard on 8080 included.
        ( cd "$STACK_DIR" && docker compose up -d --remove-orphans >/dev/null )
        echo "stack: docker compose up -d --remove-orphans"
        # `up -d <service>` converges that service alone, so the run above is the
        # one that applies a definition or image change to the other three. This
        # one is here because a bind mount is resolved when the container starts:
        # a file written above is invisible to a pihole that kept running.
        if [ "$pihole_changed" -eq 1 ]; then
            ( cd "$STACK_DIR" && docker compose up -d --force-recreate pihole >/dev/null )
            echo "stack: docker compose up -d --force-recreate pihole"
        fi
    else
        echo "stack: not started (PI_ROOT is set)"
    fi

    echo "changed=$CHANGED"
}

# A file this script owns, compared as bytes so the line endings it writes are
# part of the contract rather than a diff nobody meant to make.
check_text() {
    local dest="$1" content="$2"
    if [ ! -f "$dest" ]; then
        echo "MISSING $dest"
        return 1
    fi
    if ! cmp -s <(printf '%s\n' "$content") "$dest"; then
        echo "DIFFERS $dest"
        diff -u "$dest" <(printf '%s\n' "$content") || true
        return 1
    fi
    return 0
}

cmd_verify() {
    local host="$1" problems=0 file

    for file in pihole.env; do
        if [ ! -f "$ROOT$STACK_DIR/$file" ]; then
            echo "MISSING $ROOT$STACK_DIR/$file"
            problems=1
        fi
    done

    if [ ! -f "$ROOT$STACK_DIR/docker-compose.yml" ]; then
        echo "MISSING $ROOT$STACK_DIR/docker-compose.yml"
        problems=1
    elif ! cmp -s <(desired_compose "$host") "$ROOT$STACK_DIR/docker-compose.yml"; then
        echo "DIFFERS $ROOT$STACK_DIR/docker-compose.yml"
        diff -u "$ROOT$STACK_DIR/docker-compose.yml" <(desired_compose "$host") || true
        problems=1
    fi

    if ! check_text "$ROOT$STACK_DIR/etc-pihole-updatelists/pihole-updatelists.conf" \
        "$(cat "$TEMPLATES/pihole-updatelists.conf")"; then
        problems=1
    fi
    if ! check_text "$ROOT$STACK_DIR/etc-dnsmasq.d/02-my-wildcard-dns.conf" \
        "$(cat "$TEMPLATES/02-my-wildcard-dns.conf")"; then
        problems=1
    fi

    if [ ! -f "$ROOT/etc/hostname" ]; then
        echo "MISSING $ROOT/etc/hostname"
        problems=1
    elif [ "$(tr -d '[:space:]' < "$ROOT/etc/hostname")" != "$host" ]; then
        echo "DIFFERS $ROOT/etc/hostname: $(cat "$ROOT/etc/hostname")"
        problems=1
    fi

    echo "root device: $(root_device)"

    engine_drift || problems=1

    if [ -z "$ROOT" ]; then
        # --all, and the declared service list compared with what is up: `ps`
        # without it hides a stopped container, so a fully down stack printed
        # nothing and this function answered "ok".
        local expected actual names expected_list names_list
        expected="$(cd "$STACK_DIR" && docker compose config --services 2>/dev/null | sort || true)"
        actual="$(cd "$STACK_DIR" && docker compose ps --all --format '{{.Service}} {{.State}}' 2>/dev/null || true)"
        if [ -n "$actual" ]; then
            printf '%s\n' "$actual"
        fi
        names="$(printf '%s\n' "$actual" | awk 'NF { print $1 }' | sort)"
        if [ "$expected" != "$names" ]; then
            expected_list="${expected//$'\n'/ }"
            names_list="${names//$'\n'/ }"
            echo "NOT RUNNING: declared [${expected_list}] but found [${names_list}]"
            problems=1
        elif printf '%s\n' "$actual" | awk '$2 != "running" { found = 1 } END { exit !found }'; then
            echo "NOT RUNNING: a service above is not in state running"
            problems=1
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
    for file in hostname; do
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
    for file in hostname; do
        if [ -f "$dir/etc/$file" ]; then
            cp -a "$dir/etc/$file" "$ROOT/etc/$file"
        fi
    done
    for file in config.txt cmdline.txt; do
        if [ ! -f "$dir/boot/$file" ]; then
            continue
        fi
        if [ "$file" = "cmdline.txt" ] && ! cmdline_matches_this_disk "$dir/boot/$file"; then
            echo "kept $ROOT/boot/firmware/cmdline.txt as it is: the backup names another disk's PARTUUID" >&2
            echo "the copy is in $dir/boot/cmdline.txt; compare it by hand before using it" >&2
            continue
        fi
        cp -a "$dir/boot/$file" "$ROOT/boot/firmware/$file"
    done

    cmd_verify "$host"
}

# A cmdline.txt carries the PARTUUID of the disk the image was written to. That
# is right on the disk it names and unbootable on any other, and the machine that
# would fix it is the one that will not start. So the line is written only when
# it names the disk this host is running from; when that cannot be read (a
# PI_ROOT fixture, a host without blkid), it is not written at all.
cmdline_matches_this_disk() {
    local wanted current device
    wanted="$(sed -n 's/.*PARTUUID=\([^[:space:]]*\).*/\1/p' "$1" | head -1)"
    if [ -z "$wanted" ]; then
        return 0
    fi
    if [ -n "$ROOT" ] || ! command -v blkid >/dev/null 2>&1; then
        return 1
    fi
    device="$(root_device)"
    case "$device" in
        /dev/*) ;;
        *) return 1 ;;
    esac
    current="$(blkid -s PARTUUID -o value "$device" 2>/dev/null || true)"
    [ -n "$current" ] && [ "$current" = "$wanted" ]
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

# Sourced by the tests, which drive the pure helpers without running a verb; a
# direct call still runs one.
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    main "$@"
fi
