# Network state: a read-only collector for the network hardware — design

**Status:** draft, awaiting approval · **Date:** 2026-11-24 · **Board:** homelab
**Live basis (measured 2026-11-24):** SSH on port 22 answers on both MikroTiks, `192.168.1.11`
(CRS326, identity string `CR326`) and `192.168.1.43` (CRS309, identity left at `MikroTik`); the
Livebox at `192.168.1.1` serves a SoftAtHome login shell, firmware build `v1.0.161_20260923`.

## Intent

Give an agent, or the operator, the **current** configuration of the network hardware as text, in
one command, without logging into a device by hand.

The repository holds the *declared* network state: the forwarded ports in
[`docs/how-it-works/network.md`](../../how-it-works/network.md), the edge Pis' compose stacks in
`ansible/playbooks/templates/runners/`, the switch addresses in `docs/reference/metrics.md`. It holds
nothing of the devices' own configuration — the firewall filters, the bridge VLAN table, the port
trunks, the DHCP server, the actual RouterOS version. An agent asked "why does that port reach the
NAS" today has to guess or ask.

## Current state

| | |
|---|---|
| MikroTik CRS326 | `192.168.1.11`, RouterOS, scraped by `mktxp`; identity is `CR326`, a typo in the device config |
| MikroTik CRS309 | `192.168.1.43`, RouterOS, scraped by `mktxp`; identity is the untouched default `MikroTik` |
| Livebox | `192.168.1.1`, Orange / SoftAtHome, plain NAT; no SSH, no documented API |
| Credentials on this workstation | none — `~/.config/secrets/homelab.env` holds no router key, and `mktxp`'s working pair lives in the `monitoring/mktxp-conf` Secret, which the read-only session cannot read |
| Precedent | `ansible/collectors/host-audit.sh` — a read-only collector that prints Markdown, with a redactor and `hack/tests/test_host_audit.py` proving it — and its output, [`docs/audit/hosts-2026-11-24.md`](../../audit/hosts-2026-11-24.md), which is the page shape this collector copies |

Two RouterOS facts decide the shape: `/export` prints the device's whole configuration as text, and
it hides sensitive values unless asked for them (`show-sensitive=no` on RouterOS 7, `hide-sensitive`
on RouterOS 6). So the safe dump is the default one.

The Livebox is not the same kind of thing. Its only configuration export is a binary backup file its
web interface hands out after an admin login; that file is opaque, version-bound, and unreadable by
an agent or a diff.

## Goals

1. `ansible/collectors/network-audit.sh` prints one Markdown page: a section per device, the
   device's own `/export` text, and a short status block (identity, model, serial, RouterOS version,
   uptime, interface link state).
2. Nothing is written to a device. The account used on the Mikrotiks carries the RouterOS `read`
   policy (plus `test` for ping/traceroute if wanted), and the script never passes `show-sensitive`
   or omits `hide-sensitive`.
3. No secret can reach the page: RouterOS text passes through a redactor written for RouterOS
   attributes, and a test proves the secret shapes (`wpa2-pre-shared-key`, `wpa-pre-shared-key`,
   `preshared-key`, `private-key`, `authentication-password`, `encryption-password`, `password`,
   `snmp` community) are removed wherever they sit on the line, while `public-key`,
   `policy=read`, `mode=dynamic-keys`, addresses and port names survive.
4. A device that is down, slow or refuses authentication appears in the page as unreachable with
   the reason. The run still produces a usable page.
5. The renderer is testable with no device: a fixtures directory supplies recorded output and the
   whole page is produced from it.
6. The Livebox section states only what was read from the box without a credential, and labels
   everything else as not read from the device.

## Non-goals

- Writing configuration back to a device, or correcting drift.
- A restore-capable backup (RouterOS `.backup` binaries, the Livebox backup file). Different
  artefact, different home, different decision.
- The authenticated Livebox read (port forwards, WAN address, DHCP leases). Follow-up card; it needs
  the admin password and its endpoints are unverified on this firmware.
- Nightly collection in the cluster, and history over time.

## Anti-goals

- A page that needed `show-sensitive` to be worth reading.
- Any file in the repository holding an unredacted dump, even briefly.
- A Livebox section that reads like live state when it is a copy of the sentence in the network page.

## Approaches

Three were on the table; **A** was chosen.

| | How it works | Trade |
|---|---|---|
| **A — shell collector beside `host-audit.sh`** | SSH, `/export`, print Markdown | Cheapest, no new dependency, reuses the test style and the page shape; the page is the device's own wording |
| B — Python client over the RouterOS API | structured objects instead of text | Easy per-port deltas later; a library, more code, no better for a snapshot |
| C — CronJob in the cluster, output into the OVH bucket | nightly, no credentials on the workstation | unreadable without a restore, and agents are the point; a later step, not the first |

A also means the collector follows the practice this repository has already proved about handing
device text to an agent — a redactor with tests on both sides — while the redactor itself has to be
written for RouterOS, because the host one is not safe on it (see Decisions).

## Decisions

**Home and name.** `ansible/collectors/network-audit.sh`, next to `host-audit.sh`, so the device
collectors stay one family and one README section covers both. Tests in
`hack/tests/test_network_audit.py`.

**Access is a key, not a password.** The operator creates the account once per Mikrotik and imports
a public key; the script then authenticates with `ssh -i`. No password appears in an argument, an
environment variable or this repository.

```
/user group add name=audit policy=read,ssh
/user add name=audit group=audit
/user ssh-keys import public-key-file=network-audit.pub user=audit
```

The group carries `ssh` because RouterOS counts SSH access as a policy of its own: a group holding
`read` alone cannot open the session, whatever else it may read. `test` is not granted — `/export`,
`/print` and `/interface ethernet monitor` are all `read` commands.

The private key stays in `~/.ssh/`; its path is `NET_AUDIT_KEY`, default `~/.ssh/network-audit`.
RouterOS builds that predate ed25519 import need an RSA key instead — recorded on the page, not
worked around in the script.

**What is captured per Mikrotik.** `/export` (RouterOS 7, where sensitive values are hidden by
default) or `/export hide-sensitive`
(RouterOS 6), chosen after reading `/system resource print`; then `/system routerboard print`,
`/system package print`, `/system identity print`, `/interface ethernet monitor [find] once`,
`/ip address print`, `/ip route print`. Enough to answer "what is configured" and "what is up", nothing that changes.

Why the status block: `/export` carries no link state, no uptime and no RouterOS version, and
"the port is administratively up but the link is down" is exactly the kind of finding an audit is
for.

**Redaction is written for RouterOS, not borrowed.** A builder check ran the shared
`host-audit.sh --redact` over fourteen plausible export lines and found three defects: the
`wpa2-pre-shared-key` and `wpa-pre-shared-key` attributes pass through in clear; a secret that is
not the first `key=value` on a line passes (`... private-key="…" listen-port=…` came out intact);
and the flag scanner destroys a real value, turning `mode=dynamic-keys` into `<redacted>` because
`wpa2-psk` sits just before it.

So the collector carries its own `redact_routeros()`: every `name=value` pair on the line is
examined, a known-sensitive attribute name keeps the name and loses the value, and `public-key` is
allowed before the deny list so a peer's public key stays readable. `host-audit.sh --redact` is not
applied to RouterOS output at all. The unit test on those shapes is the real guard — and it has to
be, because `hack/check-docs.sh`'s secret scan only looks for keys containing password, passwd,
token, secret or api_key, so it would pass a leaked `wpa2-pre-shared-key` without a word.

**Output.** Markdown on stdout, like `host-audit.sh`. The script prints the whole page, banner
included, mirroring [`docs/audit/hosts-2026-11-24.md`](../../audit/hosts-2026-11-24.md): the five
labels `hack/check-docs.sh` requires — **In one line**, **Use it when**, **Verified**, **Kind:**
and **Sources** — with `Kind: record`, a date from `NET_AUDIT_DATE` (default: today), and a closing
"What this record does not cover" section. `Kind: snapshot` is not one of the four kinds the checker
accepts, so the older `traefik-2026-11-21.md` banner is not the model. The operator redirects it:

```
./ansible/collectors/network-audit.sh > docs/audit/network-2026-11-24.md
```


**Failure behaviour.** `ConnectTimeout=5`, `BatchMode=yes`, per-command timeout. Unreachable or
refused targets are reported in the page. Exit `0` when at least one device answered, `1` when none
did, `2` on usage error.

**Configuration.** `NET_AUDIT_TARGETS` (default `crs326=192.168.1.11 crs309=192.168.1.43`),
`NET_AUDIT_USER` (default `audit`), `NET_AUDIT_KEY`, `NET_AUDIT_DATE`, `LIVEBOX_HOST` (default
`192.168.1.1`), and `NET_AUDIT_FROM_FIXTURE` for tests.

**The Livebox section, in this version.** Read without a credential: whether `/` answers, its HTTP
status, and the firmware build string from the `sah=` query on `loader.js` (`v1.0.161_20260923`
today).

There is less remembered material than this design first claimed. `docs/how-it-works/network.md`
holds no DHCP facts and no forward table — one sentence naming the ports forwarded to
`192.168.1.46`, verified 2026-11-22. The page quotes that sentence, dates it, and marks it "not
read from the device", so an agent can tell measured from remembered. Widening the remembered half
means reading the box with its admin password; that is the follow-up card. The operator chose the
thin section for this version, so the first snapshot is honest about its narrowness rather than
waiting on an endpoint nobody has proved.

**Docs, and the order they land in.** `mkdocs.yml` needs the page under History > Audits — that one
is enforced. The row in `docs/audit/README.md` is convention only; nothing checks it, and
`hosts-2026-11-24.md` landed without one. The pointer from `docs/how-it-works/network.md` and the
example command in `ansible/README.MD` must land **with** the first snapshot page, not before:
`hack/check_docs.py` resolves every `docs/*.md` path cited anywhere in the repository, and
`hack/check_docs_links.py` fails an unresolved relative link, so citing the page first fails the gate.
`ansible/README.MD`'s "the one thing in this directory that works today" becomes false with a second
collector and is reworded in the same change.

**Board.** One `task` card for the collector: `Doing` when the work starts, `Validation` with the
branch, the PR and what was verified. One `task` card for the authenticated Livebox read, in
`Backlog`, with the credential gap named.

## What is left to the builder

- Section order and wording inside the page, and how a device's export is fenced.
- How the RouterOS version is detected and which flag each generation gets.
- Whether the interface status block is one table or one line per port.
- The exact fixture files the tests use.

## Verification

1. `bash -n ansible/collectors/network-audit.sh` and `shellcheck` if present.
2. `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests`, including
   the new redaction and render-from-fixture cases.
3. A render from fixtures checked structurally: the banner passes `hack/check_docs.py`, no fixture
   secret appears, a line near the end of the export is present, and the page ends with the
   "does not cover" section. A checked-in golden page is deliberately not used — the banner carries
   the script version.
4. `hack/check-docs.sh --base origin/develop` once the snapshot page is added — and it fails if the
   page is cited from anywhere before it exists.
5. **The one proof this repository cannot run:** a live run by the operator with the real key, on
   both devices, whose output becomes the first snapshot. I cannot authenticate to either device, so
   the script ships with fixture-level evidence only, and the live run is the operator's step.

## Risks

- **RouterOS version unknown.** Both devices are 838-series hardware and may run v6. The flag
  differs; the script reads the version first and the page records it.
- **Key import on older RouterOS.** ed25519 may be refused; RSA is the fallback.
- **The Mikrotik may hold no SSH-enabled user beyond `admin`.** Creating the read-only account is a
  write on the device and stays the operator's step.
- **The authenticated Livebox read may never be worth it.** That is why it is a card and not code
  now; the unauthenticated part is in this version because it is measurable today.
- **A snapshot ages.** The page states its date in the banner and in the first line of the Livebox
  section; nothing pretends to be current.
