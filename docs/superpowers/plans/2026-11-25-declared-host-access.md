# Declared host access: a laptop adopted by a line in git — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the list of SSH keys that may log in to the six hosts a declared value in `group_vars`, written to both accounts by the layer, so adopting a laptop is one added line and retiring it is one deleted line.

**Architecture:** One list, `trusted_ssh_keys`, is written as the *whole content* of `~vronteix/.ssh/authorized_keys` and `~ansible/.ssh/authorized_keys` by `ansible.builtin.copy` with `backup: true`, so a key not in the list disappears on the next apply and `make check` reports it as drift. `roles/automation` stops reading the human account's file; a `make adopt` target prints the line for the laptop the operator is on but never edits the reviewed YAML. `ansible.cfg` names the identity, so a run no longer depends on whichever default key a laptop happens to own.

**Tech Stack:** Ansible (ansible-core 2.18, roles already present), a POSIX `make` target, and the existing offline test suite in `hack/tests/` (pytest reading YAML and running commands; no cluster, no host).

**Spec:** `docs/superpowers/specs/2026-11-25-declared-host-access-design.md`

## Global Constraints

- Board card #716. Design approved 2026-11-25; card #711 is retired with the Pi premise, and **no runner is built**.
- **Four hosts in this pass**: `nas`, `nuc0`, `streaming`, `rbp2`. `rbp0` and `rbp1` have no `ansible` account until card #481 reflashes them; their path is `docs/runbooks/pi-reflash.md`.
- **One list, no second narrower list.** A key that may log in may also apply.
- YAML in `ansible/` is 2-space, LF. Key order inside a task block is `name`, module, arguments, `tags`.
- Every task in `roles/automation` carries `tags: [automation]`; the role must still be runnable alone as `-e ansible_user=vronteix --tags automation -K`. A test asserts no task is untagged.
- No secret, kubeconfig or `*.pub` file enters the tree. The committed key lines are YAML scalars, and they are public keys.
- `make check` is the drift report: a host that is correct prints no `changed`.
- Every commit message uses an emoji prefix (`🚀`, `✨`, `🔧`, `📝`, `🐛`).
- Tests run offline: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests`.
- Documentation pages live in `mkdocs.yml`'s nav or the checker fails; blockquotes, never `!!!` admonitions.
- Do not run `make apply`, and do not write to any host, in Tasks 1–5. Task 6 is the operator's.

## Review Focus

The five ways this change bites someone, most likely first. Each is pinned by a test in the stated task.

1. **An empty, wrapped or truncated key line takes root away from every host.** The `assert` must run before the account is written, and the shape must be checked, not assumed — Task 2.
2. **`make adopt` prints a line that is not valid YAML**, because the key's comment holds a `"` — Task 1.
3. **A public key with no private half is "adopted"** and the laptop still cannot log in — Task 1.
4. **`private_key_file` names a key a laptop does not have**, removing its access before any diff can be read — Task 4 (test) and Task 6 (per-laptop probe).
5. **The write removes a key the operator still needs**, because the list was captured from one account only — Task 6's diff-first step, and the survey step in the runbook.

---

### Task 1: `make adopt` prints the line, and nothing else

**Files:**
- Modify: `ansible/Makefile`
- Test: `hack/tests/test_ansible_layer.py`

**Interfaces:**
- Produces: `make -C ansible adopt KEY=<path.pub>` — exit 0 and two stdout lines (the YAML entry, then a `# fingerprint SHA256:…` line) on success; exit 1 with a message on stderr otherwise. Never writes a file.

- [ ] **Step 1: Write the failing tests**

Append to `hack/tests/test_ansible_layer.py` (add `import subprocess` at the top, beside `pathlib` and `yaml`):

```python
def make_adopt(key):
    return subprocess.run(
        ["make", "-C", str(ANSIBLE), "adopt", "KEY=" + str(key)],
        capture_output=True, text=True,
    )


def a_key(tmp_path, name="homelab_live", comment="operator@laptop"):
    subprocess.run(
        ["ssh-keygen", "-t", "ed25519", "-N", "", "-C", comment,
         "-f", str(tmp_path / name)],
        check=True, capture_output=True,
    )
    return tmp_path / (name + ".pub")


def test_make_adopt_prints_a_line_that_loads_as_yaml(tmp_path):
    run = make_adopt(a_key(tmp_path))
    assert run.returncode == 0, run.stderr
    entry = next(line for line in run.stdout.splitlines() if line.startswith("  - "))
    assert yaml.safe_load("keys:\n" + entry)["keys"][0].startswith("ssh-ed25519 ")
    assert "fingerprint SHA256:" in run.stdout

def test_make_adopt_never_edits_the_inventory(tmp_path):
    before = (ANSIBLE / "inventory/group_vars/all.yml").read_bytes()
    assert make_adopt(a_key(tmp_path)).returncode == 0
    assert (ANSIBLE / "inventory/group_vars/all.yml").read_bytes() == before

def test_make_adopt_refuses_a_file_that_is_not_a_key(tmp_path):
    junk = tmp_path / "junk.pub"
    junk.write_text("hello\n")
    run = make_adopt(junk)
    assert run.returncode != 0
    assert "not an OpenSSH public key" in run.stderr

def test_make_adopt_refuses_a_public_key_without_its_private_half(tmp_path):
    pub = a_key(tmp_path)
    orphan = tmp_path / "orphan.pub"
    orphan.write_bytes(pub.read_bytes())
    run = make_adopt(orphan)
    assert run.returncode != 0
    assert "proves nothing on its own" in run.stderr

def test_make_adopt_refuses_a_mismatched_pair(tmp_path):
    a_key(tmp_path, "first")
    pub = a_key(tmp_path, "second")
    pub.write_bytes((tmp_path / "first.pub").read_bytes())
    run = make_adopt(pub)
    assert run.returncode != 0
    assert "does not match" in run.stderr

def test_make_adopt_survives_a_comment_that_would_break_yaml(tmp_path):
    run = make_adopt(a_key(tmp_path, comment='a"b@laptop'))
    assert run.returncode == 0, run.stderr
    entry = next(line for line in run.stdout.splitlines() if line.startswith("  - "))
    assert yaml.safe_load("keys:\n" + entry)["keys"][0].endswith("a'b@laptop")
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_ansible_layer.py -k make_adopt`
Expected: all six FAIL — `make: *** No rule to make target 'adopt'`.

- [ ] **Step 3: Add the target to `ansible/Makefile`**

Add `adopt` to the `.PHONY` line, then below `lint` (each recipe line begins with a **tab**):

```make
# Print the line `trusted_ssh_keys` wants for this laptop: the public key, proved
# against the private half beside it, and its fingerprint. It never edits
# group_vars — a reviewed file keeps its comments — and it never touches the
# network, so it runs on a laptop that cannot reach a host yet.
KEY ?= $(HOME)/.ssh/homelab_live.pub
adopt:
	@test -f "$(KEY)" || { echo "adopt: $(KEY) is missing — ssh-keygen -t ed25519 -f $(KEY) creates it" >&2; exit 1; }
	@priv=$${KEY%.pub}; \
	 case "$$(cat "$(KEY)")" in \
	   ssh-ed25519\ *|ssh-rsa\ *|ecdsa-sha2-nistp256\ *|sk-ssh-ed25519@openssh.com\ *) ;; \
	   *) echo "adopt: $(KEY) is not an OpenSSH public key" >&2; exit 1 ;; \
	 esac; \
	 test -f "$$priv" || { echo "adopt: $$priv is missing; the public half proves nothing on its own" >&2; exit 1; }; \
	 test "$$(ssh-keygen -y -P '' -f "$$priv" 2>/dev/null)" = "$$(cut -d' ' -f1,2 "$(KEY)")" \
	   || { echo "adopt: $(KEY) does not match $$priv" >&2; exit 1; }; \
	 printf '  - "%s"\n' "$$(tr '"' "'" < "$(KEY)")"; \
	 printf '# fingerprint %s\n' "$$(ssh-keygen -lf "$(KEY)" | cut -d' ' -f2)"; \
	 printf '# add that line under trusted_ssh_keys in inventory/group_vars/all.yml,\n# then: ansible-playbook playbooks/site.yml --check --diff --limit nas\n'
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_ansible_layer.py -k make_adopt`
Expected: `6 passed`.

- [ ] **Step 5: Commit**

```bash
git add ansible/Makefile hack/tests/test_ansible_layer.py
git commit -m "✨ feat: make adopt prints the declared line for a laptop key"
```

---

### Task 2: the declared list, and the automation account written from it

**Files:**
- Modify: `ansible/inventory/group_vars/all.yml`
- Modify: `ansible/roles/automation/tasks/main.yml`
- Test: `hack/tests/test_ansible_layer.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `trusted_ssh_keys` (list of public-key lines, non-empty), `human_user: vronteix`, `trusted_ssh_key_pattern` (a regex string), all in `inventory/group_vars/all.yml`. Task 3 reads the same three names. `automation_key_source_user` no longer exists anywhere.

**Operator input required.** The real key lines cannot be invented and the agent cannot read `~/.ssh`. Before Step 4's tests pass, the operator pastes the lines they want into `trusted_ssh_keys`, from `cat ~/.ssh/homelab_live.pub` on each laptop and from `ssh vronteix@<host> cat ~/.ssh/authorized_keys` on the four hosts. Until then the non-empty assertion fails, by design.

- [ ] **Step 1: Write the failing tests**

Add `import re` beside the imports, then append after the `make_adopt` tests. Delete `test_the_automation_account_takes_its_key_from_the_human_account` entirely — it asserts the behaviour this task removes. Add alongside the other inventory tests:

```python
def test_the_key_list_is_declared_and_well_formed():
    defaults = load("inventory/group_vars/all.yml")
    keys = defaults["trusted_ssh_keys"]
    assert isinstance(keys, list) and keys, "an empty list locks every host out"
    pattern = defaults["trusted_ssh_key_pattern"]
    malformed = [key for key in keys if not re.match(pattern, key)]
    assert malformed == [], malformed
    assert defaults["human_user"] == "vronteix"

def test_the_list_is_refused_before_an_account_is_written():
    tasks = yaml.safe_load((ANSIBLE / "roles/automation/tasks/main.yml").read_text())
    asserts = [
        task["ansible.builtin.assert"]
        for task in tasks
        if isinstance(task, dict) and "ansible.builtin.assert" in task
    ]
    joined = "\n".join(str(check) for check in asserts)
    assert "trusted_ssh_keys is defined" in joined
    assert "trusted_ssh_keys | length > 0" in joined
    assert "reject('match', trusted_ssh_key_pattern)" in joined
    names = [task["name"] for task in tasks if isinstance(task, dict)]
    assert names.index("Refuse a key list that cannot be declared") < names.index(
        "Create the automation account, key-only"
    )

def test_the_automation_account_is_written_from_the_declared_list():
    tasks = yaml.safe_load((ANSIBLE / "roles/automation/tasks/main.yml").read_text())
    writes = [
        task["ansible.builtin.copy"]
        for task in tasks
        if isinstance(task, dict) and "ansible.builtin.copy" in task
        and str(task["ansible.builtin.copy"].get("dest", "")).endswith("/.ssh/authorized_keys")
    ]
    assert len(writes) == 1, writes
    write = writes[0]
    assert "trusted_ssh_keys | join" in write["content"]
    assert "automation_user" in write["dest"]
    assert write["backup"] is True
    assert write["mode"] == "0600"

def test_no_task_reads_the_human_key_file_any_more():
    for path in yaml_files():
        assert "automation_key_source" not in path.read_text(), path
    role = (ANSIBLE / "roles/automation/tasks/main.yml").read_text()
    assert "remote_src" not in role
    assert "getent_passwd" not in role
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_ansible_layer.py -k "key_list or refused_before or written_from_the_declared or reads_the_human"`
Expected: FAIL — `KeyError: 'trusted_ssh_keys'` and the automation writes missing.

- [ ] **Step 3: Declare the list**

In `ansible/inventory/group_vars/all.yml`, replace the `automation_key_source_user` block (its comment and value) with:

```yaml
# The human account, named once: `roles/accounts` writes the same declared keys
# into it that `roles/automation` writes into the automation account.
human_user: vronteix

# Every key in this list logs in as both accounts, on every host this layer
# reaches. The automation account holds `ansible ALL=(ALL) NOPASSWD: ALL`, so a
# key here is root on the fleet. One line per laptop; the trailing comment is
# whose it is. `make adopt` prints the line for the laptop you are on, and
# deleting a line here is how a laptop is retired.
trusted_ssh_keys:
  - "ssh-ed25519 REPLACE-WITH-REAL-KEY operator@laptop"

# What a key line may look like. A wrapped or truncated paste is the other way to
# lose the fleet: the file stays syntactically valid and holds a key that can
# never authenticate. `roles/automation` refuses a list that does not match.
trusted_ssh_key_pattern: '^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp256|sk-ssh-ed25519@openssh\.com) [A-Za-z0-9+/]+={0,2}( \S+)*$'
```

- [ ] **Step 4: Rewrite the automation role's key handling**

In `ansible/roles/automation/tasks/main.yml`, delete these four tasks and their surrounding comment: `Read the account automation inherits its keys from`, `Note where that account keeps the keys that already log in`, `Read that file before creating an account no key can reach`, `Stop before an account exists that nothing can log in to`, and the task `Give it the keys the human account already accepts`. Replace the file's header comment paragraph about inheritance with a sentence naming the declared list and the 2026-11-25 decision. Then add, **before** `Create the automation account, key-only`:

```yaml
# The list is checked before the account exists, because an account written from
# a broken list cannot be logged in to, and the run that would fix it needs that
# account. Empty and malformed are the two ways to lose the fleet in one edit.
- name: Refuse a key list that cannot be declared
  ansible.builtin.assert:
    that:
      - trusted_ssh_keys is defined
      - trusted_ssh_keys | length > 0
      - trusted_ssh_keys | reject('match', trusted_ssh_key_pattern) | list | length == 0
    fail_msg: >-
      trusted_ssh_keys is empty, or holds a line that is not an OpenSSH public
      key. An account written from that list cannot be logged in to.
  tags: [automation]
```

and, **after** `Give it a home for its keys`:

```yaml
# The whole file, not an appended line: that is what makes a key the operator did
# not declare disappear on the next run, and what `make check` then reports.
# `copy` rather than `authorized_key` because only `copy` backs the old file up.
- name: Give it the keys declared in group_vars
  ansible.builtin.copy:
    content: "{{ trusted_ssh_keys | join('\n') }}\n"
    dest: "/home/{{ automation_user }}/.ssh/authorized_keys"
    owner: "{{ automation_user }}"
    group: "{{ automation_user }}"
    mode: "0600"
    backup: true
  tags: [automation]
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_ansible_layer.py`
Expected: all pass, including `test_the_automation_role_can_be_applied_alone_and_runs_before_the_removal` (every task still tagged `automation`).

If the operator has not pasted a real key yet, `test_the_key_list_is_declared_and_well_formed` fails on the placeholder. Wait for the lines rather than weakening the test.

- [ ] **Step 6: Commit**

```bash
git add ansible/inventory/group_vars/all.yml ansible/roles/automation/tasks/main.yml hack/tests/test_ansible_layer.py
git commit -m "🚀 feat: declare the hosts' SSH keys in one list"
```

---

### Task 3: the human account written from the same list

**Files:**
- Modify: `ansible/roles/accounts/tasks/main.yml`
- Test: `hack/tests/test_ansible_layer.py`

**Interfaces:**
- Consumes: `trusted_ssh_keys`, `human_user` from Task 2.
- Produces: nothing new; `roles/accounts` now writes `~{{ human_user }}/.ssh/authorized_keys` before any removal.

- [ ] **Step 1: Write the failing tests**

```python
def test_the_human_account_is_written_from_the_declared_list():
    tasks = yaml.safe_load((ANSIBLE / "roles/accounts/tasks/main.yml").read_text())
    writes = [
        task["ansible.builtin.copy"]
        for task in tasks
        if isinstance(task, dict) and "ansible.builtin.copy" in task
        and str(task["ansible.builtin.copy"].get("dest", "")).endswith("/.ssh/authorized_keys")
    ]
    assert len(writes) == 1, writes
    write = writes[0]
    assert "trusted_ssh_keys | join" in write["content"]
    assert "human_user" in write["dest"]
    assert write["backup"] is True
    assert write["mode"] == "0600"
    names = [task["name"] for task in tasks if isinstance(task, dict)]
    assert names.index("Give the human account the keys declared in group_vars") < names.index(
        "Remove the passwordless sudo grant on the admins group"
    )

def test_accounts_refuses_to_close_password_logins_without_a_declared_key():
    tasks = yaml.safe_load((ANSIBLE / "roles/accounts/tasks/main.yml").read_text())
    guards = [
        task["ansible.builtin.assert"]
        for task in tasks
        if isinstance(task, dict) and "ansible.builtin.assert" in task
    ]
    joined = "\n".join(str(check) for check in guards)
    assert "sshd_password_auth is defined" in joined
    assert "trusted_ssh_keys | length > 0" in joined
    assert "ansible_user | default('') != 'root'" in joined
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_ansible_layer.py -k "human_account_is_written or without_a_declared_key"`
Expected: FAIL — the writes list is empty and the guard still names `accounts_authorized_keys`.

- [ ] **Step 3: Rewrite the guard, add the write**

In `ansible/roles/accounts/tasks/main.yml`, delete `Read the account this play connects as`, `Look for a key that can replace the password` and `Refuse to close password logins on an account that has no key`, and replace them with:

```yaml
# The guard comes first, for the same reason as before: by the time the file is
# written the change has happened. What has to be true now is that the *declared*
# list holds a key, because this role is what puts it on disk — the on-disk file
# is no longer evidence, since the layer writes it.
- name: Refuse to close password logins without a declared key
  ansible.builtin.assert:
    that:
      - ansible_user | default('') != 'root'
      - sshd_password_auth is defined
      - trusted_ssh_keys is defined
      - trusted_ssh_keys | length > 0
    fail_msg: >-
      This play connects as {{ ansible_user | default('an unknown user') }} and
      trusted_ssh_keys declares no key. The next tasks take the password away,
      and a host with no declared key and no password has no way in.
```

Then, before `Remove the passwordless sudo grant on the admins group`:

```yaml
- name: Give the human account the keys declared in group_vars
  ansible.builtin.copy:
    content: "{{ trusted_ssh_keys | join('\n') }}\n"
    dest: "/home/{{ human_user }}/.ssh/authorized_keys"
    owner: "{{ human_user }}"
    group: "{{ human_user }}"
    mode: "0600"
    backup: true
```

Update the role's header comment: the sentence naming the two keys the Pis accept today, and the paragraph about lockout, now describe the declared list.

- [ ] **Step 4: Run the tests and the linter**

```bash
uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests
make -C ansible lint
```
Expected: pytest passes, `ansible-lint` reports no failure.

- [ ] **Step 5: Commit**

```bash
git add ansible/roles/accounts/tasks/main.yml hack/tests/test_ansible_layer.py
git commit -m "🚀 feat: write the human account's keys from the declared list"
```

---

### Task 4: name the identity, in its own commit

**Files:**
- Modify: `ansible/ansible.cfg`
- Test: `hack/tests/test_ansible_layer.py`

**Interfaces:**
- Produces: `private_key_file = ~/.ssh/homelab_live` as the single place the identity is named. No task, inventory file or group variable may name an identity path again.

This lands after Tasks 2 and 3 so a laptop that cannot reach a host with `homelab_live` still has a working layer until this commit, and it is its own commit so it can be reverted alone.

- [ ] **Step 1: Write the failing tests**

```python
def test_ansible_cfg_names_the_homelab_identity():
    cfg = (ANSIBLE / "ansible.cfg").read_text()
    assert "private_key_file = ~/.ssh/homelab_live" in cfg

def test_the_identity_is_named_in_one_place():
    for path in yaml_files():
        assert "ansible_ssh_private_key_file" not in path.read_text(), path
        assert "private_key_file" not in path.read_text(), path
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_ansible_layer.py -k identity`
Expected: FAIL on the first test.

- [ ] **Step 3: Add the line**

In `ansible/ansible.cfg`, under `[defaults]`, after `interpreter_python`:

```ini
# The identity every run uses, so a laptop stops depending on whichever default
# key it happens to own. Ansible passes this as an ssh `-i`, which replaces the
# default identity list rather than adding to it: a laptop without this file has
# no access, which is why the onboarding runbook proves it before this lands.
private_key_file = ~/.ssh/homelab_live
```

- [ ] **Step 4: Prove it resolves, from this laptop**

```bash
ssh -o BatchMode=yes -i ~/.ssh/homelab_live ansible@nas true && echo reachable
cd ansible && ansible-inventory --host nas | grep -i private_key
```
Expected: `reachable`, and the second command prints the key path or nothing — either is fine, as long as the first succeeded. The probe is the proof: `homelab_live` was accepted as `ansible` on `nas` on 2026-11-25, and this task makes it the only identity offered.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add ansible/ansible.cfg hack/tests/test_ansible_layer.py
git commit -m "🔧 chore: name the homelab identity in ansible.cfg"
```

---

### Task 5: the runbook, the README, the reflash step and the decision record

**Files:**
- Create: `docs/runbooks/ansible-laptop-onboarding.md`
- Modify: `mkdocs.yml` (nav)
- Modify: `ansible/README.MD`
- Modify: `docs/runbooks/pi-reflash.md` (step 8)
- Create: `docs/decisions/2026-11-25-declared-host-access.md`

**Interfaces:**
- Consumes: the `make adopt` contract from Task 1, `trusted_ssh_keys` from Task 2, the identity from Task 4.
- Produces: the operator-facing procedure. No code depends on this task.

- [ ] **Step 1: Write the runbook page**

`docs/runbooks/ansible-laptop-onboarding.md`, with exactly these sections and the commands below in them:

- **In one line** — `trusted_ssh_keys` in `ansible/inventory/group_vars/all.yml` is the list of keys that may log in; adding a line adopts a laptop, deleting one retires it.
- **Before you start** — the four hosts this reaches (`nas`, `nuc0`, `streaming`, `rbp2`); `rbp0`/`rbp1` arrive at their reflash (`pi-reflash.md`).
- **What is already trusted** — `sudo ./collectors/host-audit.sh` on one host prints every `authorized_keys` under `/root` and `/home/*` as key type and comment, never key material. If a third account holds a laptop key, the two-file promise is incomplete and the page says so. Run it before the first apply.
- **Adopt a laptop** — the five commands, in order, with the expected output for each:
  `./setup sync`, `ssh-keygen -t ed25519 -f ~/.ssh/homelab_live` (until `config`'s `keys.json` carries it), `make adopt`, paste the line, then `ansible-playbook playbooks/site.yml --check --diff --limit nas` and `ansible-playbook playbooks/site.yml --limit nas`, then the other three. Say on the page that a whole-fleet run is `make check` / `make apply`, and that a limit goes to `ansible-playbook` directly because make reads `--limit` as one of its own options.
- **Prove it** — `ssh -o BatchMode=yes -i ~/.ssh/homelab_live ansible@nas true` and `ansible-playbook playbooks/site.yml --check --diff --limit nas`, expected no `changed`, because a missing key is drift.
- **Retire a laptop** — delete the line, `make apply`, then `make check` on each host; a `changed` there is a host that missed the run.
- **What can lock the fleet out, and the five fences** — the two assertions, `backup: true`, the diff-first reading of `--diff`, and one host first.
- **The floor** — console on `nas`/`nuc0`, physical access to the Pis; a run already authenticated survives the write that breaks the next one.
- **Host keys** — `ssh-keyscan -t ed25519 192.168.1.41 192.168.1.27 192.168.1.46 192.168.1.48` from the validated laptop, compared against the first `ssh` from the new one.

- [ ] **Step 2: Add it to the nav**

In `mkdocs.yml`, under the `Running it:` list, add `- Adopting a laptop for Ansible: runbooks/ansible-laptop-onboarding.md` directly after the line `- Moving Ubuntu to a new LTS: runbooks/ubuntu-upgrade.md`, so the host procedures sit together.

- [ ] **Step 3: Rewrite the README's key paragraph**

In `ansible/README.MD`, in "What it deliberately does not do" → "No secrets", replace the sentence claiming the Pis' secrets are the only ones, and in the automation-account section replace the paragraph that says keys are inherited with:

> `roles/automation` and `roles/accounts` write the same declared list,
> `trusted_ssh_keys`, into `ansible`'s and `vronteix`'s `authorized_keys`: one line per laptop, and
> a key that is not in the list is removed by the next run. `make adopt` prints the line for the
> machine you are on. The procedure is
> [`docs/runbooks/ansible-laptop-onboarding.md`](../docs/runbooks/ansible-laptop-onboarding.md).

Two repairs the task review found in the same file, both one sentence:

- In the usage block near the top (`make init` / `make check` / `make apply` / `make lint`), add the line `make adopt  # print this laptop's key line for trusted_ssh_keys`, so the command the design names is documented where the other targets are.
- At "pass a limit" (about line 25), say that the limit goes to `ansible-playbook` directly, because make reads `--limit` as one of its own options and exits; the two example commands below it are already the right form.

Keep the two-run bootstrap section unchanged, including `-e ansible_user=vronteix` and `--tags automation` — a test asserts both are present.

- [ ] **Step 4: Fix the reflash step**

In `docs/runbooks/pi-reflash.md` step 8, replace "the first creates the `ansible` account from the keys `vronteix` already accepts" with "the first creates the `ansible` account holding the keys `trusted_ssh_keys` declares — the same list `roles/accounts` writes into `vronteix`, so a reflashed Pi is adopted by the line that is already there". Read the whole step afterwards; no other sentence may promise inheritance.

- [ ] **Step 5: Write the decision record**

`docs/decisions/2026-11-25-declared-host-access.md`, in the shape of `docs/decisions/2026-11-24-automation-sudo-account.md` (banner blockquote, then `Date:`, `Decided by: operator`, `Status: active`, then `## The decision`, `## Why`, `## What this supersedes`):

- The list is declared in `group_vars`, written to both accounts as the whole file, and a key not in it is removed.
- The trade: a laptop is root on the fleet; a public key is committed to a public repository (it identifies, it does not authenticate alone); `${HOME}/.ssh` on the controller stays closed to an agent because the operator pastes, not the tool.
- **What this supersedes:** the sentence in `2026-11-24-automation-sudo-account.md` that says the automation account "trusts nothing that was not already trusted" because it copies the human file. That copy is gone; the trust is now reviewed in git. Link the older page from this one and add one line to the older page pointing forward, saying it was superseded in part.
- Why: the operator runs the layer from more than one laptop, and revocation was the half that had no record.

- [ ] **Step 6: Run the documentation checks**

```bash
hack/check-docs.sh --base origin/develop
make -C ansible lint
```
Expected: `check-docs` passes its nav, link and citation checks; the only non-fatal output is the staleness report. If `origin/develop` cannot be resolved, fetch first: `git fetch origin`.

- [ ] **Step 7: Commit**

```bash
git add docs/runbooks/ansible-laptop-onboarding.md mkdocs.yml ansible/README.MD docs/runbooks/pi-reflash.md docs/decisions/2026-11-25-declared-host-access.md
git commit -m "📝 docs: adopting a laptop is a line in group_vars"
```

---

### Task 6: the operator applies it, one host at a time

**Files:** none — this task changes hosts, not the tree. Do not run it under a subagent.

**Interfaces:**
- Consumes: everything above, merged onto the working branch.

- [ ] **Step 1: Read the diff on the pilot**

```bash
cd ansible
ansible-playbook playbooks/site.yml --check --diff --limit nas
```
Before reading it, run `collectors/host-audit.sh` on `nas` the way its README describes (copy it to the host, `sudo ./host-audit.sh`): it prints every `authorized_keys` under `/root` and `/home/*` as type and comment. Read every key the diff offers to remove. Anything still wanted goes into `trusted_ssh_keys` before the next step — including a key the audit found in an account other than `vronteix` or `ansible`, which the layer does not write. Expected: the two accounts' `authorized_keys` replaced, nothing else changed.

- [ ] **Step 2: Apply to the pilot, then the rest**

```bash
ansible-playbook playbooks/site.yml --limit nas
ansible-playbook playbooks/site.yml --check --diff --limit nas   # expected: no `changed`
ansible-playbook playbooks/site.yml --limit nuc0
ansible-playbook playbooks/site.yml --limit streaming
ansible-playbook playbooks/site.yml --limit rbp2
make check                      # expected: no `changed` on the four
```

- [ ] **Step 3: Prove retirement**

Add a throwaway key by hand on `nas`, as `ansible` with its passwordless sudo:

```bash
ssh ansible@nas "sudo sh -c 'echo \"ssh-ed25519 AAAA… throwaway\" >> /home/vronteix/.ssh/authorized_keys'"
```
then `ansible-playbook playbooks/site.yml --check --diff --limit nas` — expected: `changed`, naming the file. `ansible-playbook playbooks/site.yml --limit nas`, then the check again — expected: no `changed`.

- [ ] **Step 4: Record what happened**

Move card #716 to `Validation` with the branch, the pull request and the four commands above and their output. A card whose premise is gone is trashed, not validated — that is not this case.
