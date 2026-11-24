#!/usr/bin/env bash
# host-audit.sh — read-only inventory of one host, printed as Markdown.
#
#   sudo ./host-audit.sh > /tmp/opencode/host-audit/$(hostname).md
#   sudo AUDIT_NETWORK=1 ./host-audit.sh     # also asks apt and do-release-upgrade (network, slow)
#   ./host-audit.sh --redact < some-output   # filter stdin through the redactor, nothing else
#   ./host-audit.sh --help
#
# Reads only. No file is written, no unit is started, no setting is changed, and no package list is
# refreshed: `apt-get -s` is a simulation and `do-release-upgrade -c` only checks.
#
# Every value a command prints passes through redact() first. That function removes secret *values*
# and keeps the key, so the report says `token: <redacted>` instead of `token: …`. It is the only
# thing standing between a host's credentials and this file; hack/tests/test_host_audit.py proves it
# catches the shapes this homelab actually stores (K3s token, WireGuard keys, env files, URL
# credentials, JWTs, PEM blocks) and leaves `PasswordAuthentication no` alone.
#
# Exit: 0 printed (root or not — a non-root run marks the root-only sections), 2 usage error.
set -uo pipefail

SCRIPT_VERSION="2026-11-24.1"
AUDIT_MAX_LINES="${AUDIT_MAX_LINES:-200}"
AUDIT_NETWORK="${AUDIT_NETWORK:-0}"
AUDIT_CMD_TIMEOUT="${AUDIT_CMD_TIMEOUT:-25}"

# --------------------------------------------------------------------------------------------------
# Redaction
# --------------------------------------------------------------------------------------------------
# A key is secret when it is one of the bare names, or ends in _token/_secret/_password/… — never
# when a secret word only appears at the *start* of a longer word, which is what keeps
# `passwordauthentication no` from sshd -T readable in the report.
redact() {
  awk '
    function issecret(k,   t) {
      t = tolower(k)
      gsub(/["\047]/, "", t)
      gsub(/[-.]/, "_", t)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", t)
      sub(/^(export|set|declare[[:space:]]+-x|readonly)[[:space:]]+/, "", t)
      if (t ~ /^(token|secret|pass|password|passwd|api_key|apikey|private_key|privatekey|preshared_key|presharedkey|psk|pre_shared_key|authorization|auth_token|authtoken|auth|client_secret|clientsecret|secret_key|secretkey|access_key|accesskey|access_key_id|secret_access_key|passphrase|credential|credentials|key)$/) return 1
      # `key` as a suffix covers the shapes a prefix defeats: etcd-s3-secret-key,
      # aws_secret_access_key, ssh_key, registry_auth. Nothing in this homelab
      # uses *_key for a value that is safe to print.
      if (t ~ /_(token|secret|password|passwd|api_key|apikey|private_key|preshared_key|psk|passphrase|credential|credentials|key|auth)$/) return 1
      if (t ~ /(token|secret|password|passwd|privatekey|presharedkey|apikey)$/) return 1
      return 0
    }
    # A secret passed as a flag anywhere in a command line: `k3s server --token=abc`,
    # `app --api-key hunter2`. The flag name decides, and the value is swallowed.
    function redact_flags(s,   out, flag) {
      out = ""
      while (match(s, /--?[A-Za-z0-9][A-Za-z0-9_-]*[= ]/)) {
        flag = substr(s, RSTART, RLENGTH)
        out = out substr(s, 1, RSTART - 1) flag
        s = substr(s, RSTART + RLENGTH)
        if (issecret(substr(flag, 1, length(flag) - 1))) sub(/^[^[:space:]]+/, "<redacted>", s)
      }
      return out s
    }
    # ?token=… , &X-Amz-Signature=… : the parameter name decides, the value is swallowed.
    function redact_query(s,   out, param, name) {
      out = ""
      while (match(s, /[?&][A-Za-z0-9_][A-Za-z0-9_.-]*=/)) {
        param = substr(s, RSTART, RLENGTH)
        out = out substr(s, 1, RSTART - 1) param
        s = substr(s, RSTART + RLENGTH)
        name = substr(param, 2, length(param) - 2)
        if (issecret(name) || name ~ /^[Xx]-[Aa][Mm][Zz]-/) sub(/^[^&[:space:]]+/, "<redacted>", s)
      }
      return out s
    }
    /-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----/ { print "<redacted: private key block>"; ink = 1; next }
    ink {
      if ($0 ~ /-----END [A-Z0-9 ]*PRIVATE KEY-----/) ink = 0
      next
    }
    {
      line = $0

      # user:password inside a URL, including the scheme-less form
      gsub(/:\/\/[^\/[:space:]@]+:[^\/[:space:]@]+@/, "://<redacted>@", line)
      # a JWT: three base64url segments, the first being the {"alg":…} header
      gsub(/eyJ[A-Za-z0-9_-]+\.ey[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/, "<redacted: jwt>", line)
      # an AWS-style key id
      gsub(/AKIA[0-9A-Z][0-9A-Z][0-9A-Z][0-9A-Z][0-9A-Z][0-9A-Z][0-9A-Z][0-9A-Z][0-9A-Z][0-9A-Z][0-9A-Z][0-9A-Z][0-9A-Z][0-9A-Z][0-9A-Z][0-9A-Z]/, "<redacted: aws key id>", line)
      # a credential in a query string, presigned URLs included
      line = redact_query(line)
      # a secret passed as a flag
      line = redact_flags(line)

      ci = index(line, ":")
      ei = index(line, "=")
      sep = 0
      if (ci > 0 && ei > 0) sep = (ci < ei) ? ci : ei
      else if (ci > 0) sep = ci
      else if (ei > 0) sep = ei

      # "://" means the colon is a scheme, not a separator
      if (sep > 0 && substr(line, sep + 1, 2) != "//") {
        key = substr(line, 1, sep - 1)
        if (issecret(key)) {
          trailer = (substr(line, sep + 1, 1) == " ") ? " " : ""
          print substr(line, 1, sep - 1) substr(line, sep, 1) trailer "<redacted>"
          next
        }
      }
      print line
    }
  '
}

if [ "${1:-}" = "--redact" ]; then
  redact
  exit 0
fi

if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
  sed -n '2,17p' "${BASH_SOURCE[0]}"
  exit 0
fi

# --------------------------------------------------------------------------------------------------
# Print helpers
# --------------------------------------------------------------------------------------------------
have() { command -v "$1" >/dev/null 2>&1; }

run() {                                  # run <label> <command...>
  local label="$1" rc
  shift
  printf '#### %s\n\n```\n' "$label"
  if have "$1"; then
    if have timeout; then
      timeout "$AUDIT_CMD_TIMEOUT" "$@" 2>&1 | redact | head -n "$AUDIT_MAX_LINES"
    else
      "$@" 2>&1 | redact | head -n "$AUDIT_MAX_LINES"
    fi
    rc=${PIPESTATUS[0]}
    [ "$rc" = 124 ] && printf '(timed out after %ss)\n' "$AUDIT_CMD_TIMEOUT"
  else
    printf '(missing: %s)\n' "$1"
  fi
  printf '```\n\n'
}

run_sh() {                               # run_sh <label> <shell string>
  local rc
  printf '#### %s\n\n```\n' "$1"
  if have timeout; then
    timeout "$AUDIT_CMD_TIMEOUT" sh -c "$2" 2>&1 | redact | head -n "$AUDIT_MAX_LINES"
  else
    sh -c "$2" 2>&1 | redact | head -n "$AUDIT_MAX_LINES"
  fi
  rc=${PIPESTATUS[0]}
  [ "$rc" = 124 ] && printf '(timed out after %ss)\n' "$AUDIT_CMD_TIMEOUT"
  printf '```\n\n'
}

section() { printf '\n## %s\n\n' "$1"; }

kv() { printf '%s: %s\n' "$1" "${2:-unknown}"; }

# --------------------------------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------------------------------
HOST="$(hostname 2>/dev/null || echo unknown)"
printf '# Host audit — %s\n\n' "$HOST"
printf 'Collected %s by host-audit.sh %s, uid %s.\n\n' "$(date -Is 2>/dev/null)" "$SCRIPT_VERSION" "$EUID"
if [ "$EUID" -ne 0 ]; then
  printf '> **Not root.** Parts of §13 and §14 read root-only paths and will be missing or thin.\n'
  printf '> Re-run under `sudo` before trusting this report.\n\n'
fi

# --------------------------------------------------------------------------------------------------
section "1. Machine"
run "os-release" cat /etc/os-release
run "uname" uname -a
run "uptime" uptime
run_sh "hardware model" 'cat /proc/device-tree/model 2>/dev/null | tr -d "\0"; cat /sys/devices/virtual/dmi/id/product_name 2>/dev/null; cat /sys/devices/virtual/dmi/id/sys_vendor 2>/dev/null'
run "lsb_release" lsb_release -a
run "last boot" who -b
run_sh "kernel cmdline" 'cat /proc/cmdline'
run_sh "cloud-init / provisioning" 'ls -1 /etc/cloud 2>/dev/null; ls -1 /var/lib/cloud/instance 2>/dev/null | head -5'

# --------------------------------------------------------------------------------------------------
section "2. Hardware"
run "lscpu" lscpu
run "memory" free -h
run "block devices" lsblk -o NAME,SIZE,ROTA,FSTYPE,TYPE,MOUNTPOINT
run_sh "filesystems" 'df -hT -x tmpfs -x devtmpfs -x squashfs'
run_sh "disk models and health" 'for d in /sys/block/*/device/model; do printf "%s: %s\n" "$(echo "$d" | cut -d/ -f4)" "$(cat "$d")"; done 2>/dev/null'
run_sh "swap" 'swapon --show; grep -c . /proc/swaps'

# --------------------------------------------------------------------------------------------------
section "3. Packages"
run_sh "installed package count" 'dpkg-query -W -f="\${Package}\n" 2>/dev/null | wc -l'
AUDIT_MAX_LINES=8000 run "dpkg selections" dpkg-query -W -f='${Package}\t${Version}\t${Status}\n'
run "held packages" apt-mark showhold
run "snap packages" snap list
run_sh "apt sources" 'grep -rhE "^[[:space:]]*(deb|Types:|URIs:|Suites:|Components:)" /etc/apt/sources.list /etc/apt/sources.list.d/ 2>/dev/null | sort -u'
run_sh "apt source files" 'ls -1 /etc/apt/sources.list.d/ 2>/dev/null'
run_sh "apt preferences" 'cat /etc/apt/preferences.d/* 2>/dev/null'

# --------------------------------------------------------------------------------------------------
section "4. Kernel, modules, sysctl"
run_sh "installed kernels" 'dpkg-query -W -f="\${Package} \${Version}\n" "linux-image-*" 2>/dev/null; dpkg-query -W -f="\${Package} \${Version}\n" "linux-modules-extra-*" 2>/dev/null'
run "kernel modules loaded" lsmod
run_sh "/etc/modules-load.d" 'for f in /etc/modules-load.d/*.conf /etc/modules; do [ -f "$f" ] && { echo "--- $f"; grep -vE "^(#|$)" "$f"; }; done'
run_sh "/etc/modprobe.d" 'for f in /etc/modprobe.d/*.conf; do [ -f "$f" ] && { echo "--- $f"; grep -vE "^(#|$)" "$f"; }; done'
run_sh "sysctl, the ones that change behaviour" 'sysctl -a 2>/dev/null | grep -E "^(vm\.(swappiness|max_map_count|overcommit\w*|panic_on_oom))|^(net\.ipv4\.(ip_forward|ip_local_port_range|tcp_congestion_control\w*|conf\.(all|default)\.(rp_filter|forwarding|accept_source_route)))|^(net\.bridge\.bridge-nf)|^(net\.core\.(somaxconn|rmem_max|wmem_max))|^(fs\.(inotify\.\w+|file-max|nr_open))|^(kernel\.(pid_max|panic|sysrq|unprivileged_\w+))"'
run_sh "sysctl drop-ins" 'for f in /etc/sysctl.conf /etc/sysctl.d/*.conf; do [ -f "$f" ] && { echo "--- $f"; grep -vE "^(#|$)" "$f"; }; done'
run_sh "ulimits" 'for f in /etc/security/limits.conf /etc/security/limits.d/*.conf; do [ -f "$f" ] && { echo "--- $f"; grep -vE "^(#|$)" "$f"; }; done'
run_sh "cgroup version" 'grep " /sys/fs/cgroup " /proc/1/mountinfo 2>/dev/null | sed "s/.* - //" | cut -d" " -f1'

# --------------------------------------------------------------------------------------------------
section "5. Time"
run "timedatectl" timedatectl
run_sh "clock services" 'for s in systemd-timesyncd chrony ntp ntpsec; do printf "%s: %s\n" "$s" "$(systemctl is-active "$s" 2>/dev/null)"; done'
run_sh "/etc/timezone and localtime" 'cat /etc/timezone 2>/dev/null; readlink -f /etc/localtime 2>/dev/null'

# --------------------------------------------------------------------------------------------------
section "6. Services, timers, cron"
run "failed units" systemctl --failed --no-legend --no-pager
run "enabled units" systemctl list-unit-files --state=enabled --no-legend --no-pager
run "timers" systemctl list-timers --all --no-pager
run_sh "crontabs (per user)" 'for u in root $(cut -d: -f1 /etc/passwd | grep -vE "^(root|nobody|.*-)" | head -20); do c=$(crontab -l -u "$u" 2>/dev/null | grep -vE "^(#|$)"); [ -n "$c" ] && { echo "--- $u"; echo "$c"; }; done'
run_sh "/etc/cron.d and cron.{daily,weekly,monthly}" 'ls -1 /etc/cron.d /etc/cron.daily /etc/cron.weekly /etc/cron.monthly 2>/dev/null'
run_sh "/etc/cron.d contents" 'for f in /etc/cron.d/*; do [ -f "$f" ] && { echo "--- $f"; grep -vE "^(#|$)" "$f"; }; done'

# --------------------------------------------------------------------------------------------------
section "7. Network"
run "addresses" ip -br addr
run "routes" ip route
run "ipv6 routes" ip -6 route
run_sh "listening sockets" 'ss -Htulpn 2>/dev/null'
run_sh "/etc/netplan" 'for f in /etc/netplan/*; do [ -f "$f" ] && { echo "--- $f"; grep -vE "^[[:space:]]*(#|$)" "$f"; }; done'
run_sh "/etc/resolv.conf" 'ls -l /etc/resolv.conf; grep -vE "^(#|$)" /etc/resolv.conf 2>/dev/null'
run_sh "resolved stub listener" 'grep -E "^[[:space:]]*#?DNSStubListener" /etc/systemd/resolved.conf 2>/dev/null'
run_sh "/etc/hosts" 'grep -vE "^(#|$)" /etc/hosts'
run_sh "firewall: ufw" 'ufw status verbose 2>/dev/null'
run_sh "firewall: nftables" 'nft list ruleset 2>/dev/null'
run_sh "firewall: iptables legacy rules present" 'iptables-save 2>/dev/null | grep -cE "^-A"'
run "docker daemon.json" cat /etc/docker/daemon.json

# --------------------------------------------------------------------------------------------------
section "8. Storage and exports"
run_sh "/etc/fstab" 'grep -vE "^(#|$)" /etc/fstab'
run_sh "nfs server" 'systemctl is-active nfs-server 2>/dev/null; cat /etc/exports 2>/dev/null | grep -vE "^(#|$)"; exportfs -v 2>/dev/null'
run_sh "mounts of interest" 'mount | grep -E " (nfs|nfs4|cifs|/mnt|/media) " 2>/dev/null'
run_sh "local-path and media trees" 'ls -1d /mnt/* /srv/* /opt/* 2>/dev/null'
run_sh "root disk and transport" 'findmnt -no SOURCE,FSTYPE,SIZE /; lsblk -o NAME,TRAN,SIZE,MODEL,MOUNTPOINT'
run_sh "snapraid" 'ls -l /etc/snapraid.conf 2>/dev/null; snapraid status 2>/dev/null | head -15'
run_sh "/opt and operator scripts" 'ls -1 /opt 2>/dev/null; ls -l /home/*/scripts 2>/dev/null | head -20'
run_sh "size of the trees that matter, same filesystem only" 'du -shx /var/lib/rancher /var/lib/docker /var/lib/kubelet 2>/dev/null; df -h /mnt/* 2>/dev/null'

# --------------------------------------------------------------------------------------------------
section "9. Containers"
run "docker version" docker version --format '{{.Server.Version}}'
run "docker compose projects" docker compose ls --all
run "docker containers" docker ps -a --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
run "docker images" docker image ls --format 'table {{.Repository}}\t{{.Tag}}\t{{.Size}}'
run "docker volumes" docker volume ls
run_sh "docker service units" 'systemctl list-unit-files "docker*" --no-legend --no-pager'
run_sh "compose file locations" 'find /home /opt /srv /root -maxdepth 4 -name "docker-compose.y*ml" -o -maxdepth 4 -name "compose.y*ml" 2>/dev/null | head -20'
run_sh "compose trees, what a reflash would have to keep" 'find /home/*/dockers -maxdepth 2 -printf "%y %s %p\n" 2>/dev/null | head -40'

# --------------------------------------------------------------------------------------------------
section "10. Kubernetes (k3s)"
run_sh "k3s version" 'k3s --version 2>/dev/null; ls -l /usr/local/bin/k3s 2>/dev/null'
run "k3s unit" systemctl cat k3s
run_sh "/etc/rancher/k3s/config.yaml" 'cat /etc/rancher/k3s/config.yaml 2>/dev/null'
run_sh "/etc/rancher/k3s directory" 'ls -la /etc/rancher/k3s/ 2>/dev/null'
run_sh "k3s role hints" 'ls -1d /var/lib/rancher/k3s/server /var/lib/rancher/k3s/agent 2>/dev/null; grep -hoE "(--server|--cluster-init|--disable[= ][a-z,]+|--node-label[= ][^ ]+)" /etc/systemd/system/k3s.service 2>/dev/null'
run_sh "containerd config drop-ins" 'ls -1 /var/lib/rancher/k3s/agent/etc/containerd/ 2>/dev/null; cat /var/lib/rancher/k3s/agent/etc/containerd/config.toml.tmpl 2>/dev/null'
run_sh "registries.yaml" 'cat /etc/rancher/k3s/registries.yaml 2>/dev/null'
run_sh "etcd snapshot config" 'grep -hE "etcd-(snapshot|s3)" /etc/rancher/k3s/config.yaml /etc/systemd/system/k3s.service 2>/dev/null'
run_sh "etcd snapshots on disk" 'ls -l /var/lib/rancher/k3s/server/db/snapshots 2>/dev/null | tail -5; ls -ld /var/lib/rancher/k3s/server/db/etcd 2>/dev/null'
run_sh "credential file modes" 'ls -l /etc/rancher/k3s/k3s.yaml /var/lib/rancher/k3s/server/token /home/*/.kube/config 2>/dev/null'

# --------------------------------------------------------------------------------------------------
section "11. GPU"
run_sh "nvidia driver" 'nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv 2>/dev/null; cat /proc/driver/nvidia/version 2>/dev/null'
run "dkms status" dkms status
run_sh "nvidia packages" 'dpkg-query -W -f="\${Package} \${Version}\n" "nvidia-*" "cuda-*" "libnvidia-*" 2>/dev/null | grep -v "<none>"'
run_sh "/etc/modprobe.d nvidia" 'grep -rhE "^(options|blacklist).*nvidia" /etc/modprobe.d/ 2>/dev/null'
run_sh "nvidia container toolkit" 'nvidia-ctk --version 2>/dev/null; ls -1 /etc/cdi 2>/dev/null'

# --------------------------------------------------------------------------------------------------
section "12. Pi specifics"
run_sh "model, firmware and throttle" 'cat /proc/device-tree/model 2>/dev/null | tr -d "\0"; echo; vcgencmd measure_temp 2>/dev/null; vcgencmd get_throttled 2>/dev/null; vcgencmd version 2>/dev/null'
run "dpkg architecture" dpkg --print-architecture
run_sh "/boot/firmware/config.txt" 'grep -vE "^[[:space:]]*(#|$)" /boot/firmware/config.txt /boot/config.txt 2>/dev/null'
run_sh "/boot/firmware/cmdline.txt" 'cat /boot/firmware/cmdline.txt /boot/cmdline.txt 2>/dev/null'
run_sh "eeprom" 'rpi-eeprom-update 2>/dev/null; ls -1 /etc/rpi-issue 2>/dev/null && cat /etc/rpi-issue'
run_sh "boot order and root device" 'findmnt -no SOURCE,TRAN,FSTYPE / ; lsblk -o NAME,TRAN,SIZE,MODEL,MOUNTPOINT 2>/dev/null; vcgencmd bootloader_config 2>/dev/null | head -3'
run_sh "raspi packages" 'dpkg-query -W -f="\${Package} \${Version}\n" "raspi*" "rpi-*" "libraspberrypi*" 2>/dev/null | grep -v "<none>"'

# --------------------------------------------------------------------------------------------------
section "13. Security posture"
run_sh "sshd effective settings" 'sshd -T 2>/dev/null | grep -E "^(port|permitrootlogin|passwordauthentication|pubkeyauthentication|kbdinteractiveauthentication|challengeresponseauthentication|x11forwarding|allowtcpforwarding|maxauthtries|loglevel|usepam|permitemptypasswords|authenticationmethods)"'
run_sh "authorized_keys (type and comment only)" 'for h in /root /home/*; do [ -f "$h/.ssh/authorized_keys" ] || continue; printf "%s: %s key(s)\n" "$h" "$(grep -cE "(ssh-(rsa|ed25519|dss)|ecdsa-sha2-|sk-)" "$h/.ssh/authorized_keys")"; awk "{for(i=1;i<=NF;i++) if (\$i ~ /^(ssh-(rsa|ed25519|dss)|ecdsa-sha2-|sk-(ssh|ecdsa)-)/) {t=\$i; c=\"\"; for(j=i+2;j<=NF;j++) c=c (c?\" \":\"\") \$j; printf \"  %s %s\n\", t, c; break}}" "$h/.ssh/authorized_keys"; done'
run_sh "human accounts and groups" 'awk -F: "\$3 >= 1000 && \$3 < 65000 {print \$1\" uid=\"\$3\" shell=\"\$7}" /etc/passwd; getent group admins sudo docker 2>/dev/null'
run_sh "sudoers drop-ins (names)" 'ls -1 /etc/sudoers.d/ 2>/dev/null'
run_sh "sudoers drop-ins (content)" 'for f in /etc/sudoers.d/*; do [ -f "$f" ] && { echo "--- $f"; grep -vE "^(#|$)" "$f"; }; done'
run_sh "/etc/sudoers, non-comment lines" 'grep -vE "^[[:space:]]*(#|$)" /etc/sudoers 2>/dev/null'
run_sh "docker socket and its group" 'ls -l /var/run/docker.sock 2>/dev/null; getent group docker sudo adm 2>/dev/null'
run_sh "shadow summary (no hashes)" 'awk -F: "\$2 ~ /^\\\$/ {p++} \$2 !~ /^\\\$/ {l++} END {printf \"hashes set: %d, locked or blank: %d\n\", p+0, l+0}" /etc/shadow 2>/dev/null'
run_sh "unattended-upgrades" 'systemctl is-active unattended-upgrades 2>/dev/null; for f in /etc/apt/apt.conf.d/*unattended* /etc/apt/apt.conf.d/*auto-upgrades*; do [ -f "$f" ] && { echo "--- $f"; grep -vE "^(//|#|$)" "$f"; }; done'
run_sh "apparmor" 'aa-status --enabled 2>/dev/null; aa-status 2>/dev/null | head -5'

# --------------------------------------------------------------------------------------------------
section "14. Secrets present (names only, never values)"
run_sh "known locations" 'for p in /etc/wireguard /etc/rancher/k3s /etc/restic /root/.config/restic /home/*/.config/restic /etc/apt/auth.conf /root/.docker/config.json /home/*/.docker/config.json; do [ -e "$p" ] && ls -ld "$p" 2>/dev/null; done'
run_sh "secret-shaped files" 'find /etc /root /home /opt -maxdepth 3 -type f \( -name "*.env" -o -name "*.key" -o -name "*.pem" -o -name "*secret*" -o -name "*.p12" -o -name "auth.json" \) 2>/dev/null | head -50'

# --------------------------------------------------------------------------------------------------
section "15. Upgrade readiness"
run_sh "release-upgrades policy" 'grep -vE "^(#|$)" /etc/update-manager/release-upgrades 2>/dev/null; dpkg-query -W -f="\${Package} \${Version}\n" update-manager-core 2>/dev/null'
run_sh "pending dist-upgrade (simulated)" 'apt-get -s -o Debug::NoLocking=true dist-upgrade 2>/dev/null | grep -cE "^Inst"; apt-get -s -o Debug::NoLocking=true dist-upgrade 2>/dev/null | tail -2'
run_sh "upgradable list" 'apt list --upgradable 2>/dev/null | head -40'
run_sh "ubuntu pro" 'pro status --format=json 2>/dev/null | head -20'
run_sh "space for a release upgrade" 'df -h / /boot /boot/firmware /var 2>/dev/null'
run_sh "inodes, apt cache and logs" 'df -i / | tail -1; du -sh /var/cache/apt/archives /var/log /var/lib/docker 2>/dev/null'
run "dpkg audit" dpkg --audit
run_sh "previous failed upgrades" 'ls -1t /var/log/dist-upgrade 2>/dev/null | head -5; ls -1 /var/lib/ubuntu-release-upgrader 2>/dev/null'
if [ "$AUDIT_NETWORK" = "1" ]; then
  run "release check (network)" do-release-upgrade -c
else
  printf '#### release check\n\n```\n(skipped: set AUDIT_NETWORK=1 to run do-release-upgrade -c)\n```\n\n'
fi

# --------------------------------------------------------------------------------------------------
section "16. Recent errors"
run_sh "journal, this boot" 'journalctl -b -p err --no-pager 2>/dev/null | tail -40'
run_sh "kernel ring buffer" 'dmesg -T --level=err,crit,alert,emerg 2>/dev/null | tail -20'

# --------------------------------------------------------------------------------------------------
section "Summary"
printf '```\n'
kv host "$HOST"
kv os "$(. /etc/os-release 2>/dev/null && echo "${PRETTY_NAME:-unknown}")"
kv codename "$(. /etc/os-release 2>/dev/null && echo "${VERSION_CODENAME:-unknown}")"
kv kernel "$(uname -r)"
kv arch "$(dpkg --print-architecture 2>/dev/null || uname -m)"
kv model "$(cat /proc/device-tree/model 2>/dev/null | tr -d '\0' || cat /sys/devices/virtual/dmi/id/product_name 2>/dev/null)"
kv cgroup "$(grep ' /sys/fs/cgroup ' /proc/1/mountinfo 2>/dev/null | sed 's/.* - //' | cut -d' ' -f1)"
kv docker "$(docker version --format '{{.Server.Version}}' 2>/dev/null)"
kv k3s "$(k3s --version 2>/dev/null | awk 'NR==1{print $3}')"
kv k3s_role "$(if [ -d /var/lib/rancher/k3s/server ]; then echo server; elif [ -d /var/lib/rancher/k3s/agent ]; then echo agent; else echo none; fi)"
kv gpu "$(nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -1)"
kv nfs_server "$(systemctl is-active nfs-server 2>/dev/null)"
kv firewall "$(if have ufw && ufw status 2>/dev/null | grep -q 'Status: active'; then echo ufw-active; elif nft list ruleset 2>/dev/null | grep -q 'table '; then echo nftables-present; else echo none-detected; fi)"
kv ntp "$(for s in systemd-timesyncd chrony ntp; do a=$(systemctl is-active "$s" 2>/dev/null); [ "$a" = active ] && echo "$s"; done | head -1)"
kv unattended_upgrades "$(systemctl is-active unattended-upgrades 2>/dev/null)"
kv held "$(apt-mark showhold 2>/dev/null | wc -l)"
kv upgradable "$(apt-get -s -o Debug::NoLocking=true dist-upgrade 2>/dev/null | grep -cE '^Inst')"
kv release_prompt "$(awk -F= '/^Prompt=/{print $2}' /etc/update-manager/release-upgrades 2>/dev/null)"
kv root_free "$(df -h / 2>/dev/null | awk 'NR==2{print $4" of "$2}')"
kv boot_free "$(df -h /boot 2>/dev/null | awk 'NR==2{print $4" of "$2}')"
printf '```\n'
