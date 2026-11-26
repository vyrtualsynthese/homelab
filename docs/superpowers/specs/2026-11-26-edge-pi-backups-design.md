# Edge Pi backups — design

Card 862. 2026-11-26.

> **Status: for review.** Nothing is built until this is approved. The plan follows
> (`docs/superpowers/plans/`), then implementation.

## Intent

The two edge Pis (`rbp0`, `rbp1`) are the LAN's DNS and its WireGuard entry point. The state a
reflash destroys lives on them and nowhere else: `pihole.env` (the Pi-hole panel password),
`etc-pihole/` with the blocklists and settings, and `config/wg-easy.db` (every peer key and the
panel account). A dead disk today means re-issuing every WireGuard client by hand.

`ansible/pi/hosts-pi.sh backup` already knows how to collect that state, but it runs by hand to a
USB stick. What runs unattended is worse than nothing (see below). This design makes it a nightly,
off-site backup into the same OVH bucket as the cluster's four streams, **with the Pi depending on
nothing but the internet**: no timer that waits for the cluster, no export on a cluster node, no SSH
key held by a cluster job.

The cluster keeps one role, and only one: it reads the repositories afterwards and says whether they
are still advancing, so a Pi that silently stops is still noticed.

## What exists today

The card says no cron or timer runs the backup. **It does.** Measured on both Pis on 2026-11-26:

- a **root crontab at 04:00** runs `/home/vronteix/scripts/backup.sh`, a 196-byte file from 2023, and
  has run every night this fortnight (`journalctl | grep scripts/backup.sh`, last seen 04:00:01 each
  night on both hosts);
- the script does `docker system prune -af`, `restic backup /home/vronteix/dockers`, then `restic
  forget --prune`, against `s3:s3.amazonaws.com/ashudev-homelab-<host>-backup`;
- its credentials are in `/etc/environment` (mode 0644), and they are **dead**:
  `restic snapshots` answers *"The AWS Access Key Id you provided does not exist in our records"* on
  both hosts.

So the Pi's state is covered by a job that has been failing, silently, for as long as those keys have
been dead — cron's mail goes nowhere and nothing reads its exit code. That is the failure this design
exists to end, and the retirement of that job is part of it, not an afterthought. The same
`/etc/environment` is also a dead credential sitting in cleartext on the DNS host, and the old path
is AWS, which the provider decision rules out for new work.

## Approaches considered

| | How | Verdict |
|---|---|---|
| **A** | A new CronJob in `cluster/helm/backup` SSHes into each Pi, runs the backup, and restic writes it from the pod. | Rejected by the operator: a backup that only runs while the cluster is up. |
| **B** | Each Pi runs its own systemd timer and its own restic, straight to the bucket. The cluster only verifies. | **Chosen.** Backup survives the cluster being down; the credential stays on the host that already holds the data. |
| **C** | The Pi pushes into an NFS export on `nas`, and the existing `nfs` stream carries it. | Rejected: the landing place is a cluster node, so the promise of B is lost, and it needs a writable export. |

What the choice buys: independence for the Pi. What it costs: the object-storage credential has to
exist on the Pi, and the Pi runs restic — already installed, but at 0.16.4 on `rbp0` and 0.12.1 on
`rbp1`, so this change pins one binary for both. Both costs are accepted here.

`docs/how-it-works/backups.md` calls the edge beyond the cluster "deferred"; this is the change that
ends that.

## The shape

```text
rbp0 ── systemd timer 04:30 Europe/Paris ── restic ──▶ s3:…/ashudev-homelab-backup/edge-rbp0
rbp1 ── systemd timer 04:30 Europe/Paris ── restic ──▶ s3:…/ashudev-homelab-backup/edge-rbp1

> **Corrected 2026-11-26.** This diagram first said the timers were UTC, on the assumption that the
> Pis ran UTC. They do not agree: `rbp0`'s clock is `Europe/Paris` and `rbp1`'s is `Etc/UTC`
> (`timedatectl show -p Timezone`). The calendar in both units therefore names `Europe/Paris`, so the
> two hosts fire at the same instant whatever their own clock is set to.
                                                   ▲
cluster: backup-verify reads both, checks age, contents and budget; prune skips them

retired: the 04:00 root cron on each host, its 2023 script and the dead AWS keys in /etc/environment
```

One repository per host. A repository that holds exactly one host is the rule the chart's prune
already states, and it lets each Pi prune its own snapshots without ever taking a lock the other one
wants. The bucket is the existing one; the prefix is the stream name.

## What is written

The tree is exactly what `hosts-pi.sh backup` writes today:

- `dockers/` — the whole stack directory, including `pihole.env`, `etc-pihole/` and
  `config/wg-easy.db`;
- `etc/hostname`;
- `boot/config.txt`, `boot/cmdline.txt` (the pre-refresh copies, with the PARTUUID guard `restore`
  already applies);
- `bootloader.txt` (the EEPROM configuration), `host.txt`, `docker-ps.txt`.

Three exclusions, each measured on `rbp0` on 2026-11-26, and each named with what it costs:

| Excluded | Size | Why |
|---|---|---|
| `pihole-FTL.db`, `-wal`, `-shm` | 385 MB | Pi-hole's query log. It is a log, rewritten every minute; losing it costs DNS history, not configuration. Same rule the cluster applies to every application's logs. |
| `listsCache/` | 26 MB | Re-downloaded by `pihole -g` from the same lists. |
| `gravity_old.db` | 57 MB | The updater's previous copy of `gravity.db`; the live database is in the same snapshot. Same rule as autobrr's `*_sv*_*.backup` and Plex's `*.db-*`. |

What is left is about 60 MB per host, mostly `gravity.db`, and restic's deduplication keeps the
repository well under the 1 GiB budget. The manual USB copy before a reflash keeps **everything**,
exclusions included: it is one copy, taken once, and the operator may want the query log.

**Databases are copied, not read.** `gravity.db` and `config/wg-easy.db` are live while their
containers run, so the stream copies them through Python's `sqlite3` backup API — the same method
`cluster/helm/backup/files/prepare.py` uses — and the tree holds the consistent copy. Python is
already on both Pis (3.12 on `rbp0`, 3.10 on `rbp1`); the copy code uses nothing newer than 3.10, and
`verify` fails if the interpreter is missing rather than finding out mid-stream. An excluded database
is not copied at all, so the 385 MB of query log is never written to disk to be thrown away.

## The moving parts

### 1. `ansible/pi/hosts-pi.sh` gains `stream <host>`

Requires root (the stack's files belong to container UIDs), requires `/etc/restic/edge.env`, builds
the tree under a **fixed path** — `/var/lib/pi-backup/tree`, wiped before each run — with the
exclusions applied **while copying**, makes the consistent database copies, runs
`restic backup --host <host> --tag stream=edge-<host>`, prints the summary, and removes the tree.

A fixed path, not `mktemp`: restic records the paths it stores, and a path that changes every night
defeats the parent-snapshot comparison and stops the verifier from naming a file inside the snapshot
(the check in §6).

The one definition this keeps: the list of what matters is the code `backup` already uses. `stream`
must not restate it — it calls the same function with an exclusion list passed in.

The same-filesystem refusal `backup` applies (do not stage on the disk you are about to lose) does not
apply here: the temporary tree is transient, and the repository is remote. That is stated in the code
rather than bypassed quietly.

### 2. `setup` installs the timer, and `verify` checks it

`setup` already puts the host back together after a reflash; it gains these things:

- `restic`, pinned to the chart's exact tag (`0.19.1`), installed as a static binary from the upstream
  release into `/usr/local/bin/restic`, root-owned and mode 0555, with the release's SHA-256 checked
  against a constant in the script. **Not from apt**: the two Pis are on different Ubuntu releases
  today (`rbp0` ships restic 0.16.4, `rbp1` ships 0.12.1, measured 2026-11-26), so an apt pin gives
  two writers of two generations on one repository. One pinned binary is the same rule the charts
  apply to their images. GitHub is already an open item in the provider decision and Docker's own apt
  repository is used the same way.
- `python3` is assumed, not installed: 3.12 on `rbp0`, 3.10 on `rbp1`, and the consistent copy uses
  nothing newer than 3.10.
- The script installed as `/usr/local/sbin/hosts-pi.sh`, root-owned, mode 0555. A root timer must not
  execute a file the human account can rewrite. A host with no checkout runs `verify` from there, so
  `setup` also installs the render templates it needs at `/usr/local/lib/hosts-pi/templates`
  (root-owned, 0644), and the script looks beside itself and then there. `setup` keeps rendering from
  `~/hosts/pi`, and `verify` fails when the installed copy and the repo copy differ. (This is hygiene, not a new
  boundary: `vronteix` is in the `docker` group, which is already root-equivalent.)
- `/etc/systemd/system/pi-backup.service` — oneshot, `EnvironmentFile=/etc/restic/edge.env`,
  `ExecStart=/usr/local/sbin/hosts-pi.sh stream <host>`, rendered from a template with the host
  substituted, like the compose template.
- `/etc/systemd/system/pi-backup.timer` — `OnCalendar=*-*-* 04:30:00 Europe/Paris`,
  `RandomizedDelaySec=15min`, `Persistent=true`, enabled and started. Both Pis run at the same
  instant whatever their own clock says; the delay spreads the two uploads without a second table of
  times to maintain.
- **The retirement of the legacy job**: the `04:00` line pointing at `/home/vronteix/scripts/backup.sh`
  is removed from root's crontab, and `RESTIC_REPOSITORY`, `RESTIC_PASSWORD`, `AWS_ACCESS_KEY_ID` and
  `AWS_SECRET_ACCESS_KEY` are removed from `/etc/environment` (the dead keys). Everything else in that
  file stays. The script removes them idempotently and `verify` names them while they remain, so a
  reflash cannot quietly restore the old path either — a reflashed host has no root crontab at all, but
  it does have this repository, and `setup` is what runs on it.

The new timer runs at 04:30, half an hour after the legacy job's slot, so the last legacy run and the
first new one cannot reach for the same files at the same moment.

`verify` fails, naming the difference, when the units differ from the rendered text, when the timer is
not enabled, when the installed script differs from `~/hosts/pi/hosts-pi.sh`, when
`/etc/restic/edge.env` is missing or not mode 0600, when `python3` is missing, or when the legacy cron
line or the dead keys are still present. It does not call the network: a `verify` that needs the
bucket is a `verify` that fails on a bad day.

### 3. The credential

`/etc/restic/edge.env`, root-owned, mode 0600, holds:

```sh
RESTIC_REPOSITORY=s3:https://s3.gra.io.cloud.ovh.net/ashudev-homelab-backup/edge-rbp0
RESTIC_PASSWORD=…
AWS_ACCESS_KEY_ID=…
AWS_SECRET_ACCESS_KEY=…
```

The script never writes it, for the same reason it never writes `pihole.env`: a script that invented
a secret would either commit one or make a new one. It requires the file and names it when missing.
It is created once from the password manager, and listed in `docs/secrets.md` beside `pihole.env`.

**`RESTIC_PASSWORD` is the bucket's existing one, deliberately.** `backup-verify` runs every
repository with one inherited environment — the `backup-restic` Secret — and passes no per-repository
credential, and `docs/runbooks/last-resort.md` and the no-cluster procedure assume one password for
the whole bucket. A second password would mean per-repository credentials in the verifier, which this
design rules out. With the scoped S3 key below, the shared password alone opens nothing: the key
cannot read another stream's objects.

**Least privilege — decided 2026-11-26.** The cluster's bucket user can read every stream. If the Pis
used that same user, a compromised Pi could delete the cluster's backups — the thing backups exist
against. So the Pis get **their own OVH user**, limited to the two repository prefixes
`ashudev-homelab-backup/edge-rbp0/*` and `ashudev-homelab-backup/edge-rbp1/*`. That user, its S3
credential and its policy are a second copy of the three resources already in
`infra-main/ovh/backup.tf` (`ovh_cloud_project_user`, `..._s3_credential`, `..._s3_policy`, with
`ListBucket` on the bucket as the existing policy has it), and `infra-main` is outside this
repository's reach: the change is the operator's, described here so it is one edit, not a hunt. The
cluster's existing user needs no change: its policy already reads every object in the bucket, which
is what lets the verifier read the new prefixes.

**The env file is in the password manager.** What a reflashed Pi needs before its first run is the
four lines of `/etc/restic/edge.env`, so each host's file is stored verbatim as a secure note in the
operator's Bitwarden vault (the same place `homelab-backup-last-resort` already holds the bucket
password). The vault that exists is locked between sessions; storing the two notes is an operator step
because only the operator can unlock it.

### 4. First run, and `restic init`

The repositories are created once by hand, as the four existing ones were; the script never creates a
repository, for the reason the chart's contract gives — a job that can create an empty repository
where a lost one used to be makes a lost backup look healthy. `restic init` needs the same env file,
so it is one command on each Pi after the env file exists.

### 5. Retention runs on the Pi

Each Pi prunes itself weekly with the chart's shared numbers — 7 daily, 4 weekly, 12 monthly — so
retention does not depend on the cluster either. The numbers live in the script, and a test in
`hack/tests/test_pi_script.py` asserts they equal the `retention:` block of
`cluster/helm/backup/values.yaml`, so the two copies cannot drift apart unnoticed.

### 6. The chart: two `external` rows, verified and never pruned

`cluster/helm/backup/values.yaml` gains two stream rows:

```yaml
  - name: edge-rbp0
    budgetGiB: 1
    external: true
    mustMatch:
      - dockers/etc-pihole/gravity.db
      - dockers/config/wg-easy.db
  - name: edge-rbp1
    budgetGiB: 1
    external: true
    mustMatch:
      - dockers/etc-pihole/gravity.db
      - dockers/config/wg-easy.db
```

- `backup-verify` reads them like any stream: a snapshot older than 26 hours, an empty repository or
  one over budget fails the run. This is how a Pi that stops is noticed, given nothing on the Pi
  reports to the cluster.
- **And a lower bound, because freshness alone is not coverage.** A run that copies 60 MB instead of
  the 60 MB it should is fresh and green. So the newest snapshot's file list must hold each
  `mustMatch` path, matched at the end of the stored path — the same reading the chart's `files` rows
  already use, and the reason the staging path is fixed. A `stream` that swallows the exclusions into
  the wrong place, or a `gravity.db` that stops being collected, turns the night red instead of
  looking quiet.
- The prune CronJob skips `external` streams; the Pi does its own. `configmap-streams.yaml` gains the
  `mustMatch` list for an external stream and the verifier reads it; the README's contract gains the
  `external` line and the lower bound with it.

### 7. Restore

```sh
restic restore latest --target /tmp/pi-restore          # the env file names the repository
/usr/local/sbin/hosts-pi.sh restore /tmp/pi-restore/var/lib/pi-backup/tree <host>
```

The restore target keeps restic's stored path, which is why it ends in `tree/`: the snapshot holds
`/var/lib/pi-backup/tree/dockers/…`, and `restore` is given the directory that holds `dockers/`.

The tree is what `backup` writes, so `restore` already consumes it unchanged, `cmdline.txt` guard
included. `docs/runbooks/restore.md` gains a **fifth drill section, in the monthly rotation** (the page
currently rotates 1–4): extract the newest snapshot into a temporary directory, then pass when the
four things a rebuild needs are there — `dockers/etc-pihole/gravity.db` and
`dockers/config/wg-easy.db` both open and pass SQLite's `integrity_check`, `dockers/pihole.env` is
present, and `boot/` holds both files. A byte-by-byte comparison with the live tree is **not** the
test: `docker-ps.txt`, `host.txt` and the three exclusions make the two differ by design. The row goes
in the drill log and in `cluster/helm/monitoring/prometheus/values.yaml`'s `restoreDrills` (the
configmap that publishes the drill age as a metric).

## Documentation, in the same change

- `docs/how-it-works/backups.md` — the edge section replaces the "deferred" row, with the numbers, the
  exclusions and the retired cron named; the coverage table gains the two streams.
- `docs/runbooks/pi-reflash.md` — step 1 confirms last night's snapshot instead of relying on the USB
  copy (the USB copy stays as the second copy); the fresh host needs the env file from the password
  manager before its first run; the rollback points at the repository.
- `docs/runbooks/restore.md` — the restore procedure and the fifth drill.
- `docs/secrets.md` — `/etc/restic/edge.env` and its four keys on both Pis; the OVH user and its scope;
  the removal of the dead keys from `/etc/environment`.
- `docs/audit/ansible-hosts.md` — a correction note: the 2026-11-20 entry says the hosts' cron was
  deleted with the Ansible layer; it was deleted from git only, and the root crontab kept running
  against dead keys until this change.
- `cluster/helm/backup/README.md` — the `external` line in the contract.
- `cluster/helm/backup/values.yaml` — the two rows, with the measured sizes in their comments.

## Non-goals

- No cluster-side pull, no new NFS export, no SSH key held by a cluster job.
- No change to the cluster's four streams, their schedules or their budgets.
- No change to the Pi's compose stack.
- The legacy cron's nightly `docker system prune -af` is **not** replaced. It retired with the job it
  belonged to, and losing it is the point: a nightly prune of every unused image on the house's DNS
  host is not something to keep by accident. The disk has 871 GB free, so nothing needs it.
- The dead 2023 script stays on disk; only its schedule and its credentials go. Deleting a file in the
  human account is not the script's business, and a file with no caller is inert.
- No `restic check --read-data` on the Pi: the Pi has no headroom to read its repository back every
  week, and the verifier's freshness check plus the drill cover what this change is for. If repository
  integrity is wanted later, it is its own card.
- No per-stream alert wiring in Grafana from this repository: the dashboards and rules live in
  Grafana's database. If the stale-backup rule is written per stream, the two new names are added
  there by hand, and the PR says so.

## The operator's steps

1. Add the scoped OVH user in `infra-main/ovh/backup.tf` — the same three resources as `backup`, with
   a policy over the two `edge-rbp0`/`edge-rbp1` prefixes — apply it, and read the pair from
   `terraform output access_key_id` / `secret_access_key`.
2. Write `/etc/restic/edge.env` (0600 root) on `rbp0` and `rbp1`, and store each file verbatim as a
   Bitwarden secure note (`bw unlock`, then the item). The bucket password is the existing
   `homelab-backup-last-resort` item's.
3. `restic init` the two repositories, once, with that credential.
4. Run `hosts-pi.sh stream <host>` once on each, then the restore drill.

**Order matters, because the verifier fails on a repository that does not exist.** Steps 2–4 run
against the branch before the pull request merges: copy `ansible/` to each Pi as the reflash runbook
does, run `setup` (which installs the pinned restic, the units and the retirement), then the env file,
then `restic init`, then one `stream` run. The chart's two rows then land with the merge and the next
`backup-verify` at 08:30 reads two healthy streams. Merging first costs one red verifier night, which
is the correct alarm but a wasted one.

5. Add `edge-rbp0` and `edge-rbp1` to the Grafana stale-backup rule if it names streams one by one.

## Decisions the builder may settle

- Whether the copy-with-exclusions uses `tar`, `rsync` or `cp` with a skip list. Both are present on
  both Pis; `rsync` is the tidier of the three and `tar` is the one that needs no daemon on the other
  side, which does not apply here.
- The shell decomposition of `stream` against `backup` (one shared function, or `backup` calling
  `stream`'s builder).
- The exact `verify` messages and the test cases.

## Anti-goals

- A backup that runs only when the cluster is up.
- A credential written by the script.
- A repository the script could create when it is missing.
- A second list of what state matters.
- A green night that backs up 60 MB where 400 were expected, without a line naming it.
