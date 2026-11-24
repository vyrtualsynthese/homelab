# Declared host access: a laptop is adopted by a line in git — design

**Status:** draft, awaiting approval · **Date:** 2026-11-25 · **Board:** homelab, card #711
**Live basis (measured 2026-11-25):** `ansible/`, its tests, the two role implementations, `ansible.cfg`
and [`docs/decisions/2026-11-24-automation-sudo-account.md`](../../decisions/2026-11-24-automation-sudo-account.md).
Each claim about today's behaviour below was read from the file it names, not inferred from a run,
except the four SSH probes recorded under [What exists today](#what-exists-today-and-the-three-facts-that-shape-the-work).

## Intent

The six hosts accept SSH keys that no file in this repository records. `roles/automation` copies
whatever `vronteix`'s home directory holds onto the automation account, and that copy is the whole
trust model: a laptop is adopted by a person copying `~/.ssh/homelab_live` onto it, and retired by a
person deleting a line on six machines, from memory.

This makes **the list of keys that may log in a declared value**. One variable, two files per host,
written by the layer. Adoption is an added line and a run. Retirement is a deleted line, and `make
check` — the drift report the layer already exists to produce — names the hosts that still hold what
git says should be gone.

Why now: the layer only became runnable on 2026-11-24, and the operator runs it from more than one
laptop. That is what exposed the gap. Nothing else depends on this change, and no host moves.

## What exists today, and the three facts that shape the work

| Where | What it does | What it means for this design |
|---|---|---|
| `roles/automation` | reads `{{ getent_passwd[automation_key_source_user][4] }}/.ssh/authorized_keys`, then copies it on the host to `~ansible/.ssh/authorized_keys` with `remote_src: true` | the one place a key is chosen; it is replaced, not extended |
| `roles/accounts` | closes password logins, removes `%admins`, never touches an `authorized_keys` | the human file has no owner in the layer at all |
| `inventory/group_vars/all.yml` | `automation_user: ansible`, `automation_key_source_user: vronteix` | the second variable disappears; a `human_user` takes its place |
| `ansible/ansible.cfg` | sets inventory, `host_key_checking = True`, `lock_timeout`, no `private_key_file` | `ssh -G ansible@192.168.1.27` resolves to `~/.ssh/id_rsa`, `~/.ssh/id_ed25519`, … — never `~/.ssh/homelab_live`, which `config/ssh/config` selects only for the `*.local` aliases |
| `hack/tests/test_ansible_layer.py` | `test_the_automation_account_takes_its_key_from_the_human_account` asserts `remote_src: true` and refuses `lookup('file'` | this design replaces that test; it asserts the reasoning being retired |
| `docs/decisions/2026-11-24-automation-sudo-account.md` | "The account therefore trusts nothing that was not already trusted" and "no `*.pub` enters the tree" | a decision record that this one supersedes, in part |

The third fact is a defect independent of the rest: **today's run authenticates with whatever default
identity the laptop happens to own.** It works because the human account's keys have been pasted there
by hand over time, and it stops working on a laptop that has only `homelab_live`.

Four probes on 2026-11-25 from the validated laptop, `ssh -o BatchMode=yes -i ~/.ssh/homelab_live
<user>@<host> true`, because the answer decides both the scope and the risk of naming the identity:

| Host | `ansible` | `vronteix` |
|---|---|---|
| `nuc0` (192.168.1.27) | **accepted** | accepted |
| `rbp2` (192.168.1.48) | **accepted** | not probed |
| `rbp0` (192.168.1.22) | `Permission denied (publickey,password)` | not probed |
| `rbp1` (192.168.1.38) | `Permission denied (publickey,password)` | not probed |

So `homelab_live` already reaches the four cluster nodes **as both accounts**, from at least one
laptop, and the two edge Pis have no `ansible` account at all until they are reflashed (card #481).
That is why the first apply below is a four-host apply, and why the Pis' path is the reflash runbook.

## The decision

**Declared, exclusive, two accounts, one list.** In `inventory/group_vars/all.yml`:

```yaml
# Every key in this list logs in as both accounts, on all six hosts. The
# automation account holds `ansible ALL=(ALL) NOPASSWD: ALL`, so a key here is
# root on the fleet. One line per laptop; the comment names whose it is.
trusted_ssh_keys:
  - "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI... vyrtualsynthese@xps13"
human_user: vronteix
```

- The list is written to `~{{ human_user }}/.ssh/authorized_keys` and
  `~{{ automation_user }}/.ssh/authorized_keys`, mode `0600`, `exclusive: true` on both.
- A key that is not in the list is **removed** on the next apply. That is the point: it is what makes
  retirement real and what lets `make check` see a key nobody declared.
- `exclusive` is what makes this a declaration rather than a wish. Without it the variable documents
  access instead of controlling it.
- **One pair per laptop**, at the same path on every machine (`~/.ssh/homelab_live`). Two laptops are
  two lines with two fingerprints, so retiring one is deleting one line and the other keeps working.
  The private half never moves between machines — that is the whole reason to generate instead of copy
  — which is also what makes the path a constant and the key a variable.

### The trade, stated once

- **A key in this list is root on six hosts.** The automation account has passwordless sudo and the
  human account is in `%sudo`. A laptop is one of the six most trusted objects in this homelab, and
  the variable says so in its own comment rather than letting a reader assume otherwise.
- **A public key is committed to a public repository.** The rule "never commit `*.pub` keys" stays
  literally true — this is a YAML scalar, not a key file — and is superseded in intent. A public key
  identifies a laptop; it cannot log in without the private half, which never leaves the machine that
  made it. What leaks is the *existence* of the laptops, which the inventory already shows.
- **`${HOME}/.ssh` on the controller stays off limits to an agent.** No task reads a key file on the
  machine running the play. The operator pastes the line, the same way they paste a token into
  `./setup keys` today.
- **Retirement has a window.** Removing the line stops the next login after the job that removes it
  runs; a session already open stays open. Named in the runbook, not fixed here.

### Alternatives, and why not

- **Append (`make adopt` writes the host, nothing records it).** One command and no variable. But a key
  nobody revoked is invisible: `make check` cannot report it, and the six hosts become the only record
  of who has access. This is today's model with a shorter command.
- **A runner on `rbp0` (card #711's original shape).** Real, but it answers a different question: the
  operator's work is deliberately manual — a dist-upgrade, a node-by-node move, a configuration
  change — so a second machine buys nothing until the same command has to run while the laptop is shut.
  This design makes any laptop equivalent, which is the prerequisite for that card either way.
- **An SSH certificate authority.** Six hosts and three laptops do not justify one; the CA's private
  key becomes the single object to protect and the thing that has to be on every laptop to sign keys.
  Revisit if hosts ever number in the dozens.

## The shape of the change

### Which role writes which file

| Role | Owns | Stops doing |
|---|---|---|
| `automation` | the automation account, its `authorized_keys`, its sudoers file, and the assertion that the list is usable before an account exists that nothing can reach | reading and copying `vronteix`'s file — the three `getent`/`stat`/`copy` tasks and their `automation_key_source` fact |
| `accounts` | `~{{ human_user }}/.ssh/authorized_keys`, then the removals it already makes | nothing |
| `playbooks/site.yml` | unchanged: `automation` before `accounts` | — |

The order is what keeps the two-run bootstrap intact: `--tags automation` as the human creates the
account **and its keys**, so the account is reachable before `accounts` closes the human's password
path. The reason the old role copied the human file — "an account created without a key cannot be
logged in to" — still holds; it is now satisfied from the declared list instead of by inheritance.

### The five fences against locking everyone out

1. `assert` that `trusted_ssh_keys` is a non-empty list. An empty variable is the one edit that takes
   root away from all six hosts at once.
2. `assert` the shape of every entry: one of `ssh-ed25519`, `ssh-rsa`, `ecdsa-sha2-nistp256`,
   `sk-ssh-ed25519@openssh.com`; then a base64 body; then an optional comment. A truncated paste is
   the other way to lose the fleet.
3. `backup: true` on both writes, so the previous file is on the host beside the new one.
4. **The migration reads the diff before it writes.** `make check --limit <host> --diff` prints every
   key that *would be removed*. Anything the operator still wants is added to the list first. This is
   the step that captures the keys already on the hosts, which the agent cannot read.
5. **One host first**, `--limit nas`, read the result, then the rest. The repository already states
   this rule for anything that can lock out; this is the strongest case of it. `nas` is a worker, so a
   bad run costs a worker's access and never an etcd member's.

The floor if a bad list is applied anyway: the run that writes it is already authenticated, so the
damage surfaces on the next connection, and the console on `nas`/`nuc0` and physical access to the Pis
are the way back. The runbook says where they are.

### `make adopt` — the command, as chosen

Declared does not mean hand-typed. `make adopt` reads `~/.ssh/homelab_live.pub` on the machine that
runs it, proves the `.pub` matches the private key beside it (`ssh-keygen -y`, the stricter of the two
checks), refuses a file that is not an OpenSSH public key, prints the fingerprint (`ssh-keygen -l -f`),
and prints the YAML line ready to paste:

```bash
make adopt        # prints the entry, its fingerprint, and the next three steps
```

It runs on the **new** laptop and touches no network: that machine cannot reach a host yet, and does
not need to in order to print its own public key. It does **not** edit `group_vars/all.yml`. A YAML
file with its comment block is worth more than the saved keystroke, and this repository reviews its
changes through a pull request; a tool that appended would be the only writer of a reviewed file. The
command exists to format, to fingerprint, and to refuse — not to write, and not to scan.

### The identity is named

`ansible.cfg` gains `private_key_file = ~/.ssh/homelab_live`, so the layer stops depending on whichever
default identity a laptop happens to hold. This is the one change that can take access away from a
laptop *before* any diff can be read, so it lands as its own commit, after each laptop has answered the
probe in the table above for itself. Ansible passes it as `-o IdentityFile=…`, which **replaces** the
default identity list rather than adding to it — verified against `ssh -G`.

## The procedure (what the operator does, and what the runbook will say)

1. **New laptop.** `./setup sync` and `./setup keys` from the `config` repository (§Out of scope); until
   that entry lands, `ssh-keygen -t ed25519 -f ~/.ssh/homelab_live`. Then `make adopt` in a `homelab`
   clone. It prints one line.
2. **Survey what is already trusted.** `sudo ./collectors/host-audit.sh` on one host prints every
   `authorized_keys` under `/root` and `/home/*` as key type and comment, never key material. If a
   third account holds a laptop key, the two-file promise is incomplete on that host and the design
   says so here rather than discovering it in a year.
3. **Validated laptop.** Paste the line into `trusted_ssh_keys`, commit on a branch, open a pull
   request. Then `make check --limit nas --diff` and read every key it offers to remove; add any that
   are still wanted. Then `make apply --limit nas`, then the other three cluster nodes. The two edge
   Pis are not in this pass: they have no `ansible` account until they are reflashed (card #481), and
   their runbook's step 8 is rewritten in the same change so the reflash bootstraps from this list.
4. **New laptop, proving it.** `ssh -o BatchMode=yes -i ~/.ssh/homelab_live ansible@nas true`, then
   `make init` and `make check --limit nas` — no `changed` line is the proof, because a missing key is
   drift the role now reports.
5. **Retirement.** Delete the line, `make apply`. `make check` on any host that still holds it is the
   check that it worked; a key seen there is a host that did not get the run. A session already open
   stays open until it ends.
6. **Host keys.** From the validated laptop, `ssh-keyscan -t ed25519 192.168.1.41 192.168.1.27 …`
   prints the four fingerprints; the new laptop compares what its first `ssh` shows against them.
   `host_key_checking` stays `True`, and no host key is committed — a reflashed Pi regenerates its own.

## What proves each step

| Claim | Proof |
|---|---|
| the layer writes both files from one list | `hack/tests/test_ansible_layer.py`: both tasks carry `exclusive: true`, `key: "{{ trusted_ssh_keys … }}"`, and no `remote_src` copy remains in `automation` |
| an unsafe list is refused | the two `assert` tasks are named in the tests, and a `--check` run with an empty list fails before any write |
| a laptop is really adopted | step 4 above on the new machine, `make check --limit nas` printing no `changed` |
| a key really is retired | a throwaway key added by hand on `nas` is reported as drift by `make check` and removed by `make apply`; this is the round trip the tests cannot run |
| the bootstrap still works | the `README.MD` two-run sequence, run on a host whose `ansible` account does not exist (a reflashed Pi is the case) |
| the Pi path still works | `docs/runbooks/pi-reflash.md` steps 8–9 read as the declared list, not the inheritance |

## Files this design touches

- `ansible/inventory/group_vars/all.yml` — `trusted_ssh_keys`, `human_user`; drop `automation_key_source_user`.
- `ansible/roles/automation/tasks/main.yml` — replace the three key tasks with the assertion and the write.
- `ansible/roles/accounts/tasks/main.yml` — write the human file first; the guard reads the list.
- `ansible/ansible.cfg` — `private_key_file`.
- `ansible/Makefile` — the `adopt` target.
- `ansible/README.MD` — the procedure, and the paragraph that says keys are inherited.
- `hack/tests/test_ansible_layer.py` — one test replaced, three added.
- `docs/runbooks/pi-reflash.md` — step 8's "from the keys `vronteix` already accepts".
- `docs/runbooks/ansible-laptop-onboarding.md` — the procedure, in `mkdocs.yml`'s nav.
- `docs/decisions/2026-11-25-declared-host-access.md` — the record that supersedes the trust sentence
  of 2026-11-24.

## Out of scope

- **The Pi runner** (card #711's literal title). This design is what makes it optional; the decision to
  still want one belongs to the operator, on the board, not here.
- **`homelab_ssh` in `config/keys.json`.** A different repository, its own branch and card: it needs an
  entry in `keys.json`, a matching tuple in `EXPECTED_VALIDATORS` (`setup_lib/keys.py`), and a `doctor`
  check. Until it lands, the runbook says `ssh-keygen -t ed25519 -f ~/.ssh/homelab_live`.
- **Pinning `ansible-core` and a venv.** Real, and the other half of "a new laptop reproduces the
  toolchain": `config/ansible/requirements.yml` pins `community.general` 11.4.9 for core 2.16 while
  `homelab/ansible/Makefile` pins 13.x for 2.18, and nothing pins the core. It is a separate slice so
  this one stays reviewable; it does not gate access.
- **Committing host keys or a `known_hosts`.** A reflashed Pi regenerates its host key, so a committed
  file would need a commit on every reflash. The runbook compares fingerprints instead.
- **SSH CA, hardware keys, per-host key lists.** Revisit when the fleet or the threat changes.

- **The two edge Pis in this pass.** They have no `ansible` account until the reflash, so this change
  reaches four hosts. Their bootstrap arrives through `pi-reflash.md` steps 8–9 when card #481 runs,
  and the design only requires that the runbook's paragraph stops promising inheritance.

## What is left to the builder

- The exact wording of the two assertions' failure messages, and whether the shape check is one
  `assert` with a loop or a `selectattr`/`rejectattr` chain — the repository's tests name task
  behaviour, so whichever is chosen gets a test either way.
- Whether `trusted_ssh_keys` and `human_user` stay in `group_vars/all.yml` beside `automation_user` or
  move to a `group_vars/all/access.yml` of their own. One file is fewer places to look; two keeps the
  key list out of a file edited for other reasons, which makes its history readable.
- Whether the human file is written before or after the `sshd` edits inside `accounts`. Both orders
  work; writing the key first matches the role's own rule that the guard protects a change that has
  not happened yet.
- How the runbook page is titled and split. It is one page of about sixty lines, not a section in the
  README.

## Open questions

1. **Card #711's title says "run from a Pi".** This design makes every laptop equivalent and leaves the
   runner optional. Does #711 get reshaped to this procedure, superseded by a new card, or kept as a
   later convenience? That is a board decision, and it decides whether this work lands under #711 or
   under a card of its own.
2. **One list, or two** (`trusted_ssh_keys` and a narrower `automation_ssh_keys`)? One list is what the
   homelab actually does; two would let a laptop read the drift report without being able to apply.
   That is a real distinction only if a laptop is expected to be less trusted than the others, which
   is not the case today.
