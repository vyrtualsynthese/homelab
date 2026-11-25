# The Pis' Docker engine, installed by the script — design

**Status:** draft, awaiting approval · **Date:** 2026-11-25 · **Board:** homelab, card #709

**Live basis (measured 2026-11-25):** the `resolute` suite of Docker's Ubuntu repository, read from
`https://download.docker.com/linux/ubuntu/dists/resolute/pool/stable/arm64/Packages.gz`; the Pis'
installed engine, 28.3.2 on both, from [the 2026-11-24 record](../../audit/hosts-2026-11-24.md).

## Intent

A reflashed Pi has no container runtime. The compose file the script writes names four containers,
its `setup` runs `docker compose up -d`, and its `verify` runs `docker compose ps`, so all of that
fails until someone installs Docker by hand — runbook step 6 today. The card moves that step into the
repository so a rebuild is a command.

## What it does not change

- **The compose files.** `playbooks/templates/runners/rbp{0,1}/docker-compose.yml` keep their
  containers and image pins; what changes is that the engine they need is now declared beside them.
- **The Ansible layer.** The Pis are outside it by decision D4 of [the hosts
  design](2026-11-24-hosts-stabilise-and-ubuntu-upgrade-design.md), and no role names Docker. So
  `site.yml --check --limit edge` still reports no change on a rebuilt Pi: nothing in the layer
  competes with the script. (The card's done-when is exactly that, and the layer holding no Docker
  fact is what makes it true rather than a thing to prove.)
- **Ubuntu's `docker.io` / `docker-compose-v2`.** Not used: the release's own suite is in Docker's
  repository for arm64, so the runbook's fallback question is answered and the pair never needs to
  land on a Pi.

## Decision: inside `setup`, not a fifth verb

`setup` is defined as "bring a freshly flashed Pi to the declared state". The runtime is part of that
state, and a fresh host whose `setup` fails because another verb has not been run is the failure this
card exists to remove. So `cmd_setup` grows an `ensure_engine` step, before it starts the stack:

```
hostname check → require_env → ensure_engine → write files → docker compose up -d
```

The reflash sequence keeps its shape: `restore` → `setup` → `verify`, then the two-play Ansible
bootstrap. `setup` now needs the network on a fresh host; on a converged one it does not.

## How the install works

- **The suite is read, not hard-coded.** `UBUNTU_CODENAME` (falling back to `VERSION_CODENAME`) from
  `/etc/os-release`, the same way the script reads `/proc/mounts` instead of assuming `sda`. A
  reflash onto the next release then names the new suite instead of pointing apt at a codename that
  no longer exists. An empty value fails the run with the file it read.
- **The repository is Docker's own**, described as a deb822 file at
  `/etc/apt/sources.list.d/docker.sources` (types/URIs/suites/components/architectures, signed by
  `/etc/apt/keyrings/docker.asc`). The key is downloaded to a temporary file and compared with the
  installed one, so a rotated key is picked up and a converged host writes nothing.
- **Pins are bands, not exact versions:**
  `docker-ce=5:29.*`, `docker-ce-cli=5:29.*`, `containerd.io=2.4.*`,
  `docker-compose-plugin=5:8.*`.

  Why a band: nothing in this repository bumps an apt pin (Renovate does not read bash), so an exact
  version is a pin that silently ages — which is what happened to the 28.3.2 both Pis run today. A
  band reproduces the same major on every rebuild and refuses a major jump; the comment beside each
  constant carries the version measured on 2026-11-25 (`29.10.1`, `2.4.2`, `5.8.0`) as the record.
  When a suite carries only a newer major, the install fails naming the pin, and moving it is a
  deliberate edit.
- **`vronteix` joins the `docker` group**, as step 6 does today; the group is created by the package,
  so the order is package first, `usermod -aG` second, and it is skipped when already a member.
- **Root is required and said plainly**: `setup` without `PI_ROOT` and without uid 0 exits 1 with
  "run it with sudo" before touching apt.
- **A converged host does no network work**: if all four packages match their band and the keyring and
  sources file exist, `ensure_engine` returns without a fetch, and `changed` stays 0.

## `render` and `verify`

- `render` prints what would be installed and where the repository line comes from — it is the verb
  to read before a change, so it must name the pins.
- `verify` names every difference it can see: a missing package, a version outside its band, a missing
  `docker compose`, a `vronteix` outside the `docker` group. It keeps its current sentence per
  problem and its exit 1.
- Under `PI_ROOT` both say `engine: not checked (PI_ROOT is set)`, because a test fixture has no dpkg
  and no apt — the same treatment the stack already gets ("stack: not started").

## Tests

`hack/tests/test_pi_script.py`, no host required:

1. `render` prints the four package names and their bands for each Pi.
2. `setup` with `PI_ROOT` prints `engine: not touched (PI_ROOT is set)` and writes nothing under
   `$PI_ROOT/etc/apt`.
3. `verify` with `PI_ROOT` prints `engine: not checked (PI_ROOT is set)`.
4. The suite is read from `/etc/os-release`: the script names that file and does not contain a release
   codename, mirroring `test_the_root_device_is_read_not_assumed`.

## Docs

- `ansible/pi/README.md`: the engine joins the "what it owns" table, and the reflash sequence says
  what `setup` now does.
- `docs/runbooks/pi-reflash.md`: step 6 becomes a check rather than an install, the "nothing installs
  it yet" line goes, and "What the first run has to answer" loses its Docker bullet — answered here.
  The banner's status stays `pending` until a real reflash runs it.

## What is left to the operator

- The first reflash is the proof: `setup` on the new image, then `verify`, then
  `site.yml --check --limit rbp1` reporting no change.
- The compose plugin crosses majors on that reflash (the Pis run Compose v2, `resolute` carries
  plugin 5.x). The templates carry no `version:` key and only plain Compose Spec keys, so nothing
  needs changing for it — but the first run is where that is proved, not here.
