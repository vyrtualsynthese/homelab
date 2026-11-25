# One compose template for the two edge Pis

**Date** 2026-11-25 · **Status** proposed · **Repo** `homelab`

## What this is

`ansible/pi/hosts-pi.sh` writes each Pi's `/home/vronteix/dockers/docker-compose.yml` from
`ansible/playbooks/templates/runners/<host>/docker-compose.yml`. The two directories hold the same
file. With comments stripped the diff is one line: the WireGuard UDP port, `51821` on `rbp0` and
`51822` on `rbp1`, because the router forwards one public port to each.

This change replaces the pair with one template and makes the port a per-host value in the script.
The same change has `setup` place the two config files the stack bind-mounts and that no code
places today.

## Why now

- **Drift.** Every image bump is two Renovate commits, and the two files are identical today only
  because Renovate wrote both. Nothing fails when they stop matching.
- **Files the stack needs and nothing writes.** The compose bind-mounts
  `./etc-pihole-updatelists/pihole-updatelists.conf` and `./etc-dnsmasq.d`, and
  `templates/runners/` already carries both (`pihole-updatelists.conf`,
  `02-my-wildcard-dns.conf`). No script, role or runbook copies either. Docker creates a missing
  bind-mount source as a **directory**, so Pi-hole can boot with a directory where its updater
  config belongs; and the dnsmasq drop-in is what answers `*.kub.home.ashudev.com` on the LAN, so
  losing it breaks name resolution with no error anywhere.

The third orphan, `templates/runners/daemon.json`, stays out of scope: it opens 9323, which
Prometheus scrapes on both Pis, but writing it means restarting Docker — that is card #709 (Docker
in the host layer), which owns installing Docker in the first place.

## Goals

- One compose template; every host renders the same bytes apart from the port.
- `setup` places the two config files the stack mounts; `verify` names a difference in any of the
  three; `render` prints all three.
- The README and the reflash runbook describe what the script actually does.

## Non-goals

- Image upgrades. The Pis run Pi-hole `2026.07.2` and wg-easy `14` against templates pinned to
  `2026.11.0` and `15`; that is a separate change with a host to test on.
- Log caps, healthchecks, binding `5000`/`9902` to a narrower interface, `daemon.json`, installing
  Docker, and the boot files.

## Approaches considered

| Approach | Trade-off | Verdict |
|---|---|---|
| One template, port substituted per host | One file to bump, drift impossible, still one file on each Pi | **Picked** |
| Two files, plus a test that fails when they diverge | Plain YAML a human can edit; duplication stays and Renovate still bumps twice | rejected |
| Shared base plus `docker-compose.<host>.yml` override | Standard Compose, but two files on the host, two to verify, `-f` twice in every call | rejected |

## The design

**Template.** `ansible/playbooks/templates/runners/docker-compose.yml`, one file, CRLF preserved
(the current files are CRLF; converting them would make every host's next `setup` rewrite a
functionally identical file for nothing). The wg-easy port line becomes
`- "@WG_UDP_PORT@:@WG_UDP_PORT@/udp"`. The `rbp0/` and `rbp1/` directories are deleted.

The task-480 note about the removed router stays in the shared file, reworded for both hosts: the
cluster's Traefik owns 80/443 and the certificate store, and a second router on either Pi is how
one comes back by accident. `rbp1`'s header pointing at upstream `pi-hole/docker-pi-hole` is
dropped — the image is `jacklul/pihole`.

**Script.** Two functions beside `desired_hostname`:

- `wg_udp_port <host>` → `51821` for `rbp0`, `51822` for `rbp1`.
- `desired_compose <host>` → the template with the placeholder replaced.

`render`, `setup` and `verify` all read through `desired_compose`, so there is one definition of
what the file should be. `render` gains `--- <path>` blocks for the two new files.

**Placement.** `setup` writes:

| Template | Destination |
|---|---|
| `runners/docker-compose.yml` | `/home/vronteix/dockers/docker-compose.yml` |
| `runners/pihole-updatelists.conf` | `/home/vronteix/dockers/etc-pihole-updatelists/pihole-updatelists.conf` |
| `runners/02-my-wildcard-dns.conf` | `/home/vronteix/dockers/etc-dnsmasq.d/02-my-wildcard-dns.conf` |

Both new destinations are inside the stack directory, so `backup` already covers them and `restore`
already brings them back.

**Two rules the writes need.**

1. *A destination that is a directory is refused, never deleted.* That is Docker's artefact of a
   bind mount whose source was missing, and it may hold something. `setup` prints the path, says
   why it is a directory, and exits 1; the operator removes it and reruns.
2. *A changed mounted file restarts pihole.* A bind mount is resolved when the container starts, so
   a file created afterwards is invisible until it restarts. `setup` always runs the plain
   `docker compose up -d --remove-orphans` first — `up -d <service>` converges that service alone,
   so this is the run that applies a definition or image change to the other three — and then, when
   either of pihole's two files changed, `docker compose up -d --force-recreate pihole`: one
   container, a couple of seconds of DNS, and only on a run that changed something.

`verify` compares the same bytes `setup` writes, so a file `setup` leaves alone is one `verify`
accepts — a file with a missing final newline is rewritten, not blessed.

`verify` compares the rendered bytes, not the template, and diffs the same way it does for
`/etc/hosts`.

## Tests (`hack/tests/test_pi_script.py`)

- Both hosts render; the two outputs are equal after the port line is normalised.
- The shared template declares no `traefik` service (the existing test moves to the new path and
  covers both hosts by rendering rather than by reading one file).
- `setup` writes what `render` printed, byte for byte, including `\r\n`; a second `setup` prints
  `changed=0`.
- `verify` names each of the three files when it differs.
- A destination that is a directory is refused and left in place.
- The port is the only difference: `51821` never appears in `rbp1`'s render and `51822` never in
  `rbp0`'s.

## Docs touched

- `ansible/pi/README.md`: the owned-path table gains the two config files, and the compose row says
  it is one template rendered per host.
- `docs/runbooks/pi-reflash.md`: the sources line, and step 7's claim that `setup` rewrites
  `/boot/firmware/config.txt` and `cmdline.txt` — it never does, and the same paragraph two
  sentences later says so.
- The two audit pages are dated records of what the hosts held on 2026-11-24 and are not rewritten.

## Calls made without asking

- **Log caps left out.** Four quiet services write json-file logs with no rotation. The effect over
  a year is real but nobody has measured a problem, and adding it recreates every container on
  both hosts on the next setup. It gets a card if the operator wants the disk protected.
- **CRLF kept** rather than normalised, so the first `setup` after this change rewrites nothing on
  a host that is already correct.
