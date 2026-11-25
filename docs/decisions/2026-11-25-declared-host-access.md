# Access to the hosts is declared in git, not copied between accounts

> **In one line.** `trusted_ssh_keys` in `ansible/inventory/group_vars/all.yml` is the whole list of keys
> that may log in as `ansible` and as `vronteix` on the four hosts; the layer writes it as the whole file,
> so a key nobody declared is removed by the next run.
>
> **Use it when** you add or retire a laptop, or you wonder what a key inside this repository is worth ·
> **Verified** 2026-11-25 · **Kind:** record
>
> **Sources** `ansible/inventory/group_vars/all.yml`, `ansible/roles/automation/tasks/main.yml`,
> `ansible/roles/accounts/tasks/main.yml`, `ansible/ansible.cfg`, `ansible/Makefile`,
> [`the design`](../superpowers/specs/2026-11-25-declared-host-access-design.md)

Date: 2026-11-25
Decided by: operator
Status: active

## The decision

The keys that may log in are a declared list, `trusted_ssh_keys`, in `ansible/inventory/group_vars/all.yml`.
`roles/automation` and `roles/accounts` render that list as the *whole content* of `ansible`'s and
`vronteix`'s `authorized_keys` on every host the layer reaches, by `ansible.builtin.copy` with
`backup: true` and mode `0600`. The file is exactly the declaration, so a key that is not in the list is
removed by the next run, and `make check` — the drift report the layer exists to produce — names the
host that still holds it.

`make adopt` prints the line for the laptop the operator is on: the public key, proved against the
private half beside it, and its fingerprint. It never edits `group_vars`, because a reviewed YAML file
keeps its comments, and it never touches the network: a laptop that cannot reach a host yet can still
print its own key. The operator pastes the line on a branch and opens a pull request.

`ansible.cfg` names `~/.ssh/homelab_live` as `private_key_file`, so a run offers that identity and no
other instead of whichever default key a laptop happens to own. The path is the same on every machine;
two laptops are two lines with two fingerprints.

### The trade, stated once

- **A key in this list is root on the fleet.** The automation account holds a passwordless sudo grant
  fleet-wide, and the human account is in `%sudo`. A laptop in this list is one of the most trusted
  objects in the homelab, and the variable says so in its own comment.
- **A public key is committed to a public repository.** A public half is not a secret: it names a laptop
  and cannot log in without the private half, which never leaves the machine that made it. What leaks is
  the existence of the laptops, which the inventory already shows. The rule that no `*.pub` **file**
  enters the tree is unchanged and still enforced by `.gitignore`; this change puts a key line in a
  reviewed YAML file, which that rule never covered. Whether the rule should be reworded is a decision of
  its own, not this one.
- **`${HOME}/.ssh` on the controller stays closed to an agent.** No task reads a key file on the machine
  running the play; the operator pastes the line `make adopt` prints, the same way a token is pasted
  into `./setup keys`.
- **Revocation has a window.** Removing the line stops the next login after the job that removes it
  runs. A session already open stays open until it ends.

The procedure is [adopting a laptop for Ansible](../runbooks/ansible-laptop-onboarding.md).

## Why

The operator runs this layer from more than one laptop, and the layer only became runnable on
2026-11-24. That is what exposed the gap: a key was adopted by copying `~/.ssh/homelab_live` onto a
machine, and retired by deleting a line on six hosts from memory. Revocation was the half with no
record — nothing in git said which keys were meant to work, so `make check` could not report one that
was not, and a key nobody remembered stayed valid.

The code that was replaced made this visible: `roles/automation` read `vronteix`'s `authorized_keys` on
the host and copied it onto `ansible`, so the automation account trusted whatever the human account
happened to hold. Editing that file was the only way to change access, and it left no diff to review.

Three alternatives were put beside it, and rejected:

- **Append instead of declare** — `make adopt` writes the host and nothing records it. One command and
  no variable, but a key nobody revoked stays invisible: `make check` cannot report it, and the hosts
  become the only record of who has access.
- **A runner on `rbp0`** — dropped with card #711 on 2026-11-25, not deferred. The work is
  deliberately manual, and a second machine is a machine to patch and a key to protect.
- **An SSH certificate authority** — six hosts and three laptops do not justify one; the CA's private
  key becomes a single object every laptop must hold in order to sign. Revisit if hosts ever number in
  the dozens.

The alternatives, the probes that decided the scope, and the five fences the runbook repeats are in
[the design](../superpowers/specs/2026-11-25-declared-host-access-design.md).

## What this supersedes

One sentence of [the automation-account record](2026-11-24-automation-sudo-account.md): the automation
account "trusts nothing that was not already trusted" because it copies the human account's file. That
copy is gone — `roles/automation` no longer reads another account's `authorized_keys` — and the trust
is now reviewed in git instead of inherited from a file on the host. The rule that no `*.pub` file
enters the tree stands as written: this record puts key lines in YAML, not key files.

What stands from that record, unchanged: the passwordless grant belongs to one named account rather
than a group, the two-run order that creates the account before the human grant is removed, and the
password on the human account's `%sudo`.
