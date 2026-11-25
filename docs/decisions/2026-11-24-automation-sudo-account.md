# Passwordless sudo belongs to one named account, not a group

> **In one line.** `%admins ALL=(ALL) NOPASSWD: ALL` is gone; a single account, `ansible`, holds the
> grant unattended runs need, and the human account keeps `%sudo`, which asks for a password.
>
> **Use it when** a playbook run answers `Missing sudo password`, you add a host to
> `ansible/inventory/hosts.yml`, or you wonder who `ansible` is and whether it can be trusted ·
> **Verified** 2026-11-24 · **Kind:** record
>
> **Sources** `ansible/roles/automation/tasks/main.yml`, `ansible/roles/accounts/tasks/main.yml`, the
> apply of 2026-11-24 that failed on `nuc0` with `ok=6 changed=2 failed=1`

Date: 2026-11-24
Decided by: operator
Status: active
Superseded in part: [declared host access](2026-11-25-declared-host-access.md) — the copy of the human
account's keys is gone; the account and its grant stand.

## The decision

The host layer removes the group grant `%admins ALL=(ALL) NOPASSWD: ALL` and replaces it with one
account's: `ansible`, key-only, in `/etc/sudoers.d/00-homelab-automation`, mode `0440`, validated by
`visudo -cf`. The inventory connects as that account. `vronteix` stays in `%sudo` — the stock
`ALL=(ALL:ALL) ALL`, a password, typed by a person.

`ansible` accepts the keys `vronteix` already accepted: the role copies `authorized_keys` on the host.
No key is read on the controller, whose `~/.ssh` is off limits to an agent, and no `*.pub` enters the
tree. The account therefore trusts nothing that was not already trusted.

The order is the safety, and it is two runs: `--tags automation` as the human creates the account
while the grant is still in force, then a run as the account removes it. `accounts` only removes the
grant when `ansible_user == automation_user`, so the run that takes the human path away is never the
run that needs it.

## Why

The first apply of this layer, on `nuc0` on 2026-11-24, removed the grant and then died one task later
with `Missing sudo password`. `sudo -n -l` before that run showed the group grant was `vronteix`'s only
passwordless path on all four cluster nodes, so the failure was not a host quirk: no host could
complete the run, and afterwards `make check` — the drift report the layer exists to produce — would
need a password typed at a terminal on every invocation.

Three options were put to the operator:

- **Keep the grant on `vronteix`.** Smallest change, but the practical exposure is what it was: a
  passwordless root for the account whose key is on disk already.
- **Remove every passwordless grant.** What the plan said. It makes the drift report, and therefore
  the whole layer, an interactive operation: no agent can converge a host or report its drift, and
  the Pis are reflashed rather than configured, so the human would repeat it by hand after each one.
- **One named automation account.** Chosen: the drift report stays runnable unattended, and the sudo
  log says `ansible` where it used to say `vronteix` a second time.

## What it costs if it is wrong

The key that opens `ansible` is a root key with no password in front of it, and it is the same key
that already opened `vronteix` on a host where sudo needed no password either. So this record does not
reduce what a stolen operator key is worth; it reduces what a stolen *password* is worth, and it makes
the two identities tell themselves apart in the log.

If the key leaks, the repair is one file and one account: delete `/etc/sudoers.d/00-homelab-automation`
and the account, which leaves `vronteix`'s password-protected `%sudo` as the way back in — from the
console if the human key is the leaked one. Revoking a key means editing both `authorized_keys` files
on the host, because the role copies rather than shares them; the run that follows puts back whatever
the human account holds.
