# Adopting a laptop for Ansible

> **In one line.** `trusted_ssh_keys` in `ansible/inventory/group_vars/all.yml` is the list of keys that
> may log in, and the layer writes it as the whole file on both accounts: adding a line adopts a laptop,
> deleting one retires it.
>
> **Use it when** a laptop has to run `ansible/` against the hosts, a laptop is retired, or you want to
> know which keys a host actually accepts · **Verified** 2026-11-25: the commands, the two writes and the
> identity were read from the files they name; no laptop has been adopted by this path yet ·
> **Kind:** how-to
>
> **Status:** pending — written from the roles, `ansible.cfg` and the design of 2026-11-25, not from a
> run. The first adoption makes it `proved`.
>
> **Sources** `ansible/inventory/group_vars/all.yml`, `ansible/Makefile` (the `adopt` target),
> `ansible/ansible.cfg`, `ansible/roles/automation/tasks/main.yml`,
> `ansible/roles/accounts/tasks/main.yml`,
> [`declared host access`](../decisions/2026-11-25-declared-host-access.md)

## In one line

`trusted_ssh_keys` in `ansible/inventory/group_vars/all.yml` is the list of keys that may log in to the
four hosts this layer reaches. One line per laptop. Adding a line adopts a laptop; deleting it retires
the laptop.

The list is the whole declaration, not an addition: `roles/automation` and `roles/accounts` render it
into `ansible`'s and `vronteix`'s `authorized_keys` with `backup: true`, so a key nobody declared
is removed by the next run and `make check` reports it as drift. `make adopt` prints the line for the
laptop it runs on, and never edits the file — a reviewed YAML file keeps its comments.

## Before you start

- **The four hosts this reaches:** `nas`, `nuc0`, `streaming` and `rbp2`. `rbp0` and `rbp1` have no
  `ansible` account at all until they are reflashed, so they are not in this pass: they arrive at their
  own procedure, step 7 of [reflashing an edge Pi](pi-reflash.md).
- **A `homelab` checkout**, with `make init` run once in `ansible/`. The identity is made on the laptop,
  so the collections are not needed to print it, but they are needed for the first run.
- **A `config` checkout** on the laptop: `./setup sync` brings the tooling, and its `keys.json` is where
  this pair will eventually be generated instead of by hand.
- **The identity is `~/.ssh/homelab_live` on every laptop.** `ansible.cfg` names it, so a run offers
  that key and no other. The path is the same everywhere; the key is not. The private half never moves
  between machines, which is why a laptop generates its own pair instead of copying one.
- **No passphrase on that pair.** `make adopt` reads the private half with `ssh-keygen -y -P ''` and
  refuses a file it cannot read, so a passphrase here stops the procedure at step 3.

## Host keys

`host_key_checking` is `True` in `ansible.cfg`, so every laptop accepts each host's key once before
anything runs from it — the drift report included. Get the fingerprints from a laptop that already
works, and keep the output:

```bash
ssh-keyscan -t ed25519 192.168.1.41 192.168.1.27 192.168.1.46 192.168.1.48 | ssh-keygen -lf -
```

Expected: four lines, each an address and a `SHA256:` fingerprint — the form ssh shows in its prompt,
which is why the scan is piped through `ssh-keygen` rather than read raw. On the new laptop, run the
connection once per host and accept what it offers:

```bash
for h in nas nuc0 streaming rbp2; do
  ssh ansible@"$h" true
done
```

Each prints the fingerprint before asking. Compare it against the scan, then accept. The connection then
fails with `Permission denied`, which is the point: the host key is settled and only the key question is
left.

A reflashed Pi regenerates its host key, so the comparison is done again after a reflash rather than
frozen in a file that would then be wrong. No host key is committed.

## What is already trusted

The hosts accepted keys before this list existed, pasted by hand over time. Run the collector from the
laptop that already works, before the first apply, so the diff has something to be read against. It
prints every `authorized_keys` under `/root` and `/home/*` as key type and comment, never key material.
The path to the script is from the repository root:

```bash
scp ansible/collectors/host-audit.sh vronteix@nas:~/
ssh -t vronteix@nas sudo bash
  install -d /tmp/host-audit
  ./host-audit.sh > /tmp/host-audit/$(hostname).md
  exit
scp vronteix@nas:/tmp/host-audit/nas.md .
```

Section 13 of the report is the one to read. `nas` is first because it is the pilot below; one host
shows the shape, and a host whose answer you doubt gets its own run.

**If a third account holds a laptop key, the two-file promise is incomplete on that host.** This layer
owns exactly two files per host, `/home/ansible/.ssh/authorized_keys` and
`/home/vronteix/.ssh/authorized_keys`.
A key in `/root/.ssh/authorized_keys` or in another human account is not declared, not removed, and not
reported by `make check`. Say so in the card and decide: paste it by hand where it belongs, or take it
out by hand there.

## Adopt a laptop

Five steps, in this order.

**1. Bring the laptop's tooling up to date**, in the `config` checkout:

```bash
./setup sync
```

It re-links and reinstalls what the manifest names. Expected: it ends with its summary and no error. A
machine that has never run it needs `./setup` and `./setup keys` first, as that repository's README
says.

**2. Make the pair**, on that laptop:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/homelab_live
```

Accept the empty passphrase. This is the by-hand step until `config`'s `keys.json` carries
`homelab_live`; until then it is done with the same path on every laptop. Expected:
`~/.ssh/homelab_live` and `~/.ssh/homelab_live.pub`, and

```bash
ssh-keygen -lf ~/.ssh/homelab_live.pub
```

prints `256 SHA256:… you@laptop (ED25519)`.

**3. Print the line**, in `ansible/`:

```bash
make adopt
```

It proves the `.pub` matches the private half beside it, prints the fingerprint, and prints the line
`trusted_ssh_keys` wants:

```
  - "ssh-ed25519 AAAAC3Nza… you@laptop"
# fingerprint SHA256:…
# add that line under trusted_ssh_keys in inventory/group_vars/all.yml,
# then: ansible-playbook playbooks/site.yml --check --diff --limit nas
```

It runs with no network and edits nothing. It exits 1, naming the file, when the `.pub` is missing, when
the file is not an OpenSSH public key, when the private half is missing or cannot be read without a
passphrase, or when the two halves disagree.

**4. Paste the line and get it reviewed.** Put it under `trusted_ssh_keys:` in
`ansible/inventory/group_vars/all.yml`, on a branch, and open a pull request. Paste it; do not retype it.
The comment after the key names the laptop, and it is the only record of whose key is whose.

**5. Apply it, one host first — from a laptop whose key is already declared.** The new laptop cannot
apply its own key: nothing accepts it until this run has written it. Confirm the laptop you are on is
declared: `ssh-keygen -lf ~/.ssh/homelab_live.pub` prints a fingerprint whose trailing comment appears in
`trusted_ssh_keys`. `nas` is the pilot: a worker, so a bad run costs a worker's access and never an etcd
member's. Both commands run in `ansible/`.

```bash
ansible-playbook playbooks/site.yml --check --diff --limit nas
ansible-playbook playbooks/site.yml --limit nas
```

The first is the drift report. The two `authorized_keys` tasks are the only ones expected to change on
the first run; every line it prints prefixed with `-` is a key the run would remove. Read them, add
anything still wanted to the list, and run it again before writing. The second run is the write: expect
`failed=0`, and `changed` on the two key tasks only.

Then the same pair of commands for `nuc0`, `streaming` and `rbp2`, one at a time.

**A whole-fleet run is `make check` and `make apply`**, both from `ansible/`, and it is the right command
once `rbp0` and `rbp1` are reflashed. Until then use `--limit cluster`, which is exactly these four
hosts: a run that includes the Pis answers `unreachable` for them and exits non-zero.

```bash
ansible-playbook playbooks/site.yml --check --diff --limit cluster
ansible-playbook playbooks/site.yml --limit cluster
```

**A limit goes to `ansible-playbook` directly**: make has no `--limit`, refuses the argument and exits
before running the play. The commands above are the form.

## Prove it

Once the four applies are done, run this on the **new** laptop:

```bash
for h in nas nuc0 streaming rbp2; do
  ssh -o BatchMode=yes -i ~/.ssh/homelab_live ansible@"$h" true && echo "$h ok" || echo "$h DENIED";
done
```

Expected: four `ok` lines. `-i` offers that key and no other, and `BatchMode=yes` answers the host-key
prompt with no instead of asking — which is why the four hosts are accepted first, in
[host keys](#host-keys) above. This is the same proof `ansible.cfg` gives every run, without Ansible in
the middle.

Before the apply the same loop answers `Permission denied` on all four: the key is written down in git
and not yet on a host, which is the state, not a fault. A `DENIED` *after* the apply is the real
finding — the host did not get the write, or its host key was never accepted.

Then the drift report on the pilot:

```bash
ansible-playbook playbooks/site.yml --check --diff --limit nas
```

Expected: `changed=0`, no diff. A `changed` line under either key task means the file on the host is not
the declaration — a key git does not know, or no file at all — which is drift: the write above did not
run there, or a key was added by hand afterwards.

## Retire a laptop

1. **Delete its line** from `trusted_ssh_keys`, on a branch, and open the pull request. From the merge
   on, git says the key is gone.
2. **Apply it** from a laptop that is still declared, in `ansible/`:
   `ansible-playbook playbooks/site.yml --limit cluster`.
3. **Check each host**: `ansible-playbook playbooks/site.yml --check --diff --limit cluster`. A
   `changed` under either key task is a host that missed the run; apply it again with
   `--limit <host>`.

From the retired laptop the same probe as above answers `Permission denied (publickey)`. Then delete
`~/.ssh/homelab_live` and `~/.ssh/homelab_live.pub` there, and its entry in `config`'s `keys.json` if it
has one.

**A session already open stays open until it ends.** The write removes the key from the file the next
login reads; it does not close a connection that is already authenticated. Ending that session is the
step that makes the retirement immediate.

## What can lock the fleet out, and the five fences

The one edit that takes access away from all four hosts at once is `trusted_ssh_keys` emptied, or a line
pasted in a shape that cannot authenticate — a hand-wrapped key is the common one, because the base64
body does not wrap. The empty case is caught before anything is written (fence 1). The wrapped line is
not, and it produces an `authorized_keys` nobody can log in with, fleet-wide, written by a run that needs
the account it just broke. Five things stand in front of that:

1. **`roles/automation` asserts the list is defined and not empty** before it creates the account. An
   empty variable fails the run before any file is written.
2. **It asserts the shape of every line** against `trusted_ssh_key_pattern`: a known key type, a base64
   body of whole groups, an optional comment. This is a shape check, not proof: it cannot tell a
   hand-wrapped line from a long comment, which is why the line comes from `make adopt` rather than a
   keyboard.
3. **Both writes carry `backup: true`**, so the previous `authorized_keys` sits beside the new one with a
   timestamp in its name. That copy is the way back without the console.
4. **Read the `--diff` before the write.** `--check --diff --limit <host>` prints every key the run would
   remove. Add the ones still wanted to the list first; this is the only step that captures keys that
   only exist on the hosts.
5. **One host first.** `nas` is the pilot, and the three others follow one at a time. A bad list costs
   one worker, not the fleet.

`roles/accounts` has its own guard, for the same reason: it refuses to close password logins when the
declared list holds no key, because the file on disk is no longer evidence once the layer writes it.

## The floor

If a bad list is applied anyway, the run that wrote it is already authenticated and keeps working. So
the damage shows on the *next* connection, not in the middle of the run — keep the session open until
the probe above passes.

The way back in, in the order to try it:

- **An open session on a host**, from which the corrected list can be applied.
- **A screen and a keyboard on `nas` or `nuc0`**, the two hosts that have one.
- **Physical access to a Pi**: its boot partition, or the reflash procedure, is the only remote-free
  path there.
- **The timestamped copy beside `authorized_keys`**, put back by hand, when neither of the above is
  reachable.
