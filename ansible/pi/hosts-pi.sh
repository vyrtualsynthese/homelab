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
# What this script never writes: pihole.env and certs/. The first holds the
# Pi-hole password, the second the panel's TLS leaf and key. Both are
# gitignored, and a script that wrote them would either commit a secret or
# invent one. It requires them and names them when they are missing. The
# WireGuard side has no environment file since wg-easy 15: its settings live in
# the panel's own database under the stack directory, which `backup` copies,
# and its sign-in is its own account.
#
# What it also does not invent: /boot/firmware/config.txt and cmdline.txt. After
# a reflash the image's own copies are right for the disk they were written to,
# and a remembered PARTUUID is a way to make a Pi unbootable from a distance.
# `backup` saves them, `restore` puts them back, and the repository keeps a copy
# once the operator has run one.
set -euo pipefail

HOSTS="rbp0 rbp1"
HUMAN_USER="vronteix"
STACK_DIR="/home/$HUMAN_USER/dockers"

# The nightly stream: one repository per host, written by this host and no one
# else. The credential is a file the operator writes (setup never invents one),
# and PI_RESTIC lets the tests stand a recorder in for the binary.
BACKUP_ENV="/etc/restic/edge.env"
BACKUP_ENV_KEYS="AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY RESTIC_PASSWORD RESTIC_REPOSITORY "
STAGING_DIR="/var/lib/pi-backup"
# The query log is a log (385 MB on rbp0, rewritten every minute); listsCache is
# re-downloaded by `pihole -g`; gravity_old.db is the updater's copy of the
# database the same snapshot already holds. The manual USB copy keeps all three.
BACKUP_EXCLUDES="pihole-FTL.db* listsCache gravity_old.db"
RESTIC_BIN="${PI_RESTIC:-/usr/local/bin/restic}"
# The chart's retention, copied because a host outside the cluster cannot read
# cluster/helm/backup/values.yaml at prune time. A test fails when the two drift.
RETENTION_DAILY=7
RETENTION_WEEKLY=4
RETENTION_MONTHLY=12
# restic is installed from the project's own release, not apt: the two images
# carry 0.16.4 and 0.12.1, and a repository wants one writer version.
RESTIC_VERSION="0.19.1"
RESTIC_SHA256="a5f64aaab53d51e311fa3829124c5b703f2d14cf187d8640b6be3b2b49376465"
RESTIC_URL="https://github.com/restic/restic/releases/download/v${RESTIC_VERSION}/restic_${RESTIC_VERSION}_linux_arm64.bz2"
# The 04:00 root job this replaces ran `scripts/backup.sh` against an S3 bucket
# whose keys were revoked; its RESTIC_/AWS_ variables sit dead in /etc/environment.
LEGACY_CRON_MATCH="scripts/backup.sh"
LEGACY_ENV_PATTERN="^[[:space:]]*(export[[:space:]]+)?(RESTIC_REPOSITORY|RESTIC_PASSWORD|AWS_ACCESS_KEY_ID|AWS_SECRET_ACCESS_KEY)="

# SSH and sudo: the two settings ansible/roles/accounts owns on the four cluster
# nodes, written here because the layer cannot reach the Pis until they are
# reflashed (card #481). Measured on both hosts on 2026-11-26: the main
# sshd_config said `no`, cloud-init's own drop-in said `yes` and sorts first, and
# /etc/sudoers carried the group grant. The strings match the role's, and
# test_the_pi_and_the_ansible_layer_* keep the two records from drifting.
SSHD_CONFIG="/etc/ssh/sshd_config"
SSHD_DROPIN="/etc/ssh/sshd_config.d/00-homelab.conf"
SUDOERS="/etc/sudoers"
CLOUD_INIT_SUDOERS="/etc/sudoers.d/90-cloud-init-users"
ADMINS_GROUP="admins"
ADMINS_GRANT="%admins ALL=(ALL) NOPASSWD: ALL"

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
# The VPN panel's database. It holds every peer's private key and the panel
# account's password hash, and a fresh container writes it 0644 root:root, so
# any local account that reads it owns the tunnels. `setup` narrows the copy the
# panel made; `verify` reads the mode back. A reflash, or a `config` directory
# the stack recreated, is what brings the readable copy back.
WG_DB="$STACK_DIR/config/wg-easy.db"
WG_DB_MODE="600"
# `docker compose up -d` returns when the container has started, and the panel
# creates its database during its own start. A file that has not appeared yet is
# waited for this long before the stack is called broken.
WG_DB_WAIT=30
# Pi-hole's leaf. Its web server drops to the image's `pihole` account, which
# is uid 1000 — the same number as HUMAN_USER on a fresh image — and it reads
# the certificate as that account. A leaf installed 0600 root:root in a 0700
# directory is unreadable to it, and FTL does not fall back: it fails to load
# the certificate, refuses to listen on 80 and 443, and the panel goes dark.
# `setup` therefore hands this one file to the account the stack runs as, and
# leaves the Caddy pair in the same directory root-only.
CERT_DIR="$STACK_DIR/certs"
PIHOLE_PEM="$CERT_DIR/pihole.pem"
CADDY_KEY="$CERT_DIR/tls-key.pem"
CERT_DIR_MODE="700"
PIHOLE_PEM_MODE="600"
CADDY_PEM_MODE="600"
# The resolvers both Pis carry, on both hosts: rbp0 first because it is the
# source of truth, the second address because either one answering is enough.
# Written here rather than in the netplan template so no ansible YAML repeats an
# address; test_the_resolver_list_is_the_two_edge_addresses checks it against
# the inventory, which stays the record.
PIHOLE_DNS_SERVERS="192.168.1.22, 192.168.1.38"

# The host firewall's sources. The script runs on the host, where the inventory
# is not, so these are written here and compared with the records by
# test_the_pi_and_the_ansible_layer_allow_the_same_ports. `LAN_CIDR` is the /24
# the cluster's first address sits in (`lan_cidr` in group_vars/all.yml); the
# four node addresses are the cluster group in inventory order, because the
# exporters are host-network and only those four may read them; the tunnel is
# the subnet inside each wg-easy container's wg0, measured 10.8.0.1/24 on both
# on 2026-11-26; the panel network is the compose network that container joins,
# and it is where a peer's traffic arrives from, because wg-easy masquerades it
# to its own address there (measured on rbp0 on 2026-11-27).
LAN_CIDR="192.168.1.0/24"
CLUSTER_ADDRESSES="192.168.1.41 192.168.1.27 192.168.1.46 192.168.1.48"
VPN_CIDR="10.8.0.0/24"
PANEL_CIDR="172.30.0.0/24"
# The two edge hosts' addresses, in the same order as `HOSTS`, so the VRRP rule
# can name the *other* Pi: an advertisement from a host's own address would be a
# second master on the same link. `FIREWALL_HOST` is the verb's host argument,
# set by `main()`; the tests set it in the environment of the sourced script.
EDGE_ADDRESSES="192.168.1.22 192.168.1.38"
FIREWALL_HOST="${FIREWALL_HOST:-}"
# The WireGuard listener, one number for both hosts: they share one panel
# database, so a config minted on either names this port and a client keeps
# dialling it whichever Pi answers.
WG_UDP_PORT="51821"
# The address a client keeps using whichever Pi answers. keepalived moves it:
# rbp0 holds it while it is up, rbp1 takes it after rbp0 misses its
# advertisements. One instance per host, unicast to the other's address, so the
# two never need multicast on the LAN.
VIP_ADDRESS="192.168.1.9"
KEEPALIVED_CONF="/etc/keepalived/keepalived.conf"
# The notify script keepalived runs on every transition, and the two paths it
# writes: the state file the failover path reads, and the textfile directory
# node-exporter serves to Prometheus.
WG_LIB="/usr/local/lib/wg-vpn"
VRRP_STATE_FILE="/run/wg-vpn/vrrp.state"
TEXTFILE_DIR="/var/lib/node-exporter/textfile"
# The database the edge sync reads while this Pi is down, and the script and
# units that keep it a minute old. The copy is root:wg-sync 0640: the sync runs
# as wg-sync and no other local account may read the peers' private keys.
WG_SYNC_DIR="/var/lib/wg-easy-sync"
WG_SNAPSHOT_SCRIPT="$WG_LIB/wg-snapshot.sh"
WG_SNAPSHOT_SERVICE="/etc/systemd/system/wg-vpn-snapshot.service"
WG_SNAPSHOT_TIMER="/etc/systemd/system/wg-vpn-snapshot.timer"
# The other half of the sync: the standby pulls the authority's copy every
# minute and installs it when it moved. WG_SYNC_KEY is the private half of the
# one key the pair uses, WG_SYNC_AUTH_KEYS is the authorized_keys line that
# restricts that key to `cat` on the snapshot and one source address. Neither is
# written here: this script cannot mint a key, and one it invented would be a key
# in git or a key nobody has. It fixes their mode when they exist and names the
# missing half.
WG_SYNC_SCRIPT="$WG_LIB/wg-sync.sh"
WG_SYNC_SERVICE="/etc/systemd/system/wg-vpn-sync.service"
WG_SYNC_TIMER="/etc/systemd/system/wg-vpn-sync.timer"
WG_SYNC_KEY="/etc/wg-sync/id_ed25519"
WG_SYNC_AUTH_KEYS="/var/lib/wg-sync/.ssh/authorized_keys"
WG_SYNC_STAMP="$WG_SYNC_DIR/last-installed"
WG_SYNC_ACCOUNT_HOME="/var/lib/wg-sync"
WG_SYNC_AUTHORITY="192.168.1.22"
WG_SYNC_STANDBY="192.168.1.38"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${PI_ROOT:-}"
ROOT="${ROOT%/}"
# Two places, because the script is run from both: the ansible tree, which is the
# only copy a fresh host has, and /usr/local/sbin, where `setup` installs it as the
# systemd timers' entry point and where `verify` is run on a host that has no tree.
# A hard `cd` to the first made every verb die at load on a real host, before
# dispatch, so the nightly timer could never have written a snapshot.
TEMPLATES=""
for candidate in "$HERE/../playbooks/templates/runners" "$ROOT/usr/local/lib/hosts-pi/templates"; do
    if [ -d "$candidate" ]; then
        TEMPLATES="$(cd "$candidate" && pwd)"
        break
    fi
done
CHANGED=0
CHANGED_THIS_WRITE=0

usage() {
    cat >&2 <<'USAGE'
usage: hosts-pi.sh render|setup|verify|stream|prune <host>
       hosts-pi.sh vpn-sync <host> [--force]
       hosts-pi.sh backup|restore <dir> <host>
hosts: rbp0 rbp1
USAGE
    exit 2
}

require_templates() {
    if [ -z "$TEMPLATES" ]; then
        echo "no playbooks/templates/runners beside $0: run this verb from the" \
            "ansible tree (ansible/pi/hosts-pi.sh), where the templates travel" >&2
        exit 1
    fi
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

# `HOSTS` and `EDGE_ADDRESSES` are index-aligned; both walk the pair of lists
# once and print the address at the host's index. An unknown host is refused by
# `check_host`, the same way every verb refuses one.
host_address() {
    local host="$1" known

    check_host "$host"
    set -- $EDGE_ADDRESSES
    for known in $HOSTS; do
        if [ "$known" = "$host" ]; then
            printf '%s\n' "$1"
            return 0
        fi
        shift
    done
}

peer_address() {
    local host="$1" known

    check_host "$host"
    set -- $EDGE_ADDRESSES
    for known in $HOSTS; do
        if [ "$known" != "$host" ]; then
            printf '%s\n' "$1"
            return 0
        fi
        shift
    done
}

# The name the VPN panel answers on. One label under the internal wildcard, so
# Pi-hole's existing record sends it here (02-my-wildcard-dns.conf carries the
# two specific addresses) and the certificate is a per-host leaf, not the shared
# `*.kub.local` one.
panel_host() {
    case "$1" in
        rbp0) printf 'vpn-rbp0.kub.local\n' ;;
        rbp1) printf 'vpn-rbp1.kub.local\n' ;;
    esac
}

# The name Pi-hole serves its panel and API on. Its own leaf, for the same
# reason as the VPN panel's: the shared `*.kub.local` key on an edge host would
# be a key to every internal name. Pi-hole answers it from
# 02-my-wildcard-dns.conf, and the cluster's exporter is the client that needs
# the name to verify.
pihole_host() {
    case "$1" in
        rbp0) printf 'pihole-rbp0.kub.local\n' ;;
        rbp1) printf 'pihole-rbp1.kub.local\n' ;;
    esac
}

# The stack, the same file on both hosts: the port is a literal and every other
# value is shared, so 'verify' compares the same bytes on either Pi.
desired_compose() {
    cat "$TEMPLATES/docker-compose.yml"
}

# The panel's TLS endpoint, rendered per host because the name differs and the
# certificate must match it.
desired_caddyfile() {
    sed "s/@PANEL_HOST@/$(panel_host "$1")/g" "$TEMPLATES/Caddyfile"
}

# The settings both Pi-holes share. One template, no per-host value: the two
# hosts must end up byte-identical, and 'verify' compares bytes.
desired_pihole_settings() {
    cat "$TEMPLATES/pihole.settings"
}

# The resolver list, the same on both hosts: rbp0 first, the pair as the fallback.
# Written under /etc/netplan and applied by setup. Cloud-init has held no seed for
# this file since the image was built in 2023, so the repository can own it.
desired_netplan() {
    sed "s/@PIHOLE_DNS_SERVERS@/$PIHOLE_DNS_SERVERS/g" "$TEMPLATES/50-cloud-init.yaml"
}

# systemd-resolved's drop-in. Netplan holds the two Pi-hole addresses, so this
# names no server of its own; what it does is keep the stub listener off, so
# /etc/resolv.conf lists those two and a container reads them rather than
# falling back to Docker's 8.8.8.8.
desired_resolved() {
    cat "$TEMPLATES/90-pihole-resolvers.conf"
}

desired_hostname() {
    printf '%s\n' "$1"
}

# rbp0 is the source of truth and keeps the address while it is up; rbp1 is the
# backup. A host this script does not know is refused by `check_host`.
vrrp_state() {
    case "$1" in
        rbp0) printf 'MASTER\n' ;;
        rbp1) printf 'BACKUP\n' ;;
        *) check_host "$1" ;;
    esac
}

vrrp_priority() {
    case "$1" in
        rbp0) printf '150\n' ;;
        rbp1) printf '100\n' ;;
        *) check_host "$1" ;;
    esac
}

# The VRRP instance, rendered per host: the state, the priority, and the two
# addresses differ; everything else is shared, so the two files differ only in
# what they must. preempt_delay keeps a rebooted rbp0 from taking the address
# back from a healthy rbp1 the moment it starts advertising.
desired_keepalived() {
    local host="$1"
    sed -e "s/@HOST@/$host/g" \
        -e "s/@VRRP_STATE@/$(vrrp_state "$host")/g" \
        -e "s/@VRRP_PRIORITY@/$(vrrp_priority "$host")/g" \
        -e "s/@OWN_ADDRESS@/$(host_address "$host")/g" \
        -e "s/@PEER_ADDRESS@/$(peer_address "$host")/g" \
        -e "s|@WG_LIB@|$WG_LIB|g" \
        "$TEMPLATES/keepalived.conf"
}

# The notify script, the same bytes on both hosts: its two paths are the only
# values, and they are shared, so `verify` compares the same file either way.
desired_vrrp_notify() {
    sed -e "s|@VRRP_STATE_FILE@|$VRRP_STATE_FILE|g" \
        -e "s|@TEXTFILE_DIR@|$TEXTFILE_DIR|g" \
        "$TEMPLATES/wg-vrrp-notify.sh"
}

# The snapshot script, the same bytes on both hosts: its two paths are the only
# values and they are shared, so `verify` compares the same file either way.
desired_snapshot() {
    sed -e "s|@WG_SYNC_DIR@|$WG_SYNC_DIR|g" \
        -e "s|@TEXTFILE_DIR@|$TEXTFILE_DIR|g" \
        "$TEMPLATES/wg-snapshot.sh"
}

# The oneshot's unit names the script, so it carries the library path rather than
# a second copy of it that a move of WG_LIB would leave behind.
desired_snapshot_service() {
    sed -e "s|@WG_LIB@|$WG_LIB|g" \
        "$TEMPLATES/systemd/wg-vpn-snapshot.service"
}

# The pull's script, the same bytes on both hosts. Its paths and the authority's
# address are the only values; both are shared, so `verify` compares the same file
# either way and a host that swapped roles needs no new render.
desired_sync() {
    sed -e "s|@WG_SYNC_DIR@|$WG_SYNC_DIR|g" \
        -e "s|@VRRP_STATE_FILE@|$VRRP_STATE_FILE|g" \
        -e "s|@TEXTFILE_DIR@|$TEXTFILE_DIR|g" \
        -e "s|@WG_SYNC_AUTHORITY@|$WG_SYNC_AUTHORITY|g" \
        "$TEMPLATES/wg-sync.sh"
}

# The oneshot's unit names the script, so it carries the library path rather than
# a second copy of it that a move of WG_LIB would leave behind.
desired_sync_service() {
    sed -e "s|@WG_LIB@|$WG_LIB|g" \
        "$TEMPLATES/systemd/wg-vpn-sync.service"
}

# The drop-in that closes password logins. It is numbered 00 on purpose: Ubuntu's
# sshd_config includes sshd_config.d/*.conf near its top and sshd takes the first
# value it reads, so cloud-init's 50-cloud-init.conf (`PasswordAuthentication
# yes`, and it is 0600 so nothing notices it) wins over anything lower in the
# main file. Ours sorts ahead of it, which is what makes `no` the answer.
desired_sshd_dropin() {
    cat <<'EOF'
# Written by ansible/pi/hosts-pi.sh; an edit here is reverted on the next setup.
PasswordAuthentication no
X11Forwarding no
EOF
}

# The two timers. Both run the installed copy of this script, because systemd
# starts them from / and not from a checkout; both are oneshot so a slow restic
# run is a job, not a service that stays "active" for a day. The 15 minutes of
# jitter keep the two hosts off the bucket's API at the same second.
desired_backup_service() {
    cat <<EOF
# Written by ansible/pi/hosts-pi.sh; an edit here is reverted on the next setup.
[Unit]
Description=Nightly edge backup stream

[Service]
Type=oneshot
EnvironmentFile=$BACKUP_ENV
ExecStart=/usr/local/sbin/hosts-pi.sh stream $1
Nice=10
TimeoutStartSec=1h
EOF
}

desired_backup_timer() {
    cat <<'EOF'
# Written by ansible/pi/hosts-pi.sh; an edit here is reverted on the next setup.
[Unit]
Description=Nightly edge backup stream

[Timer]
OnCalendar=*-*-* 04:30:00 Europe/Paris
RandomizedDelaySec=15min
Persistent=true

[Install]
WantedBy=timers.target
EOF
}

desired_prune_service() {
    cat <<EOF
# Written by ansible/pi/hosts-pi.sh; an edit here is reverted on the next setup.
[Unit]
Description=Weekly prune of the edge backup stream

[Service]
Type=oneshot
EnvironmentFile=$BACKUP_ENV
ExecStart=/usr/local/sbin/hosts-pi.sh prune $1
Nice=10
TimeoutStartSec=2h
EOF
}

desired_prune_timer() {
    cat <<'EOF'
# Written by ansible/pi/hosts-pi.sh; an edit here is reverted on the next setup.
[Unit]
Description=Weekly prune of the edge backup stream

[Timer]
OnCalendar=Sun *-*-* 05:30:00 Europe/Paris
Persistent=true

[Install]
WantedBy=timers.target
EOF
}

# /etc/hosts is left to cloud-init, which rewrites it from its own template on
# every boot (`manage_etc_hosts: true` in these images). A script that owned it
# would be overwritten by the next boot and would then report a difference
# nothing could settle; the rendered file names the host and localhost, which is
# all this stack needs from it.

require_env() {
    local missing=0 file
    for file in pihole.env certs/tls.pem certs/tls-key.pem certs/pihole.pem; do
        if [ ! -f "$ROOT$STACK_DIR/$file" ]; then
            echo "missing $ROOT$STACK_DIR/$file" >&2
            missing=1
        fi
    done
    if [ "$missing" -eq 1 ]; then
        echo "pihole.env holds the Pi-hole panel password, and certs/ the TLS" >&2
        echo "leaves and key minted by 'hack/internal-cert.sh --host <name>'." >&2
        echo "Both are gitignored. After a reflash, 'restore' is what brings" >&2
        echo "them back from the backup; on a fresh host, write or copy them" >&2
        echo "once by hand." >&2
        exit 1
    fi
}

# The lockout guard, and the same one roles/accounts makes: by the time the
# drop-in is written the password is gone, so the question has to be asked first.
# What is asked here is narrower than the role's, because this script owns no key
# list — the Pi's keys are the operator's, written by the image's user-data and by
# `make adopt` on the cluster nodes. It refuses only when there is no key at all,
# which is the one state in which turning the password off is a lockout.
require_login_key() {
    local keys="$ROOT/home/$HUMAN_USER/.ssh/authorized_keys"
    if [ -s "$keys" ] &&
       grep -Eq '^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp256|sk-ssh-ed25519@openssh\.com) ' "$keys"; then
        return 0
    fi
    echo "refusing to close password logins: $keys holds no public key." >&2
    echo "This run writes PasswordAuthentication no, and a host with no key and no" >&2
    echo "password has no way in. Put one of the lines from" >&2
    echo "ansible/inventory/group_vars/all.yml (trusted_ssh_keys) into that file first." >&2
    exit 1
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
    case " $(id -nG "$HUMAN_USER" 2>/dev/null || true) " in
        *" docker "*) ;;
        *)
            usermod -aG docker "$HUMAN_USER"
            CHANGED=$((CHANGED + 1))
            echo "engine: $HUMAN_USER joined the docker group"
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

    case " $(id -nG "$HUMAN_USER" 2>/dev/null || true) " in
        *" docker "*) ;;
        *)
            echo "DIFFERS $HUMAN_USER is not in the docker group"
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

# One active directive in the main sshd_config. A commented example is left alone:
# sshd ignores it, and rewriting it would edit Ubuntu's documentation rather than
# the setting — the trap roles/accounts documents for its own patterns. Returns 0
# when the file moved, 1 when it already said this, so the caller knows whether a
# reload is owed.
set_sshd_directive() {
    local directive="$1" value="$2" file="$ROOT$SSHD_CONFIG" before
    if [ ! -f "$file" ]; then
        return 1
    fi
    before="$(cat "$file")"
    if grep -qE "^${directive}[[:space:]]" "$file"; then
        sed -i -E "s/^${directive}[[:space:]].*/${directive} ${value}/" "$file"
    else
        printf '%s %s\n' "$directive" "$value" >> "$file"
    fi
    if [ "$before" = "$(cat "$file")" ]; then
        return 1
    fi
    CHANGED=$((CHANGED + 1))
    echo "sshd: $directive $value in $SSHD_CONFIG"
    return 0
}

# The passwordless grant, in the order roles/accounts uses: the line first, the
# group after. `%sudo` stays as Ubuntu ships it, so the human account keeps a
# password on sudo — which is the point of removing the grant. The edit is
# validated before it is installed, because a broken sudoers takes every
# account's sudo away at once, including the run that is writing it.
ensure_sudo_policy() {
    local tmp

    if [ -f "$ROOT$CLOUD_INIT_SUDOERS" ]; then
        rm -f "$ROOT$CLOUD_INIT_SUDOERS"
        CHANGED=$((CHANGED + 1))
        echo "sudo: removed $CLOUD_INIT_SUDOERS, cloud-init's grant for the retired ubuntu account"
    fi

    if [ -f "$ROOT$SUDOERS" ] && grep -qxF "$ADMINS_GRANT" "$ROOT$SUDOERS"; then
        tmp="$(mktemp)"
        grep -vxF "$ADMINS_GRANT" "$ROOT$SUDOERS" > "$tmp" || true
        if [ ! -s "$tmp" ]; then
            rm -f "$tmp"
            echo "refusing: removing the grant would leave $SUDOERS empty; the file is not what this script expects" >&2
            exit 1
        fi
        if [ -z "$ROOT" ]; then
            if ! visudo -cf "$tmp" >/dev/null; then
                rm -f "$tmp"
                echo "refusing: the edited $SUDOERS does not pass visudo; the file is untouched" >&2
                exit 1
            fi
            install -m 0440 -o root -g root "$tmp" "$ROOT$SUDOERS"
        else
            cat "$tmp" > "$ROOT$SUDOERS"
            chmod 0440 "$ROOT$SUDOERS"
        fi
        rm -f "$tmp"
        CHANGED=$((CHANGED + 1))
        echo "sudo: removed the $ADMINS_GROUP passwordless grant from $SUDOERS"
    fi

    if [ -n "$ROOT" ]; then
        echo "sudo: the $ADMINS_GROUP group is not touched (PI_ROOT is set)"
    elif getent group "$ADMINS_GROUP" >/dev/null 2>&1; then
        groupdel "$ADMINS_GROUP"
        CHANGED=$((CHANGED + 1))
        echo "sudo: removed the $ADMINS_GROUP group"
    fi
}

# The SSH half: a drop-in numbered ahead of cloud-init's, the two directives in
# the main file so the two records agree, and one reload when either moved.
ensure_sshd_policy() {
    local changed=0

    write_abs "$ROOT$SSHD_DROPIN" "$(desired_sshd_dropin)"
    if [ "$CHANGED_THIS_WRITE" -eq 1 ]; then
        chmod 0644 "$ROOT$SSHD_DROPIN"
        [ -n "$ROOT" ] || chown root:root "$ROOT$SSHD_DROPIN"
        changed=1
        echo "sshd: wrote $SSHD_DROPIN"
    fi

    if set_sshd_directive PasswordAuthentication no; then changed=1; fi
    if set_sshd_directive X11Forwarding no; then changed=1; fi

    if [ -z "$ROOT" ] && [ "$changed" -eq 1 ]; then
        systemctl reload ssh
        echo "sshd: reloaded"
    fi
}

# The edge rule set, the same one ansible/roles/firewall/vars/main.yml holds for
# the `edge` group. One line per rule: port|proto|source|comment. The comment is
# ufw's own, so `ufw show added` and `verify` compare the same text the operator
# would read in the host's rule list.
#
# What is deliberately absent: every port a container publishes. Docker's DNAT
# and the CNI rewrite in NAT and FORWARD before INPUT is consulted, so 51821/udp
# (wg-easy) and 8443/tcp (caddy) are governed by Docker, not by this file, and
# 9902/tcp (smartctl-exporter) stays reachable whether ufw is on or off. A rule
# here would filter nothing and read as protection.
#
# 443 is here because Pi-hole is the other case: it runs with network_mode: host,
# so its HTTPS panel reaches INPUT, and `hack/check_pihole_parity.py` dials it.
# wg-easy masquerades a peer's traffic to its own address on the compose network
# before it leaves, so ufw sees `172.30.0.2` and the four tunnel rules match
# nothing today; the two `panel` rules are what let a peer reach Pi-hole, and
# without them the remote client has no resolver at all.
# 9100 and 9323 are host-network exporters that Prometheus scrapes from a pod
# egressing as `nas`; the failure without a rule is silent.
#
# VRRP is a protocol, not a port: the port field is empty for that row, and
# `ufw_rule_line` drops `to any port` when it is. The source is the other edge
# host's address, never this one's.
firewall_rule_table() {
    cat <<'EOF'
22|tcp|lan|ssh from the LAN, key-only
80|tcp|lan|the Pi-hole web interface
443|tcp|lan|Pi-hole HTTPS web interface
53|tcp|lan|LAN DNS
53|udp|lan|LAN DNS
53|tcp|vpn|DNS for clients on the WireGuard tunnel
53|udp|vpn|DNS for clients on the WireGuard tunnel
80|tcp|vpn|the Pi-hole web interface from the tunnel
443|tcp|vpn|Pi-hole HTTPS web interface from the tunnel
53|tcp|panel|DNS for peers, from the wg-easy container after masquerade
53|udp|panel|DNS for peers, from the wg-easy container after masquerade
9100|tcp|nodes|node-exporter, scraped by a pod that egresses as its node
9323|tcp|nodes|the Docker metrics endpoint daemon.json opens on 0.0.0.0
|vrrp|peer|VRRP advertisements from the other edge host
EOF
}

# A named source set, resolved to the addresses it stands for. One rule per
# address, which is what roles/firewall/tasks/resolve.yml does on the nodes.
firewall_source_addresses() {
    case "$1" in
        lan) printf '%s\n' "$LAN_CIDR" ;;
        nodes) printf '%s\n' $CLUSTER_ADDRESSES ;;
        vpn) printf '%s\n' "$VPN_CIDR" ;;
        panel) printf '%s\n' "$PANEL_CIDR" ;;
        peer) printf '%s\n' "$(peer_address "$FIREWALL_HOST")" ;;
        *)
            echo "unknown firewall source '$1'" >&2
            exit 1
            ;;
    esac
}

# address|port|proto|comment, in the order the rules are declared.
firewall_entries() {
    local port proto source comment address
    while IFS='|' read -r port proto source comment; do
        while IFS= read -r address; do
            printf '%s|%s|%s|%s\n' "$address" "$port" "$proto" "$comment"
        done < <(firewall_source_addresses "$source")
    done < <(firewall_rule_table)
}

# The line `ufw show added` and `ufw status` carry, so applying and checking
# name the same thing.
ufw_rule_line() {
    local address="$1" port="$2" proto="$3" comment="$4"
    if [ -z "$port" ]; then
        printf "ufw allow from %s proto %s comment '%s'\n" \
            "$address" "$proto" "$comment"
        return 0
    fi
    printf "ufw allow from %s to any port %s proto %s comment '%s'\n" \
        "$address" "$port" "$proto" "$comment"
}

desired_firewall_commands() {
    local address port proto comment
    while IFS='|' read -r address port proto comment; do
        ufw_rule_line "$address" "$port" "$proto" "$comment"
    done < <(firewall_entries)
}

# Rules first, then the defaults, then enable: a host that takes the default
# deny before its SSH rule loses the session that was still adding it. `allow
# routed` is not decoration — Docker's containers egress through FORWARD, and a
# deny there takes every one of them off the network. The SSH rule is the LAN's,
# so run this from the LAN or the console: a run over the tunnel has no rule to
# lose the session to, and sudo drops SSH_CLIENT, so no check can see it.
ensure_firewall() {
    local added status address port proto comment rule

    if [ -n "$ROOT" ]; then
        echo "firewall: not applied (PI_ROOT is set)"
        return 0
    fi
    if ! command -v ufw >/dev/null 2>&1; then
        DEBIAN_FRONTEND=noninteractive apt-get update -qq
        DEBIAN_FRONTEND=noninteractive apt-get install -y ufw >/dev/null
        CHANGED=$((CHANGED + 1))
        echo "firewall: installed ufw"
    fi

    added="$(ufw show added 2>/dev/null || true)"
    while IFS='|' read -r address port proto comment; do
        rule="$(ufw_rule_line "$address" "$port" "$proto" "$comment")"
        if ! grep -qxF "$rule" <<<"$added"; then
            if [ -z "$port" ]; then
                ufw allow from "$address" proto "$proto" comment "$comment" >/dev/null
                CHANGED=$((CHANGED + 1))
                echo "firewall: allowed proto $proto from $address"
            else
                ufw allow from "$address" to any port "$port" proto "$proto" \
                    comment "$comment" >/dev/null
                CHANGED=$((CHANGED + 1))
                echo "firewall: allowed $port/$proto from $address"
            fi
        fi
    done < <(firewall_entries)

    status="$(ufw status verbose 2>/dev/null || true)"
    if ! grep -qx 'Default: deny (incoming), allow (outgoing), allow (routed)' <<<"$status"; then
        ufw default deny incoming >/dev/null
        ufw default allow routed >/dev/null
        CHANGED=$((CHANGED + 1))
        echo "firewall: default deny incoming, allow outgoing and routed"
    fi
    if ! grep -qx 'Status: active' <<<"$status"; then
        ufw --force enable >/dev/null
        CHANGED=$((CHANGED + 1))
        echo "firewall: enabled"
    fi
}

# What `verify` reads. `ufw show added` is the rule list as declared, comments
# included, which is the form the script applies and the form the operator reads;
# `status` is what makes it live.
firewall_drift() {
    local status added rule problems=0

    if [ -n "$ROOT" ]; then
        echo "firewall: not checked (PI_ROOT is set)"
        return 0
    fi
    status="$(ufw status verbose 2>/dev/null || true)"
    if ! grep -qx 'Status: active' <<<"$status"; then
        echo "DIFFERS the host firewall is not active"
        problems=1
    fi
    if ! grep -qx 'Default: deny (incoming), allow (outgoing), allow (routed)' <<<"$status"; then
        echo "DIFFERS the firewall defaults are not deny incoming / allow routed"
        problems=1
    fi
    added="$(ufw show added 2>/dev/null || true)"
    while IFS= read -r rule; do
        if ! grep -qxF "$rule" <<<"$added"; then
            echo "MISSING $rule"
            problems=1
        fi
    done < <(desired_firewall_commands)
    [ "$problems" -eq 0 ]
}

# keepalived moves the shared address and sqlite3 is how a run reads the panel's
# database without the panel. Both are Ubuntu's own packages: the base image
# carries neither, and a missing binary would otherwise fail the first state
# transition silently.
ensure_sync_packages() {
    local packages="" package

    if [ -n "$ROOT" ]; then
        echo "keepalived: packages not installed (PI_ROOT is set)"
        return 0
    fi
    for package in keepalived sqlite3; do
        command -v "$package" >/dev/null 2>&1 || packages="$packages $package"
    done
    if [ -z "$packages" ]; then
        return 0
    fi
    DEBIAN_FRONTEND=noninteractive apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y $packages >/dev/null
    CHANGED=$((CHANGED + 1))
    echo "keepalived: installed$packages"
}

# The instance and the script it calls, written only when their bytes differ, so
# a run over a host that already matches restarts nothing. keepalived reads its
# configuration once at start: a rewritten file is invisible until the unit is
# restarted, which is why the flag matters — a restart drops the address for a
# second on the Pi that holds it.
ensure_keepalived() {
    local host="$1" changed=0 notify="$WG_LIB/wg-vrrp-notify.sh"

    write_abs "$ROOT$KEEPALIVED_CONF" "$(desired_keepalived "$host")"
    if [ "$CHANGED_THIS_WRITE" -eq 1 ]; then
        chmod 0644 "$ROOT$KEEPALIVED_CONF"
        [ -n "$ROOT" ] || chown root:root "$ROOT$KEEPALIVED_CONF"
        changed=1
        echo "keepalived: wrote $KEEPALIVED_CONF"
    fi

    write_abs "$ROOT$notify" "$(desired_vrrp_notify)"
    if [ "$CHANGED_THIS_WRITE" -eq 1 ]; then
        chmod 0755 "$ROOT$notify"
        [ -n "$ROOT" ] || chown root:root "$ROOT$notify"
        changed=1
        echo "keepalived: wrote $notify"
    fi

    if [ -z "$ROOT" ] && [ "$changed" -eq 1 ]; then
        systemctl daemon-reload
        systemctl enable --now keepalived
        systemctl restart keepalived
        echo "keepalived: enabled and restarted"
    fi
}

# The copy is what the cluster reads while this Pi is gone, and the timer is what
# keeps it a minute old. The group and the directory are made here rather than by
# the sync: the script's 0640 root:wg-sync has to point at something on its first
# run. Under PI_ROOT a fixture has no such group, so only the directory is made.
ensure_snapshot() {
    local changed=0

    if [ -z "$ROOT" ]; then
        if ! getent group wg-sync >/dev/null 2>&1; then
            groupadd --system wg-sync
            CHANGED=$((CHANGED + 1))
            echo "snapshot: created the wg-sync group"
        fi
        if [ ! -d "$ROOT$WG_SYNC_DIR" ]; then
            install -d -m 0750 -o root -g wg-sync "$ROOT$WG_SYNC_DIR"
            CHANGED=$((CHANGED + 1))
            echo "snapshot: created $WG_SYNC_DIR"
        fi
    elif [ ! -d "$ROOT$WG_SYNC_DIR" ]; then
        install -d -m 0750 "$ROOT$WG_SYNC_DIR"
        CHANGED=$((CHANGED + 1))
        echo "snapshot: created $WG_SYNC_DIR"
    fi

    write_abs "$ROOT$WG_SNAPSHOT_SCRIPT" "$(desired_snapshot)"
    if [ "$CHANGED_THIS_WRITE" -eq 1 ]; then
        chmod 0755 "$ROOT$WG_SNAPSHOT_SCRIPT"
        [ -n "$ROOT" ] || chown root:root "$ROOT$WG_SNAPSHOT_SCRIPT"
        changed=1
        echo "snapshot: wrote $WG_SNAPSHOT_SCRIPT"
    fi

    write_abs "$ROOT$WG_SNAPSHOT_SERVICE" "$(desired_snapshot_service)"
    if [ "$CHANGED_THIS_WRITE" -eq 1 ]; then
        chmod 0644 "$ROOT$WG_SNAPSHOT_SERVICE"
        [ -n "$ROOT" ] || chown root:root "$ROOT$WG_SNAPSHOT_SERVICE"
        changed=1
        echo "snapshot: wrote $WG_SNAPSHOT_SERVICE"
    fi

    write_abs "$ROOT$WG_SNAPSHOT_TIMER" "$(cat "$TEMPLATES/systemd/wg-vpn-snapshot.timer")"
    if [ "$CHANGED_THIS_WRITE" -eq 1 ]; then
        chmod 0644 "$ROOT$WG_SNAPSHOT_TIMER"
        [ -n "$ROOT" ] || chown root:root "$ROOT$WG_SNAPSHOT_TIMER"
        changed=1
        echo "snapshot: wrote $WG_SNAPSHOT_TIMER"
    fi

    if [ -z "$ROOT" ] && [ "$changed" -eq 1 ]; then
        systemctl daemon-reload
        systemctl enable --now wg-vpn-snapshot.timer
        echo "snapshot: enabled the timer"
    fi
}

# The account the pull runs as, and the four directories it needs. The account is
# --system with its own group, which is the group the snapshot's 0640 and its
# directory's 0750 already name: the sync reads the copy and reaches nothing else
# on this host. Under PI_ROOT there is no account to create, so the directories
# are made and the modes are what a fixture can prove.
#
# What is never written here: the key and the authorized_keys. A private key
# cannot be invented and the public half has to match it, so the operator copies
# both once. A file that exists is put back to its mode, because a key another
# local account can read is the same as no key at all.
ensure_sync_account() {
    local host="$1" entry path mode user group

    if [ -z "$ROOT" ] && ! getent passwd wg-sync >/dev/null 2>&1; then
        useradd --system --create-home --home-dir "$WG_SYNC_ACCOUNT_HOME" \
            --shell /bin/sh wg-sync
        CHANGED=$((CHANGED + 1))
        echo "sync: created the wg-sync account on $host"
    fi

    for entry in \
        "$WG_SYNC_ACCOUNT_HOME/.ssh:0700:wg-sync:wg-sync" \
        "/etc/wg-sync:0700:root:root" \
        "$WG_SYNC_DIR:0750:root:wg-sync" \
        "$TEXTFILE_DIR:0755:root:root"; do
        IFS=: read -r path mode user group <<<"$entry"
        if [ ! -d "$ROOT$path" ]; then
            install -d -m "$mode" "$ROOT$path"
            CHANGED=$((CHANGED + 1))
            echo "sync: created $path"
        fi
        if [ -z "$ROOT" ]; then
            chown "$user:$group" "$ROOT$path"
        fi
        chmod "$mode" "$ROOT$path"
    done

    for entry in "$WG_SYNC_KEY:0600:root:root" \
        "$WG_SYNC_AUTH_KEYS:0600:wg-sync:wg-sync"; do
        IFS=: read -r path mode user group <<<"$entry"
        if [ ! -f "$ROOT$path" ]; then
            continue
        fi
        chmod "$mode" "$ROOT$path"
        if [ -z "$ROOT" ]; then
            chown "$user:$group" "$ROOT$path"
        fi
    done
}

# The pull's script and the timer that runs it every minute, written only when
# their bytes differ so a run over a matching host restarts nothing. The timer is
# enabled rather than started: `enable --now` on a timer arms it, and the first
# tick is a minute away, which is the schedule the unit declares.
#
# Only the standby pulls. The authority is the host every client is reading, so a
# pull there would stop its own Panel for nothing; and the account it would dial
# is itself, where the key it needs does not exist, so each minute would publish
# `ok 0` and raise the staleness alert against the host that is working. A host
# that is not the standby therefore gets no unit, and one a previous run left
# behind is switched off.
ensure_sync() {
    local host="$1" changed=0

    if [ "$(host_address "$host")" != "$WG_SYNC_STANDBY" ]; then
        if [ -z "$ROOT" ] && [ -f "$ROOT$WG_SYNC_TIMER" ]; then
            systemctl disable --now wg-vpn-sync.timer
            CHANGED=$((CHANGED + 1))
        fi
        # Removing the three files is what lets the host converge: `sync_drift`
        # fails on their existence, so a disabled timer left in place would be
        # drift only a hand could clear.
        for path in "$WG_SYNC_SCRIPT" "$WG_SYNC_SERVICE" "$WG_SYNC_TIMER"; do
            if [ -e "$ROOT$path" ]; then
                rm -f "$ROOT$path"
                CHANGED=$((CHANGED + 1))
                echo "sync: removed $path from the authority"
            fi
        done
        if [ -z "$ROOT" ]; then
            systemctl daemon-reload
        fi
        echo "sync: $host is the authority, no sync timer here"
        return 0
    fi

    write_abs "$ROOT$WG_SYNC_SCRIPT" "$(desired_sync)"
    if [ "$CHANGED_THIS_WRITE" -eq 1 ]; then
        chmod 0755 "$ROOT$WG_SYNC_SCRIPT"
        [ -n "$ROOT" ] || chown root:root "$ROOT$WG_SYNC_SCRIPT"
        changed=1
        echo "sync: wrote $WG_SYNC_SCRIPT"
    fi

    write_abs "$ROOT$WG_SYNC_SERVICE" "$(desired_sync_service)"
    if [ "$CHANGED_THIS_WRITE" -eq 1 ]; then
        chmod 0644 "$ROOT$WG_SYNC_SERVICE"
        [ -n "$ROOT" ] || chown root:root "$ROOT$WG_SYNC_SERVICE"
        changed=1
        echo "sync: wrote $WG_SYNC_SERVICE"
    fi

    write_abs "$ROOT$WG_SYNC_TIMER" "$(cat "$TEMPLATES/systemd/wg-vpn-sync.timer")"
    if [ "$CHANGED_THIS_WRITE" -eq 1 ]; then
        chmod 0644 "$ROOT$WG_SYNC_TIMER"
        [ -n "$ROOT" ] || chown root:root "$ROOT$WG_SYNC_TIMER"
        changed=1
        echo "sync: wrote $WG_SYNC_TIMER"
    fi

    if [ -z "$ROOT" ] && [ "$changed" -eq 1 ]; then
        systemctl daemon-reload
        systemctl enable --now wg-vpn-sync.timer
        echo "sync: enabled the timer"
    fi
}

# What `verify` reads: both files byte for byte, and on a real host the unit that
# runs them. A unit that is not enabled is the failure a file comparison cannot
# see: the instance is correct and nothing starts it after a reboot.
keepalived_drift() {
    local host="$1" notify="$WG_LIB/wg-vrrp-notify.sh" problems=0

    if ! check_text "$ROOT$KEEPALIVED_CONF" "$(desired_keepalived "$host")"; then
        problems=1
    fi
    if ! check_text "$ROOT$notify" "$(desired_vrrp_notify)"; then
        problems=1
    fi
    if [ -z "$ROOT" ] && ! systemctl is-enabled --quiet keepalived; then
        echo "DIFFERS the keepalived unit is not enabled"
        problems=1
    fi
    return "$problems"
}

# What `verify` reads: the three files byte for byte, and on a real host the
# timer. A timer that is not enabled is the failure a file comparison cannot see:
# the files are right and nothing refreshes the copy after a reboot.
snapshot_drift() {
    local problems=0

    if ! check_text "$ROOT$WG_SNAPSHOT_SCRIPT" "$(desired_snapshot)"; then
        problems=1
    fi
    if ! check_text "$ROOT$WG_SNAPSHOT_SERVICE" "$(desired_snapshot_service)"; then
        problems=1
    fi
    if ! check_text "$ROOT$WG_SNAPSHOT_TIMER" \
        "$(cat "$TEMPLATES/systemd/wg-vpn-snapshot.timer")"; then
        problems=1
    fi
    if [ -z "$ROOT" ] && ! systemctl is-enabled --quiet wg-vpn-snapshot.timer; then
        echo "DIFFERS the snapshot timer is not enabled"
        problems=1
    fi
    return "$problems"
}

# What `verify` reads for the pull: the three files byte for byte, and on a real
# host the timer. The timer is the half a file comparison cannot see — the files
# are right and nothing starts the pull after a reboot. On the authority the
# check is inverted: it should carry none of them, and a unit left there by an
# older run is the drift, because it pulls from the host it is on.
sync_drift() {
    local host="$1" problems=0

    if [ "$(host_address "$host")" != "$WG_SYNC_STANDBY" ]; then
        if [ -e "$ROOT$WG_SYNC_SCRIPT" ] || [ -e "$ROOT$WG_SYNC_SERVICE" ] ||
            [ -e "$ROOT$WG_SYNC_TIMER" ]; then
            echo "DIFFERS $host is the authority and carries a sync unit"
            problems=1
        fi
        return "$problems"
    fi

    if ! check_text "$ROOT$WG_SYNC_SCRIPT" "$(desired_sync)"; then
        problems=1
    fi
    if ! check_text "$ROOT$WG_SYNC_SERVICE" "$(desired_sync_service)"; then
        problems=1
    fi
    if ! check_text "$ROOT$WG_SYNC_TIMER" \
        "$(cat "$TEMPLATES/systemd/wg-vpn-sync.timer")"; then
        problems=1
    fi
    if [ -z "$ROOT" ] && ! systemctl is-enabled --quiet wg-vpn-sync.timer; then
        echo "DIFFERS the sync timer is not enabled"
        problems=1
    fi
    return "$problems"
}

# One key pair, two halves, and each host needs one of them: only the standby
# dials the authority, so only it holds the private key; only the authority
# accepts that connection, so only it holds the authorized_keys line. Asking a
# host about the half it does not need would send the operator to the wrong
# machine, so each is asked about its own.
sync_key_drift() {
    local host="$1" address file what problems=0

    address="$(host_address "$host")"
    if [ "$address" = "$WG_SYNC_STANDBY" ]; then
        file="$WG_SYNC_KEY"
        what="the private key the standby at $WG_SYNC_STANDBY dials the authority with"
    elif [ "$address" = "$WG_SYNC_AUTHORITY" ]; then
        file="$WG_SYNC_AUTH_KEYS"
        what="the authorized_keys line the authority at $WG_SYNC_AUTHORITY accepts"
    else
        return 0
    fi
    if [ ! -s "$ROOT$file" ]; then
        echo "MISSING $ROOT$file: $what"
        problems=1
    fi
    return "$problems"
}

# The database is the container's own file, so this never writes it: it narrows
# the mode of the copy the panel made and nothing else. Root owns it on a real
# host because the container runs as root, so 0600 keeps every local account out
# and the panel itself in.
ensure_wg_db_mode() {
    local file="$ROOT$WG_DB" mode waited=0

    if [ -d "$file" ]; then
        refuse_directory "$file"
    fi
    if [ ! -e "$file" ] && [ -n "$ROOT" ]; then
        # A fixture holds what it was given. Inventing a database here would let
        # the mode check pass on a file no container ever wrote.
        return 0
    fi
    while [ ! -e "$file" ] && [ "$waited" -lt "$WG_DB_WAIT" ]; do
        sleep 1
        waited=$((waited + 1))
    done
    if [ ! -e "$file" ]; then
        echo "wg-easy: no $file after ${WG_DB_WAIT}s; the panel has not created its database" >&2
        echo "the VPN has no peer set until it does: read 'docker compose logs wg-easy'" >&2
        exit 1
    fi

    mode="$(stat -c %a "$file")"
    if [ "$mode" = "$WG_DB_MODE" ]; then
        return 0
    fi
    chmod "$WG_DB_MODE" "$file"
    CHANGED=$((CHANGED + 1))
    echo "wg-easy: $file mode $mode -> $WG_DB_MODE"
}

# What `verify` reads: the mode the file carries. A world-readable copy is every
# peer's private key and the panel's password hash in a single read.
wg_db_drift() {
    local file="$ROOT$WG_DB" mode problems=0

    if [ -d "$file" ]; then
        echo "DIFFERS $file is a directory; the panel cannot use it"
        return 1
    fi
    if [ ! -e "$file" ]; then
        # A fixture may hold no stack at all. On a real host the panel creates
        # the database on its first start, and `setup` insists it appeared.
        if [ -z "$ROOT" ]; then
            echo "MISSING $file"
            problems=1
        fi
        return "$problems"
    fi
    mode="$(stat -c %a "$file")"
    if [ "$mode" != "$WG_DB_MODE" ]; then
        echo "DIFFERS $file mode $mode, expected $WG_DB_MODE"
        problems=1
    fi
    return "$problems"
}

# The account the leaf must belong to: the human account that owns the stack
# directory, read from its home rather than from `id`, so the same answer comes
# out of a fixture tree as out of a Pi. Pi-hole's web server runs as the image's
# `pihole` user, uid 1000 by default — the same number as this account on these
# hosts — and it reads the certificate as that account. A leaf installed
# 0600 root:root in a 0700 directory is unreadable to it, and FTL does not fall
# back: it fails to load the certificate, refuses to listen on 80 and 443, and
# the panel goes dark. If PIHOLE_UID ever moves off 1000, this ownership has to
# move with it.
stack_account_ids() {
    stat -c '%u %g' "$ROOT/home/$HUMAN_USER"
}

# Hands the panel's leaf to the account the panel runs as, and nothing else. The
# directory becomes the stack account's so Pi-hole can traverse it; the leaf
# becomes readable to it; the Caddy key beside them stays root-only, because
# nothing but root has business holding the VPN panel's private key.
ensure_cert_modes() {
    local dir="$ROOT$CERT_DIR" pem="$ROOT$PIHOLE_PEM" ids uid gid before

    if [ ! -d "$dir" ]; then
        return 0
    fi
    ids="$(stack_account_ids)"
    uid="${ids% *}"
    gid="${ids#* }"

    before="$(stat -c '%u:%g %a' "$dir")"
    if [ "$before" != "$uid:$gid $CERT_DIR_MODE" ]; then
        chown "$uid:$gid" "$dir"
        chmod "0$CERT_DIR_MODE" "$dir"
        CHANGED=$((CHANGED + 1))
        echo "certs: $dir $before -> $uid:$gid $CERT_DIR_MODE"
    fi
    if [ -f "$pem" ]; then
        before="$(stat -c '%u:%g %a' "$pem")"
        if [ "$before" != "$uid:$gid $PIHOLE_PEM_MODE" ]; then
            chown "$uid:$gid" "$pem"
            chmod "0$PIHOLE_PEM_MODE" "$pem"
            CHANGED=$((CHANGED + 1))
            echo "certs: $pem $before -> $uid:$gid $PIHOLE_PEM_MODE"
        fi
    fi
    # Caddy runs as root, so its pair needs no owner set here; it only ever needs
    # to stay unreadable to every other account, and a copy `restore` brought
    # back from a USB disk may not be.
    for file in "$ROOT$CADDY_KEY" "$ROOT$CERT_DIR/tls.pem"; do
        [ -f "$file" ] || continue
        before="$(stat -c %a "$file")"
        if [ "$before" != "$CADDY_PEM_MODE" ]; then
            chmod "0$CADDY_PEM_MODE" "$file"
            CHANGED=$((CHANGED + 1))
            echo "certs: $file mode $before -> $CADDY_PEM_MODE"
        fi
    done
}

# What `verify` reads: an unreadable leaf is a panel that will not start, and a
# readable VPN key is the tunnels. Both are named, because neither shows up as
# a difference in a file this script renders.
cert_mode_drift() {
    local dir="$ROOT$CERT_DIR" pem="$ROOT$PIHOLE_PEM" key="$ROOT$CADDY_KEY" ids uid gid problems=0 mode bits

    if [ ! -d "$dir" ]; then
        return 0
    fi
    ids="$(stack_account_ids)"
    uid="${ids% *}"
    gid="${ids#* }"

    mode="$(stat -c '%u:%g' "$dir")"
    if [ "$mode" != "$uid:$gid" ]; then
        echo "DIFFERS $dir is owned by $mode, expected $uid:$gid ($HUMAN_USER)"
        problems=1
    fi
    mode="$(stat -c %a "$dir")"
    if [ "$mode" != "$CERT_DIR_MODE" ]; then
        echo "DIFFERS $dir mode $mode, expected $CERT_DIR_MODE"
        problems=1
    fi
    if [ -f "$pem" ]; then
        mode="$(stat -c '%u:%g' "$pem")"
        if [ "$mode" != "$uid:$gid" ]; then
            echo "DIFFERS $pem is owned by $mode, so the panel cannot read it"
            problems=1
        fi
        mode="$(stat -c %a "$pem")"
        if [ "$mode" != "$PIHOLE_PEM_MODE" ]; then
            echo "DIFFERS $pem mode $mode, expected $PIHOLE_PEM_MODE"
            problems=1
        fi
    fi
    if [ -f "$key" ]; then
        bits="$(stat -c %a "$key")"
        if [ "$((0$bits & 077))" -ne 0 ]; then
            echo "DIFFERS $key mode $bits lets another account read the VPN key"
            problems=1
        fi
    fi
    return "$problems"
}

cmd_render() {
    local host="$1"

    require_templates
    echo "--- $STACK_DIR/docker-compose.yml"
    desired_compose
    echo "--- $STACK_DIR/Caddyfile"
    desired_caddyfile "$host"
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
    echo "--- $STACK_DIR/pihole.settings"
    desired_pihole_settings
    echo "--- /etc/netplan/50-cloud-init.yaml"
    desired_netplan
    echo "--- /etc/systemd/resolved.conf.d/90-pihole-resolvers.conf"
    desired_resolved
    echo "--- $SSHD_DROPIN"
    desired_sshd_dropin
    echo "--- $SSHD_CONFIG"
    echo "PasswordAuthentication no, X11Forwarding no: the active directives only,"
    echo "the commented examples left as they are. cloud-init's"
    echo "50-cloud-init.conf said yes and sorts first, which is why the drop-in"
    echo "above is numbered 00."
    echo "--- $SUDOERS"
    echo "removed: $ADMINS_GRANT"
    echo "then the $ADMINS_GROUP group itself; %sudo stays as Ubuntu ships it"
    echo "--- $CLOUD_INIT_SUDOERS"
    echo "removed: cloud-init's grant for the retired ubuntu account"
    echo "--- ufw (the host firewall, enabled by setup on a real host)"
    echo "default: deny incoming, allow outgoing, allow routed (the containers keep egress)"
    desired_firewall_commands
    echo "never reaches ufw: $WG_UDP_PORT/udp (wg-easy), 8443/tcp (caddy) and"
    echo "9902/tcp (smartctl-exporter) are published by Docker, which DNATs in NAT"
    echo "and FORWARD before INPUT; a ufw rule for them would filter nothing"
    echo "--- $KEEPALIVED_CONF"
    desired_keepalived "$host"
    echo "--- $WG_LIB/wg-vrrp-notify.sh"
    desired_vrrp_notify
    echo "--- $WG_SNAPSHOT_SCRIPT"
    desired_snapshot
    echo "--- $WG_SNAPSHOT_SERVICE"
    desired_snapshot_service
    echo "--- $WG_SNAPSHOT_TIMER"
    cat "$TEMPLATES/systemd/wg-vpn-snapshot.timer"
    if [ "$(host_address "$host")" = "$WG_SYNC_STANDBY" ]; then
        echo "--- $WG_SYNC_SCRIPT"
        desired_sync
        echo "--- $WG_SYNC_SERVICE"
        desired_sync_service
        echo "--- $WG_SYNC_TIMER"
        cat "$TEMPLATES/systemd/wg-vpn-sync.timer"
        echo "--- $WG_SYNC_STAMP"
        echo "<table> <sha256> per line: the five tables as they stood after the last"
        echo "install this host made. 'vpn-sync' writes it, and a line that moved on"
        echo "this host is a local edit it refuses to overwrite without --force."
    else
        echo "--- $WG_SYNC_SCRIPT, $WG_SYNC_SERVICE and $WG_SYNC_TIMER"
        echo "none: $host is the authority, and only the standby pulls. A pull here"
        echo "would stop the Panel clients are using and dial the host it is on."
    fi
    echo "--- $WG_SYNC_KEY ($WG_SYNC_STANDBY)"
    echo "required, never written here: the private half the standby dials the"
    echo "authority with. 0600 root:root; a fixture cannot prove the ownership."
    echo "--- $WG_SYNC_AUTH_KEYS ($WG_SYNC_AUTHORITY)"
    echo "required, never written here: one line, restricting that key to reading"
    echo "the snapshot from $WG_SYNC_STANDBY and nothing else:"
    echo "restrict,command=\"/bin/cat $WG_SYNC_DIR/wg-easy.db\",from=\"$WG_SYNC_STANDBY\" ssh-ed25519 ... wg-sync@rbp1"
    echo "--- the shared address"
    echo "$VIP_ADDRESS/24: held by rbp0, taken by rbp1 when rbp0 stops answering."
    echo "--- /home/$HUMAN_USER/.ssh/authorized_keys"
    echo "the operator's, not written here: setup refuses to close password logins"
    echo "while no key is in that file"
    echo "--- $STACK_DIR/config/wg-easy.db"
    echo "wg-easy 15's own database: panel account, host, port, DNS, peers. Not"
    echo "written here, and copied by 'backup' with the rest of the stack."
    echo "setup narrows it to $WG_DB_MODE once the panel has created it, and"
    echo "'verify' reads the mode back: it holds every peer's private key"
    echo "the panel signs in with its own account: no OAuth provider, no secret file"
    echo "--- $STACK_DIR/pihole.env"
    echo "required, never printed and never written: the panel password is the"
    echo "only key it holds, and it is gitignored by the *.env rule"
    echo "--- $STACK_DIR/certs/tls.pem and tls-key.pem"
    echo "required, never printed and never written: this host's own leaf from"
    echo "the internal CA, 'hack/internal-cert.sh --host $(panel_host "$host")'."
    echo "caddy serves it on 8443 and wg-easy's 5000 stays on the compose network"
    echo "--- $STACK_DIR/certs/pihole.pem"
    echo "required, never printed and never written: one combined PEM (certificate"
    echo "then key) for $(pihole_host "$host"), from the same CA, minted with"
    echo "'hack/internal-cert.sh --host $(pihole_host "$host")'. Pi-hole reads it"
    echo "through FTLCONF_webserver_tls_cert; ./certs is mounted read-only into"
    echo "the pihole container and the cluster's exporter verifies it on 443"
    echo "--- /usr/local/sbin/hosts-pi.sh"
    echo "this script, installed at mode 0555: systemd starts it from /, not from a checkout"
    echo "--- restic $RESTIC_VERSION"
    echo "static linux-arm64 release from github.com/restic/restic, sha256 $RESTIC_SHA256"
    echo "--- /etc/systemd/system/pi-backup.service"
    desired_backup_service "$host"
    echo "--- /etc/systemd/system/pi-backup.timer"
    desired_backup_timer
    echo "--- /etc/systemd/system/pi-backup-prune.service"
    desired_prune_service "$host"
    echo "--- /etc/systemd/system/pi-backup-prune.timer"
    desired_prune_timer
    echo "--- the panel"
    echo "https://$(panel_host "$host"):8443, the panel's own account: no provider,"
    echo "which is also why it answers when the cluster is down"
}

# The checksum is checked against the published release, so a truncated download
# fails before it can replace a working binary.
install_restic() {
    if [ -n "$ROOT" ]; then
        echo "restic: not installed under PI_ROOT"
        return 0
    fi
    if [ -x /usr/local/bin/restic ] &&
        /usr/local/bin/restic version | grep -q "restic $RESTIC_VERSION "; then
        echo "restic: already $RESTIC_VERSION"
        return 0
    fi

    local tmp
    tmp="$(mktemp -d)"
    # A RETURN trap would stay installed after this function and fire on every
    # later function return, so cleanup is pinned to the script's own exit.
    trap 'rm -rf "$tmp"' EXIT
    if ! curl -fsSL --max-time 300 "$RESTIC_URL" -o "$tmp/restic.bz2"; then
        echo "restic: download failed from $RESTIC_URL" >&2
        exit 1
    fi
    if ! printf '%s  %s\n' "$RESTIC_SHA256" "$tmp/restic.bz2" | sha256sum -c - >/dev/null; then
        echo "restic: checksum mismatch on the downloaded $RESTIC_VERSION release" >&2
        exit 1
    fi
    bunzip2 -c "$tmp/restic.bz2" > "$tmp/restic"
    chmod 0555 "$tmp/restic"
    mv "$tmp/restic" /usr/local/bin/restic.tmp
    mv /usr/local/bin/restic.tmp /usr/local/bin/restic
    rm -rf "$tmp"
    trap - EXIT
    echo "restic: installed $RESTIC_VERSION"
}

# The legacy job is retired, not replaced: its `docker system prune -af` goes with
# it (the nightly stream does not need the disk back), and its credential lines are
# revoked keys that would only confuse the next reader of /etc/environment.
retire_legacy_backup() {
    local cron="$ROOT/var/spool/cron/crontabs/root" envfile="$ROOT/etc/environment" tmp

    if [ -f "$cron" ] && grep -qF "$LEGACY_CRON_MATCH" "$cron"; then
        tmp="$cron.retired"
        ( umask 077; grep -vF "$LEGACY_CRON_MATCH" "$cron" > "$tmp" ) || true
        cat "$tmp" > "$cron"
        rm -f "$tmp"
        echo "retired: $LEGACY_CRON_MATCH from $cron"
        CHANGED=$((CHANGED + 1))
    fi
    if [ -f "$envfile" ] && grep -qE "$LEGACY_ENV_PATTERN" "$envfile"; then
        tmp="$envfile.retired"
        ( umask 077; grep -vE "$LEGACY_ENV_PATTERN" "$envfile" > "$tmp" ) || true
        cat "$tmp" > "$envfile"
        rm -f "$tmp"
        echo "retired: the dead RESTIC/AWS keys from $envfile"
        CHANGED=$((CHANGED + 1))
    fi
}

cmd_setup() {
    local host="$1" current_name pihole_changed=0 caddy_changed=0 netplan_changed=0 resolved_changed=0

    require_templates

    current_name=""
    if [ -f "$ROOT/etc/hostname" ]; then
        current_name="$(tr -d '[:space:]' < "$ROOT/etc/hostname")"
    fi
    if [ -n "$current_name" ] && [ "$current_name" != "$host" ]; then
        echo "this host calls itself '$current_name'; refusing to configure it as '$host'" >&2
        exit 1
    fi

    require_env

    if [ -z "$ROOT" ] && [ "$(id -u)" -ne 0 ]; then
        echo "setup writes $SSHD_DROPIN, $SUDOERS and the host firewall; run it with sudo" >&2
        exit 1
    fi
    require_login_key

    ensure_engine
    ensure_sudo_policy
    ensure_sshd_policy
    ensure_firewall
    ensure_sync_packages
    ensure_keepalived "$host"
    ensure_snapshot
    ensure_sync_account "$host"
    ensure_sync "$host"

    write_abs "$ROOT$STACK_DIR/docker-compose.yml" "$(desired_compose)"
    # Caddy reads this at start, and it is a bind mount: a file written under a
    # running caddy is invisible until the container restarts. Hence the flag and
    # the recreate at the bottom, the same rule pihole's files follow.
    write_abs "$ROOT$STACK_DIR/Caddyfile" "$(desired_caddyfile "$host")"
    caddy_changed="$CHANGED_THIS_WRITE"
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
    write_abs "$ROOT$STACK_DIR/pihole.settings" "$(desired_pihole_settings)"
    if [ "$CHANGED_THIS_WRITE" -eq 1 ]; then
        pihole_changed=1
    fi
    write_abs "$ROOT/etc/netplan/50-cloud-init.yaml" "$(desired_netplan)"
    netplan_changed="$CHANGED_THIS_WRITE"
    # netplan warns on every run that a world-readable file there is "too open",
    # and it is right: it describes the host's addresses.
    chmod 0600 "$ROOT/etc/netplan/50-cloud-init.yaml"
    write_abs "$ROOT/etc/systemd/resolved.conf.d/90-pihole-resolvers.conf" "$(desired_resolved)"
    resolved_changed="$CHANGED_THIS_WRITE"
    write_abs "$ROOT/etc/hostname" "$(desired_hostname "$host")"

    install_restic
    # The templates travel beside the script so `verify` works from
    # /usr/local/sbin, where a host that has no ansible tree runs it.
    install -d -m 0755 "$ROOT/usr/local/lib/hosts-pi/templates"
    # `runners/` carries the systemd units in a subdirectory of its own, so the
    # tree is copied rather than a flat glob that stops at the first directory.
    cp -a "$TEMPLATES/." "$ROOT/usr/local/lib/hosts-pi/templates/"
    find "$ROOT/usr/local/lib/hosts-pi/templates" -type f -exec chmod 0644 {} +
    install -D -m 0555 "$HERE/hosts-pi.sh" "$ROOT/usr/local/sbin/hosts-pi.sh"
    write_abs "$ROOT/etc/systemd/system/pi-backup.service" "$(desired_backup_service "$host")"
    write_abs "$ROOT/etc/systemd/system/pi-backup.timer" "$(desired_backup_timer)"
    write_abs "$ROOT/etc/systemd/system/pi-backup-prune.service" "$(desired_prune_service "$host")"
    write_abs "$ROOT/etc/systemd/system/pi-backup-prune.timer" "$(desired_prune_timer)"
    retire_legacy_backup
    if [ -z "$ROOT" ]; then
        systemctl daemon-reload
        systemctl enable --now pi-backup.timer pi-backup-prune.timer
        echo "systemd: pi-backup.timer and pi-backup-prune.timer enabled"
    else
        echo "systemd: not touched under PI_ROOT"
    fi

    # The resolvers are a live setting: writing the files changes nothing until
    # netplan and resolved read them. This runs before the stack is touched,
    # because a container's /etc/resolv.conf is a copy taken when it is created.
    if [ -z "$ROOT" ]; then
        if [ "$netplan_changed" -eq 1 ]; then
            netplan apply
            echo "netplan: applied"
        fi
        if [ "$resolved_changed" -eq 1 ]; then
            systemctl restart systemd-resolved
            echo "resolved: restarted"
        fi
    fi

    # Before the stack, because a bind mount is fixed when the container is
    # created: a leaf the panel cannot read has to be handed over first. It runs
    # under PI_ROOT too, so a fixture proves the ownership rather than skipping
    # the only step that makes it.
    ensure_cert_modes

    if [ -z "$ROOT" ]; then
        # --remove-orphans is what takes a service off a host when this template
        # stops declaring it: `up -d` starts what the file names and leaves
        # everything else running. Without the flag, the router this template no
        # longer declares would stay up on rbp0, dashboard on 8080 included.
        ( cd "$STACK_DIR" && docker compose up -d --remove-orphans >/dev/null )
        echo "stack: docker compose up -d --remove-orphans"
        # `up -d <service>` converges that service alone, so the run above is the
        # one that applies a definition or image change to the other four. This
        # one is here because a bind mount is resolved when the container starts:
        # a file written above is invisible to a pihole that kept running.
        if [ "$pihole_changed" -eq 1 ]; then
            ( cd "$STACK_DIR" && docker compose up -d --force-recreate pihole >/dev/null )
            echo "stack: docker compose up -d --force-recreate pihole"
        fi
        if [ "$caddy_changed" -eq 1 ]; then
            ( cd "$STACK_DIR" && docker compose up -d --force-recreate caddy >/dev/null )
            echo "stack: docker compose up -d --force-recreate caddy"
        fi
        # The three bridge services are the ones that resolve names through the
        # host's file rather than Pi-hole answering for them; pihole and
        # node_exporter share the host's namespace and need no recreate.
        if [ "$netplan_changed" -eq 1 ] || [ "$resolved_changed" -eq 1 ]; then
            ( cd "$STACK_DIR" && docker compose up -d --force-recreate wg-easy caddy smartctl-exporter >/dev/null )
            echo "stack: docker compose up -d --force-recreate wg-easy caddy smartctl-exporter"
        fi
    else
        echo "stack: not started (PI_ROOT is set)"
    fi

    # After the stack is up, because the panel writes the file on its own start.
    ensure_wg_db_mode

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
    local host="$1" problems=0 file content

    require_templates

    if [ -z "$ROOT" ] && [ "$(id -u)" -ne 0 ]; then
        echo "verify reads $SUDOERS, $SSHD_CONFIG, the firewall and the VPN database; run it with sudo" >&2
        exit 1
    fi

    for file in pihole.env certs/tls.pem certs/tls-key.pem certs/pihole.pem; do
        if [ ! -f "$ROOT$STACK_DIR/$file" ]; then
            echo "MISSING $ROOT$STACK_DIR/$file"
            problems=1
        fi
    done

    if [ ! -f "$ROOT$STACK_DIR/docker-compose.yml" ]; then
        echo "MISSING $ROOT$STACK_DIR/docker-compose.yml"
        problems=1
    elif ! cmp -s <(desired_compose) "$ROOT$STACK_DIR/docker-compose.yml"; then
        echo "DIFFERS $ROOT$STACK_DIR/docker-compose.yml"
        diff -u "$ROOT$STACK_DIR/docker-compose.yml" <(desired_compose) || true
        problems=1
    fi

    if ! check_text "$ROOT$STACK_DIR/Caddyfile" "$(desired_caddyfile "$host")"; then
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
    if ! check_text "$ROOT$STACK_DIR/pihole.settings" "$(desired_pihole_settings)"; then
        problems=1
    fi
    if ! check_text "$ROOT/etc/netplan/50-cloud-init.yaml" "$(desired_netplan)"; then
        problems=1
    fi
    if ! check_text "$ROOT/etc/systemd/resolved.conf.d/90-pihole-resolvers.conf" "$(desired_resolved)"; then
        problems=1
    fi

    if [ ! -f "$ROOT/etc/hostname" ]; then
        echo "MISSING $ROOT/etc/hostname"
        problems=1
    elif [ "$(tr -d '[:space:]' < "$ROOT/etc/hostname")" != "$host" ]; then
        echo "DIFFERS $ROOT/etc/hostname: $(cat $ROOT/etc/hostname)"
        problems=1
    fi

    # SSH and sudo: what the 2026-11-24 measurement found on both Pis, and what
    # setup converges them to. The winning value is the drop-in's; the main file
    # is checked as a second record, because a run that loses the drop-in would
    # otherwise reopen password logins with nothing to say so.
    if ! check_text "$ROOT$SSHD_DROPIN" "$(desired_sshd_dropin)"; then
        problems=1
    fi
    if [ ! -f "$ROOT$SSHD_CONFIG" ]; then
        echo "MISSING $ROOT$SSHD_CONFIG"
        problems=1
    else
        for directive in PasswordAuthentication X11Forwarding; do
            if ! grep -qx "$directive no" "$ROOT$SSHD_CONFIG"; then
                echo "DIFFERS $ROOT$SSHD_CONFIG: $directive is not 'no'"
                problems=1
            fi
        done
    fi
    if [ -f "$ROOT$SUDOERS" ] && grep -qxF "$ADMINS_GRANT" "$ROOT$SUDOERS"; then
        echo "DIFFERS $ROOT$SUDOERS: $ADMINS_GRANT is still there"
        problems=1
    fi
    if [ -e "$ROOT$CLOUD_INIT_SUDOERS" ]; then
        echo "DIFFERS $ROOT$CLOUD_INIT_SUDOERS is still there"
        problems=1
    fi
    if [ -z "$ROOT" ]; then
        if getent group "$ADMINS_GROUP" >/dev/null 2>&1; then
            echo "DIFFERS the $ADMINS_GROUP group still exists"
            problems=1
        fi
    else
        echo "sudo: the $ADMINS_GROUP group is not checked (PI_ROOT is set)"
    fi
    if [ ! -s "$ROOT/home/$HUMAN_USER/.ssh/authorized_keys" ]; then
        echo "MISSING a public key for $HUMAN_USER in /home/$HUMAN_USER/.ssh/authorized_keys"
        problems=1
    fi

    # The backup stream: the four units, the installed copy systemd starts, the
    # credential file, and the two legacy leftovers setup is supposed to clear.
    for unit in pi-backup.service pi-backup.timer pi-backup-prune.service pi-backup-prune.timer; do
        case "$unit" in
            pi-backup.service) content="$(desired_backup_service "$host")" ;;
            pi-backup.timer) content="$(desired_backup_timer)" ;;
            pi-backup-prune.service) content="$(desired_prune_service "$host")" ;;
            *) content="$(desired_prune_timer)" ;;
        esac
        if ! check_text "$ROOT/etc/systemd/system/$unit" "$content"; then
            problems=1
        fi
    done
    if [ ! -f "$ROOT/usr/local/sbin/hosts-pi.sh" ]; then
        echo "MISSING $ROOT/usr/local/sbin/hosts-pi.sh"
        problems=1
    elif ! cmp -s "$HERE/hosts-pi.sh" "$ROOT/usr/local/sbin/hosts-pi.sh"; then
        echo "DIFFERS $ROOT/usr/local/sbin/hosts-pi.sh"
        problems=1
    fi
    if [ ! -f "$ROOT$BACKUP_ENV" ]; then
        echo "MISSING $ROOT$BACKUP_ENV"
        problems=1
    elif [ "$(stat -c %a "$ROOT$BACKUP_ENV")" != "600" ]; then
        echo "DIFFERS $ROOT$BACKUP_ENV: mode $(stat -c %a "$ROOT$BACKUP_ENV"), not 600"
        problems=1
    elif ! backup_env_problem "$ROOT$BACKUP_ENV"; then
        problems=1
    fi
    if ! command -v python3 >/dev/null 2>&1; then
        echo "MISSING python3: stage_sqlite and prune need it"
        problems=1
    fi
    if [ -f "$ROOT/var/spool/cron/crontabs/root" ] &&
        grep -qF "$LEGACY_CRON_MATCH" "$ROOT/var/spool/cron/crontabs/root"; then
        echo "DIFFERS $ROOT/var/spool/cron/crontabs/root still runs $LEGACY_CRON_MATCH"
        problems=1
    fi
    if [ -f "$ROOT/etc/environment" ] && grep -qE "$LEGACY_ENV_PATTERN" "$ROOT/etc/environment"; then
        echo "DIFFERS $ROOT/etc/environment still carries the dead RESTIC/AWS keys"
        problems=1
    fi
    if [ -z "$ROOT" ]; then
        for timer in pi-backup.timer pi-backup-prune.timer; do
            if ! systemctl is-enabled --quiet "$timer"; then
                echo "DIFFERS $timer is not enabled"
                problems=1
            fi
        done
    else
        echo "systemd: the timers are not checked under PI_ROOT"
    fi

    echo "root device: $(root_device)"

    engine_drift || problems=1

    firewall_drift || problems=1

    keepalived_drift "$host" || problems=1

    snapshot_drift || problems=1

    sync_drift "$host" || problems=1

    sync_key_drift "$host" || problems=1

    wg_db_drift || problems=1

    cert_mode_drift || problems=1

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

# What a backup holds, in one place: `backup` and `stream` differ in where the
# copy goes and which words they exclude, never in what state matters.
copy_state() {
    local dest="$1" host="$2"
    shift 2
    local status=0

    mkdir -p "$dest/dockers"
    if [ "$#" -eq 0 ]; then
        cp -a "$ROOT$STACK_DIR/." "$dest/dockers/"
    else
        local excludes="" arg
        for arg in "$@"; do
            excludes="$excludes --exclude=$arg"
        done
        # tar returns 1 when a file changed while it was read, which on a live
        # stack is normal and not a reason to lose the night. Anything above 1
        # is a real failure. The exclusions are patterns for tar, not the shell.
        set +e
        # shellcheck disable=SC2086
        tar -C "$ROOT$STACK_DIR" -cf - --warning=no-file-changed $excludes . |
            tar -C "$dest/dockers" -xf -
        status=$?
        set -e
        if [ "$status" -gt 1 ]; then
            echo "copy_state: tar failed ($status) reading $ROOT$STACK_DIR" >&2
            exit "$status"
        fi
    fi

    mkdir -p "$dest/etc"
    for file in hostname; do
        if [ -f "$ROOT/etc/$file" ]; then
            cp -a "$ROOT/etc/$file" "$dest/etc/$file"
        fi
    done

    mkdir -p "$dest/boot"
    for file in config.txt cmdline.txt; do
        if [ -f "$ROOT/boot/firmware/$file" ]; then
            cp -a "$ROOT/boot/firmware/$file" "$dest/boot/$file"
        fi
    done

    # The boot order and the EEPROM release live in the board, not on the disk,
    # but they are the two facts a rebuilt Pi is checked against.
    if [ -z "$ROOT" ] && command -v rpi-eeprom-config >/dev/null 2>&1; then
        rpi-eeprom-config > "$dest/bootloader.txt" || true
    fi

    printf 'host: %s\nroot device: %s\n' "$host" "$(root_device)" > "$dest/host.txt"
    if [ -z "$ROOT" ]; then
        ( cd "$STACK_DIR" && docker compose ps > "$dest/docker-ps.txt" 2>&1 ) || true
    fi
}

# The verb that runs the pull by hand, with the same script the timer runs. It is
# root's work — it stops the Panel, replaces the database and writes the stamp —
# so it is asked for an installed script rather than rendering one on the spot:
# what runs here is what the timer will run a minute later. --force is the
# operator accepting that a local edit on this host is lost.
cmd_vpn_sync() {
    local host="$1" force="${2:-0}"

    check_host "$host"
    # The verb runs on the standby. On the authority it would fetch from itself
    # through a key that host does not hold, and stopping its own panel is the
    # one thing this design never does while it carries the address.
    if [ "$(host_address "$host")" != "$WG_SYNC_STANDBY" ]; then
        echo "$host is the authority; the sync runs on the standby ($WG_SYNC_STANDBY)" >&2
        exit 1
    fi
    if [ ! -f "$ROOT$WG_SYNC_SCRIPT" ]; then
        echo "no $ROOT$WG_SYNC_SCRIPT; run 'hosts-pi.sh setup $host' first" >&2
        exit 1
    fi
    WG_ROOT="$ROOT" WG_SYNC_FORCE="$force" bash "$ROOT$WG_SYNC_SCRIPT"
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

    copy_state "$dir" "$host"

    # The two halves of the sync key pair, when this host holds them: neither is
    # regenerated by a setup, so a reflash that loses them leaves the standby
    # unable to pull until somebody exchanges a new pair by hand. They stay out of
    # `copy_state` because the nightly stream has its own account and its own
    # credentials; this is the copy the pre-reflash procedure takes by hand.
    mkdir -p "$dir/wg"
    if [ -d "$ROOT/etc/wg-sync" ]; then
        cp -a "$ROOT/etc/wg-sync" "$dir/wg/etc-wg-sync"
    fi
    if [ -d "$ROOT/var/lib/wg-sync/.ssh" ]; then
        mkdir -p "$dir/wg/wg-sync"
        cp -a "$ROOT/var/lib/wg-sync/.ssh" "$dir/wg/wg-sync/.ssh"
    fi

    bytes="$(du -sb "$dir" | cut -f1)"
    echo "backed up $host to $dir ($bytes bytes)"
    echo "check: the Pi-hole database alone is more than 100 MB"
}

# A live SQLite file copied byte for byte is not a backup: the committed rows can
# sit in the -wal beside it. So every database the copy brought over is replaced
# by one taken through SQLite's own backup API, and the -wal/-shm/-journal the
# copy carried are dropped with it. An excluded database never reaches this step:
# copy_state never wrote it.
stage_sqlite() {
    local src="$1" dst="$2"

    python3 - "$src" "$dst" <<'PY'
import os
import sqlite3
import sys

src, dst = sys.argv[1], sys.argv[2]
for root, _, names in os.walk(dst):
    for name in sorted(names):
        if not name.endswith((".db", ".sqlite", ".sqlite3")):
            continue
        target = os.path.join(root, name)
        rel = os.path.relpath(target, dst)
        source = os.path.join(src, rel)
        for suffix in ("", "-wal", "-shm", "-journal"):
            path = target + suffix
            if os.path.exists(path):
                os.unlink(path)
        try:
            live = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
            copy = sqlite3.connect(target)
            with copy:
                live.backup(copy)
            # The backup carries the source's journal mode in the header, so the
            # copy would come back as a WAL database with an empty -wal beside it.
            # Convert it once, here, so the snapshot holds one file per database.
            copy.execute("PRAGMA journal_mode=DELETE")
            live.close()
            copy.close()
        except Exception as exc:
            print(f"database {rel} cannot be staged: {exc}", file=sys.stderr)
            sys.exit(1)
PY
}

# The credential is the operator's file: setup never writes it, and stream refuses
# rather than contacting the bucket with whatever the environment happens to hold.
backup_env_problem() {
    # Four lines, the four keys, nothing else. A paste that lost a line, or a
    # command whose error box landed in the file, used to be sourced line by
    # line: the shell printed `│: not found`, then restic failed on an invalid
    # Authorization header and the real cause was never named. rbp0 did exactly
    # that on 2026-11-26.
    local file="$1" lines keys
    lines="$(grep -c '' "$file" 2>/dev/null)"
    [ -n "$lines" ] || lines=0
    if [ "$lines" -ne 4 ]; then
        echo "$BACKUP_ENV holds $lines lines, not the four KEY=value lines it must hold" >&2
        return 1
    fi
    keys="$(sed -n 's/^\([A-Z_][A-Z0-9_]*\)=.*/\1/p' "$file" | LC_ALL=C sort | tr '\n' ' ')"
    if [ "$keys" != "$BACKUP_ENV_KEYS" ]; then
        echo "$BACKUP_ENV holds the keys [$keys], not [$BACKUP_ENV_KEYS]" >&2
        return 1
    fi
    return 0
}

require_backup_env() {
    local file="$ROOT$BACKUP_ENV"

    if [ ! -f "$file" ]; then
        echo "$BACKUP_ENV is missing; write it from the password manager (docs/secrets.md)" >&2
        exit 1
    fi
    if [ "$(stat -c %a "$file")" != "600" ]; then
        echo "$BACKUP_ENV must be mode 0600, not $(stat -c %a "$file")" >&2
        exit 1
    fi
    backup_env_problem "$file" || exit 1
    if [ -z "${RESTIC_REPOSITORY:-}" ]; then
        set -a
        # shellcheck source=/dev/null
        . "$file"
        set +a
    fi
    : "${RESTIC_REPOSITORY:?$BACKUP_ENV must set RESTIC_REPOSITORY}"
    : "${RESTIC_PASSWORD:?$BACKUP_ENV must set RESTIC_PASSWORD}"
    : "${AWS_ACCESS_KEY_ID:?$BACKUP_ENV must set AWS_ACCESS_KEY_ID}"
    : "${AWS_SECRET_ACCESS_KEY:?$BACKUP_ENV must set AWS_SECRET_ACCESS_KEY}"
}

# Each host prunes its own repository: the verifier reads the snapshots but never
# rewrites them, so this is the one place the edge retention is applied.
cmd_prune() {
    local host="$1"

    if [ -z "$ROOT" ] && [ "$(id -u)" -ne 0 ]; then
        echo "prune rewrites the repository; run it with sudo" >&2
        exit 1
    fi
    require_backup_env

    "$RESTIC_BIN" forget \
        --retry-lock 2h \
        --keep-daily "$RETENTION_DAILY" \
        --keep-weekly "$RETENTION_WEEKLY" \
        --keep-monthly "$RETENTION_MONTHLY" \
        --prune
    echo "pruned $host to $RETENTION_DAILY daily, $RETENTION_WEEKLY weekly, $RETENTION_MONTHLY monthly"
}

# The nightly run: the same tree, minus the three words in BACKUP_EXCLUDES, under
# a fixed path so the snapshot's file list is the same every night. Nothing is
# staged on the disk a reflash would destroy — the tree is transient and the
# repository is remote — so `backup`'s same-filesystem refusal does not apply.
cmd_stream() {
    local host="$1"

    if [ -z "$ROOT" ] && [ "$(id -u)" -ne 0 ]; then
        echo "stream reads files the containers own; run it with sudo" >&2
        exit 1
    fi

    require_backup_env

    rm -rf "$ROOT$STAGING_DIR/tree"
    mkdir -p "$ROOT$STAGING_DIR/tree"

    # shellcheck disable=SC2086
    copy_state "$ROOT$STAGING_DIR/tree" "$host" $BACKUP_EXCLUDES
    stage_sqlite "$ROOT$STACK_DIR" "$ROOT$STAGING_DIR/tree/dockers"

    if "$RESTIC_BIN" backup \
        --retry-lock 2h \
        --host "$host" \
        --tag "stream=edge-$host" \
        "$ROOT$STAGING_DIR/tree"; then
        # Only a finished run removes the tree: a failed one leaves it in place so
        # the next look can see exactly what restic was given.
        rm -rf "$ROOT$STAGING_DIR/tree"
    else
        echo "restic backup failed; leaving $STAGING_DIR/tree for inspection" >&2
        exit 1
    fi
    echo "streamed $host to $RESTIC_REPOSITORY"
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

    # The sync key pair, back where it belongs: the private half is the standby's
    # and 0600, the authorized_keys line is the authority's. A restore that put
    # them back world-readable would leave every peer's key one `cat` away.
    if [ -d "$dir/wg/etc-wg-sync" ]; then
        mkdir -p "$ROOT/etc/wg-sync"
        cp -a "$dir/wg/etc-wg-sync/." "$ROOT/etc/wg-sync/"
        if [ -f "$ROOT/etc/wg-sync/id_ed25519" ]; then
            chmod 0600 "$ROOT/etc/wg-sync/id_ed25519"
        fi
    fi
    if [ -d "$dir/wg/wg-sync/.ssh" ]; then
        mkdir -p "$ROOT/var/lib/wg-sync/.ssh"
        cp -a "$dir/wg/wg-sync/.ssh/." "$ROOT/var/lib/wg-sync/.ssh/"
        chmod 0700 "$ROOT/var/lib/wg-sync/.ssh"
    fi
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
    local verb="${1:-}" force=0
    [ -n "$verb" ] || usage
    shift

    case "$verb" in
        render|setup|verify)
            [ "$#" -eq 1 ] || usage
            check_host "$1"
            FIREWALL_HOST="$1"
            "cmd_$verb" "$1"
            ;;
        stream|prune)
            [ "$#" -eq 1 ] || usage
            check_host "$1"
            "cmd_$verb" "$1"
            ;;
        vpn-sync)
            case "$#" in
                1) force=0 ;;
                2)
                    [ "$2" = "--force" ] || usage
                    force=1
                    ;;
                *) usage ;;
            esac
            check_host "$1"
            FIREWALL_HOST="$1"
            cmd_vpn_sync "$1" "$force"
            ;;
        backup|restore)
            [ "$#" -eq 2 ] || usage
            check_host "$2"
            FIREWALL_HOST="$2"
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
