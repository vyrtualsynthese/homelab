# Hosts: stabilise and make upgrade-ready — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every one of the six machines ends clean, known-good and ready for a release upgrade, with its host configuration declared in git so a rebuild is a command rather than a memory.

**Architecture:** A thin Ansible layer replaces the two broken playbooks: one inventory, three groups (`servers`, `cluster`, `edge`), one role per concern, and `site.yml` as the only entry point. `--check` is the drift contract. The two edge Pis get one bash script with four verbs because they are reflashed, not patched, and Ansible is not installed on a fresh image. Host mutations that are not declarative — the NFS export rewrite, the kernel and package trims — are their own tasks with a recorded before/after, because a role that pretends to own them would lie on the next run.

**Tech Stack:** Ansible core 2.18 with `ansible.posix` and `community.general`, `ansible-lint` 26.11, bash for the Pi script, pytest for the checks that run without a host, `ansible/collectors/host-audit.sh` for the evidence.

**Spec:** [`docs/superpowers/specs/2026-11-24-hosts-stabilise-and-ubuntu-upgrade-design.md`](../specs/2026-11-24-hosts-stabilise-and-ubuntu-upgrade-design.md)

## Global Constraints

- **Ubuntu 26.04.1 LTS is the destination; nothing in this plan changes a release.** A `do-release-upgrade` in any task is a failure of that task.
- **No task writes to a host from this session.** Every apply is run by the operator; every task's verification names the command they run and the output that means it passed.
- **One host at a time, never two.** Before any apply: `host-audit.sh` run on that host, and that node's backup job green in the last 24 h.
- **`--check` green before every apply.** A play that reports drift on a host it has never managed is expected; a play that reports drift after it has is a bug in the play.
- **Idempotence is the acceptance test:** a second apply reports `changed=0`.
- **No secret value in the repository.** `pihole.env`, `wg-easy.env` and `.env` on the Pis are inputs the script requires and never writes.
- **No `NOPASSWD: ALL` anywhere.** `%admins` is removed by Task 2; nothing re-adds it.
- **YAML: 2 spaces, LF, lowercase keys.** CRLF applies only under `cluster/argocd/`, which this plan does not touch.
- **Commit prefixes:** `🚀 feature:`, `🔧 chore:`, `🐛 fix:`.
- **Node addresses:** `nas` 192.168.1.41, `nuc0` 192.168.1.27, `streaming` 192.168.1.46, `rbp2` 192.168.1.48, `rbp0` 192.168.1.22, `rbp1` 192.168.1.38.

## Review Focus

The spec says what the hosts must end up as. These are the conditions it does not describe, most likely to bite first; each has a test in the task that owns the code.

1. **Enabling `ufw` on a K3s node while etcd peers talk on 2379/2380.** A missing rule is quorum loss, not a refused connection. Task 5's firewall variable test pins all six required ports per group, and the task applies to one node before the rest.
2. **`apt autoremove --purge` removing the module set the running kernel boots with.** On `nas` the media disk and on `streaming` the GPU module live in `linux-modules-extra-*`. Task 3 pins the running kernel and keeps two.
3. **An NFS client that mounts `/mnt` and exists nowhere in git** — the operator's workstation. Narrowing the export gives it `EIO`, not a clean refusal. Task 6's test pins the eight lines and the absence of `/mnt`; the client set itself is recorded by the operator with `showmount -a` in that task's verification, because no file in this repository names it.
4. **A Pi whose USB disk is not `sda` after a reflash.** Task 8's script reads the root device from `/proc/mounts`, and a test proves it does.
5. **`--check` run while `apt` holds its lock**, reporting a false drift on every host. Task 1 sets `lock_timeout` and a test asserts it.

---

### Task 1: The layer's skeleton and its drift contract

**Files:**
- Create: `ansible/ansible.cfg`
- Create: `ansible/inventory/hosts.yml`
- Create: `ansible/group_vars/all.yml`
- Create: `ansible/group_vars/cluster.yml`
- Create: `ansible/group_vars/edge.yml`
- Create: `ansible/group_vars/servers.yml`
- Create: `ansible/playbooks/site.yml`
- Create: `ansible/roles/baseline/{tasks,meta}/main.yml`
- Delete: `ansible/playbooks/setup-node.yaml`
- Delete: `ansible/playbooks/upgrade-all.yaml`
- Modify: `ansible/Makefile`
- Test: `hack/tests/test_ansible_layer.py`

**Interfaces:**
- Produces: the inventory group names `cluster` (all four nodes), `servers` (the three control-plane nodes), `edge` (the two Pis), and the variable `timezone` in `group_vars/all.yml`. Every later task adds a role under `ansible/roles/<name>/` and an entry in `site.yml`; it never adds a new inventory file.

- [ ] **Step 1: Write the failing test**

```python
# hack/tests/test_ansible_layer.py
import pathlib
import yaml

REPO = pathlib.Path(__file__).resolve().parents[2]
ANSIBLE = REPO / "ansible"


def load(path):
    return yaml.safe_load((ANSIBLE / path).read_text())


def test_inventory_has_six_hosts_in_three_groups():
    inv = load("inventory/hosts.yml")["all"]["children"]
    assert sorted(inv["cluster"]["hosts"]) == ["nas", "nuc0", "rbp2", "streaming"]
    assert sorted(inv["servers"]["hosts"]) == ["nuc0", "rbp2", "streaming"]
    assert sorted(inv["edge"]["hosts"]) == ["rbp0", "rbp1"]


def test_every_host_has_an_address_from_the_record():
    inv = load("inventory/hosts.yml")["all"]["children"]
    seen = {
        host: spec["ansible_host"]
        for group in inv.values()
        for host, spec in group["hosts"].items()
    }
    assert seen == {
        "nas": "192.168.1.41",
        "nuc0": "192.168.1.27",
        "streaming": "192.168.1.46",
        "rbp2": "192.168.1.48",
        "rbp0": "192.168.1.22",
        "rbp1": "192.168.1.38",
    }


def test_site_lists_every_role_that_exists():
    site = (ANSIBLE / "playbooks/site.yml").read_text()
    roles = sorted(p.name for p in (ANSIBLE / "roles").iterdir() if p.is_dir())
    assert roles, "no roles yet"
    for role in roles:
        assert f"- {role}" in site, f"{role} is not reached from site.yml"


def test_every_role_declares_its_metadata():
    for role in (ANSIBLE / "roles").iterdir():
        if role.is_dir():
            meta = yaml.safe_load((role / "meta/main.yml").read_text())
            assert meta["galaxy_info"]["role_name"] == role.name


def test_lock_timeout_is_set_and_short():
    cfg = (ANSIBLE / "ansible.cfg").read_text()
    assert "lock_timeout" in cfg
    assert "inventory = inventory/hosts.yml" in cfg


def test_no_playbook_grants_passwordless_root():
    for path in ANSIBLE.rglob("*.yml"):
        text = path.read_text()
        if "NOPASSWD: ALL" in text:
            assert "state: absent" in text, f"{path} writes passwordless root"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest -q hack/tests/test_ansible_layer.py`
Expected: FAIL — `FileNotFoundError` on `ansible/inventory/hosts.yml`.

- [ ] **Step 3: Write the skeleton**

`ansible/ansible.cfg`:

```ini
[defaults]
inventory = inventory/hosts.yml
roles_path = roles
host_key_checking = True
retry_files_enabled = False
interpreter_python = auto_silent
lock_timeout = 30
stdout_callback = default
[ssh_connection]
pipelining = True
```

`ansible/inventory/hosts.yml`: three groups under `all.children`, each host carrying `ansible_host` and `ansible_user: vronteix`. `edge` additionally carries `ansible_python_interpreter: /usr/bin/python3`.

`ansible/group_vars/all.yml`: `timezone: Europe/Paris`, `kernel_keep: 2`, `ansible_python_interpreter: /usr/bin/python3`.

`ansible/group_vars/servers.yml`: `k3s_role: server`. `group_vars/cluster.yml`: `k3s_role: agent` for `nas` — express as `nas` overriding in `host_vars/nas.yml` instead, so the group stays honest. `group_vars/edge.yml`: `stack_dir: /home/vronteix/dockers`.

`ansible/playbooks/site.yml`: one play, `hosts: all`, `become: true`, and a block-style role list — `roles:` then `- baseline` on its own line, because the test matches the rendered name and a flow list (`[baseline]`) would not match.

**Delete `playbooks/setup-node.yaml` and `playbooks/upgrade-all.yaml` in this task.** The first is what wrote the `%admins` grant, the second upgrades six hosts at once with no ordering; `test_no_playbook_grants_passwordless_root` fails while `setup-node.yaml` exists, which is the point of writing that test before the deletion.

`ansible/roles/baseline/tasks/main.yml`: one task that writes `/etc/profile.d/99-homelab.sh` containing `export HOMELAB_MANAGED=1`, with `mode: "0644"`. It exists only so `site.yml` has something to run in this task.

`ansible/roles/baseline/meta/main.yml`: `galaxy_info: {role_name: baseline, author: operator, description: baseline host marker, license: MIT, min_ansible_version: "2.18"}`.

`ansible/Makefile`: keep `init` (the collection installs), replace the two playbook targets with three:

```make
init:
	ansible-galaxy collection install ansible.posix community.general -f
check:
	ansible-playbook playbooks/site.yml --check --diff
apply:
	ansible-playbook playbooks/site.yml
lint:
	ansible-lint
```

There is no `audit` target: the collector is run per host by whoever is holding the SSH agent, and a loop over the inventory inside a Makefile cannot know which host it is on. Say so in the commit message.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest -q hack/tests/test_ansible_layer.py`
Expected: PASS, 6 tests.

- [ ] **Step 5: Prove the play parses without touching a host**

Run: `cd ansible && ansible-playbook playbooks/site.yml --syntax-check && ansible-lint roles/baseline`
Expected: `playbook: playbooks/site.yml` then no lint failure.

- [ ] **Step 6: Commit**

```bash
git add ansible/ansible.cfg ansible/inventory ansible/group_vars ansible/playbooks/site.yml ansible/roles/baseline ansible/Makefile hack/tests/test_ansible_layer.py
git rm -q ansible/playbooks/setup-node.yaml ansible/playbooks/upgrade-all.yaml
git commit -m "🚀 feature: an inventory and one entry point for the six hosts"
```

---

### Task 2: The `accounts` role — remove the group grant, close password SSH

**Files:**
- Create: `ansible/roles/accounts/tasks/main.yml`
- Create: `ansible/roles/accounts/defaults/main.yml`
- Create: `ansible/roles/accounts/handlers/main.yml`
- Create: `ansible/roles/accounts/meta/main.yml`
- Create: `ansible/group_vars/all.yml` (modify — add `sshd_password_auth: false`)
- Modify: `ansible/playbooks/site.yml`
- Test: `hack/tests/test_ansible_layer.py` (modify)

**Interfaces:**
- Consumes: the inventory groups from Task 1.
- Produces: the variable `sshd_password_auth` (bool, default `false`) and the handler `reload sshd`. Task 5's firewall role assumes sshd listens on 22 and nothing else.

- [ ] **Step 1: Write the failing test**

Add to `hack/tests/test_ansible_layer.py`:

```python
def test_accounts_removes_the_admins_grant_and_keeps_sudo():
    text = (ANSIBLE / "roles/accounts/tasks/main.yml").read_text()
    assert "path: /etc/sudoers" in text
    assert "line: '%admins ALL=(ALL) NOPASSWD: ALL'" in text
    assert "state: absent" in text
    assert "line: '%sudo" not in text, "the stock %sudo line must not be rewritten"


def test_accounts_closes_password_auth_for_every_host():
    defaults = yaml.safe_load((ANSIBLE / "group_vars/all.yml").read_text())
    assert defaults["sshd_password_auth"] is False
    text = (ANSIBLE / "roles/accounts/tasks/main.yml").read_text()
    assert "PasswordAuthentication" in text
    assert "X11Forwarding" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest -q hack/tests/test_ansible_layer.py -k accounts`
Expected: FAIL — the role directory does not exist.

- [ ] **Step 3: Write the role**

`tasks/main.yml`, four tasks in this order:

1. `ansible.builtin.lineinfile` — `path: /etc/sudoers`, `line: '%admins ALL=(ALL) NOPASSWD: ALL'`, `state: absent`, `validate: 'visudo -cf %s'`.
2. `ansible.builtin.group` — `name: admins`, `state: absent`. Put this **after** task 1 so a partial run never leaves a member without its group.
3. `ansible.builtin.lineinfile` — `path: /etc/ssh/sshd_config`, two separate tasks for `PasswordAuthentication {{ sshd_password_auth | ternary('yes','no') }}` and `X11Forwarding no`, each with `validate: 'sshd -t -f %s'` and `notify: reload sshd`.
4. `ansible.builtin.assert` that `ansible_user` is not `root` and that `sshd_password_auth` is defined, so a typo in `group_vars` fails the play rather than skipping the change.

`handlers/main.yml`: `ansible.builtin.systemd` — `name: ssh`, `state: reloaded`, plus `ansible.builtin.service` fallback for `sshd`.

**Ordering warning to keep in the file as a comment:** the Pis are the only hosts with `PasswordAuthentication yes`, and their keys are the two in `authorized_keys` today (`vyrtualsynthese@DESKTOP-I3PJEO5`, `valentin.ronteix@ashudev.com`). Applying this role to `edge` before confirming both keys are present locks the operator out of a machine that needs a ladder to fix.

- [ ] **Step 4: Prove it before applying**

Run: `cd ansible && ansible-playbook playbooks/site.yml --check --limit rbp1`
Expected: `changed` on the three sudoers/sshd tasks, and `%admins` present in the diff.

Then run the same with `--diff`, read every diff line, and only then apply to `rbp1` alone. Never `--limit edge` on the first run.

- [ ] **Step 5: Commit**

```bash
git add ansible/roles/accounts ansible/group_vars/all.yml ansible/playbooks/site.yml hack/tests/test_ansible_layer.py
git commit -m "🚀 feature: take back the passwordless root grant and close password SSH"
```

---

### Task 3: The `system` role — time zone, and a kernel set that stays small

**Files:**
- Create: `ansible/roles/system/{tasks,defaults,meta}/main.yml`
- Modify: `ansible/playbooks/site.yml`
- Test: `hack/tests/test_ansible_layer.py` (modify)

**Interfaces:**
- Consumes: `timezone` and `kernel_keep` from `group_vars/all.yml` (Task 1).
- Produces: nothing other tasks import, but it must run **before** `packages` so the APT autoremove in Task 4 sees the narrowed kernel set.

- [ ] **Step 1: Write the failing test**

```python
def test_system_pins_the_running_kernel_before_any_autoremove():
    text = (ANSIBLE / "roles/system/tasks/main.yml").read_text()
    order = [text.index(needle) for needle in
             ("uname -r", "apt-mark", "autoremove")]
    assert order == sorted(order), "autoremove must come after the running kernel is pinned"


def test_system_keeps_two_kernels_and_sets_one_timezone():
    defaults = yaml.safe_load((ANSIBLE / "group_vars/all.yml").read_text())
    assert defaults["kernel_keep"] == 2
    assert defaults["timezone"] == "Europe/Paris"
    tasks = (ANSIBLE / "roles/system/tasks/main.yml").read_text()
    assert "timezone" in tasks and "Europe/Paris" in tasks
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest -q hack/tests/test_ansible_layer.py -k system`
Expected: FAIL.

- [ ] **Step 3: Write the role**

`tasks/main.yml`, in this order:

1. `community.general.timezone` — `name: "{{ timezone }}"`, `hwclock: UTC`.
2. `ansible.builtin.command: uname -r` with `register: running_kernel` and `changed_when: false`.
3. `ansible.builtin.shell` that lists installed kernels, sorts by version, keeps the running one plus the newest `kernel_keep - 1` others, and `apt-mark auto` everything else. Guard with `changed_when: false` and let the next task do the removal, so the two are separately visible in `--check`.
4. `ansible.builtin.apt` — `autoremove: true`, `purge: true`, `update_cache: false`.
5. `ansible.builtin.command: dpkg --list` filtered to `^rc` then `dpkg --purge` — the config-file residue the audit found on every host. Idempotent because the second run finds nothing.

The removal must never name a package directly. `apt-mark auto` then `apt autoremove` is the only form that cannot remove the running kernel.

- [ ] **Step 4: Prove it on the smallest host first**

Run: `cd ansible && ansible-playbook playbooks/site.yml --check --diff --limit rbp2 --tags system`
Expected: the diff lists the kernels to be removed and names none of `linux-image-5.15.0-1105-raspi` nor `linux-modules-extra-5.15.0-1105-raspi` (the running pair).

If the diff names either, stop: the sort is wrong.

- [ ] **Step 5: Commit**

```bash
git add ansible/roles/system ansible/playbooks/site.yml hack/tests/test_ansible_layer.py
git commit -m "🚀 feature: one time zone and a kernel set that stops growing"
```

---

### Task 4: The `packages` role — the removals, one list per host

**Files:**
- Create: `ansible/roles/packages/{tasks,defaults,meta}/main.yml`
- Create: `ansible/host_vars/nas.yml`
- Create: `ansible/host_vars/streaming.yml`
- Create: `ansible/host_vars/rbp2.yml`
- Modify: `ansible/playbooks/site.yml`
- Test: `hack/tests/test_ansible_layer.py` (modify)

**Interfaces:**
- Consumes: `kernel_keep` (Task 3).
- Produces: the per-host variable `packages_absent` (list of package names), which Task 4 alone consumes. Every later role that needs a package present adds it to `packages_present` here rather than to its own task.

- [ ] **Step 1: Write the failing test**

```python
def test_packages_absent_is_per_host_and_never_touches_the_driver():
    nas = yaml.safe_load((ANSIBLE / "host_vars/nas.yml").read_text())
    streaming = yaml.safe_load((ANSIBLE / "host_vars/streaming.yml").read_text())
    rbp2 = yaml.safe_load((ANSIBLE / "host_vars/rbp2.yml").read_text())
    assert "intel-i915-dkms" in nas["packages_absent"]
    assert any(p.startswith("cuda-") and "12-4" in p for p in streaming["packages_absent"])
    assert all("nvidia" not in p for p in streaming["packages_absent"])
    assert all("linux-image" not in p for p in rbp2["packages_absent"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest -q hack/tests/test_ansible_layer.py -k packages`
Expected: FAIL.

- [ ] **Step 3: Write the role and the three host files**

`nas.yml`: `packages_absent: [intel-i915-dkms]`, plus the note that its DKMS registration for `intel-platform-cse-dkms` is left registered and must be removed with `dkms remove --all` in the same task, because the package is not what owns it.

`streaming.yml`: `packages_absent` covers the stale CUDA toolkits only — `cuda-*-12-4`, `12-5`, `12-6`, `12-8`, `13-0`, `13-1` — by metapackage name, plus `gedit`. `cuda-13-2` stays: one version, the one a future build would use. **The NVIDIA driver, `nvidia-srv`, and `nvidia-container-toolkit` are never in this list.**

`rbp2.yml`: `packages_absent: []` with a comment that its fifty old `linux-modules-extra-*raspi` are handled by Task 3's kernel retention, not by a package list — naming them would remove the running one on the next boot.

`tasks/main.yml`: `community.general.package` with `state: absent` over `packages_absent`, `autoremove: false` (the kernel task owns that), preceded by an `apt-get clean` task that reclaims `streaming`'s 17 GiB of `/var/cache/apt/archives`.

- [ ] **Step 4: Prove it on `streaming` only, and read the list**

Run: `cd ansible && ansible-playbook playbooks/site.yml --check --diff --limit streaming --tags packages`
Expected: every package named in the diff starts with `cuda-` and carries a version below 13.2, or is `gedit`. If `nvidia-*` appears, stop.

- [ ] **Step 5: Commit**

```bash
git add ansible/roles/packages ansible/host_vars ansible/playbooks/site.yml hack/tests/test_ansible_layer.py
git commit -m "🚀 feature: name what each host should not be carrying"
```

---

### Task 5: The `firewall` role — a default deny that knows what K3s needs

**Files:**
- Create: `ansible/roles/firewall/{tasks,vars,defaults,meta}/main.yml`
- Modify: `ansible/playbooks/site.yml`
- Test: `hack/tests/test_ansible_layer.py` (modify)

**Interfaces:**
- Consumes: the group names from Task 1.
- Produces: the variable `ufw_rules` (list of `{port, proto, from, comment}`) resolved per group, and `ufw_enabled: true`.

- [ ] **Step 1: Write the failing test**

```python
def test_firewall_covers_every_port_k3s_needs():
    rules = yaml.safe_load((ANSIBLE / "roles/firewall/vars/main.yml").read_text())
    cluster = {(r["port"], r["proto"]) for r in rules["ufw_rules_cluster"]}
    for needed in [(6443, "tcp"), (10250, "tcp"), (2379, "tcp"), (2380, "tcp"), (8472, "udp")]:
        assert needed in cluster, f"k3s needs {needed}"


def test_firewall_leaves_the_container_ports_alone():
    rules = yaml.safe_load((ANSIBLE / "roles/firewall/vars/main.yml").read_text())
    edge = {(r["port"], r["proto"]) for r in rules["ufw_rules_edge"]}
    assert (22, "tcp") in edge and (53, "udp") in edge
    assert (5000, "tcp") not in edge, "5000 must move to loopback, not behind ufw"


def test_firewall_says_what_it_does_not_cover():
    text = (ANSIBLE / "roles/firewall/tasks/main.yml").read_text()
    assert "DOCKER-USER" in text or "portmap" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest -q hack/tests/test_ansible_layer.py -k firewall`
Expected: FAIL.

- [ ] **Step 3: Write the rule sets and the role**

`vars/main.yml` carries three lists.

`ufw_rules_cluster` — host services only, source restricted to the LAN:
`22/tcp` from `192.168.1.0/24`; `6443/tcp` from `192.168.1.0/24`; `10250/tcp` from the four node addresses; `2379/tcp` and `2380/tcp` from the three server addresses; `8472/udp` from the four node addresses; `9100/tcp` from `192.168.1.46`; and on `nas` only, `111/tcp`, `2049/tcp` and `20048/tcp` from the four node addresses.

`ufw_rules_edge`: `22/tcp` from `192.168.1.0/24`; `80/tcp` and `443/tcp` from `192.168.1.0/24`; `53/tcp` and `53/udp` from `192.168.1.0/24`; `9902/tcp` from `192.168.1.46`; `51821/udp` or `51822/udp` from `0.0.0.0/0` — WireGuard is the point, and the router already forwards it.

`tasks/main.yml`: install `ufw` where absent, write `/etc/default/ufw` with `DEFAULT_FORWARD_POLICY="ACCEPT"`, apply the rules with `community.general.ufw`, then `ufw --force enable`. A comment block states the two limits in the role itself:

- **CNI `portmap` and Docker `DNAT` happen in the FORWARD and NAT paths**, so `ufw` does not filter traffic to pods or to published container ports. The Pi's `5000` stays reachable until Task 8 binds it to `127.0.0.1`; that is why it is not in the rule list.
- **`DEFAULT_FORWARD_POLICY` stays `ACCEPT`** or every pod loses its egress. The deny is `INPUT` only.

- [ ] **Step 4: Prove it on one node, with the cluster watching**

Run, in order:

```bash
cd ansible && ansible-playbook playbooks/site.yml --check --diff --limit nuc0 --tags firewall
ansible-playbook playbooks/site.yml --limit nuc0 --tags firewall
kubectl get nodes -o wide
kubectl -n kube-system exec etcd-nuc0 -- etcdctl endpoint health 2>/dev/null || true
```

Expected: `nuc0` stays `Ready`, etcd reports healthy, `ufw status verbose` lists the rules and `Default: deny (incoming)`. If the node goes `NotReady`, the rollback is `ssh nuc0 sudo ufw --force disable` and the missing rule is found by reading `journalctl -u k3s` for the refused peer.

Only after `nuc0` is green does the operator run the same on `streaming`, then `rbp2`, then `nas`, then the two Pis.

- [ ] **Step 5: Commit**

```bash
git add ansible/roles/firewall ansible/playbooks/site.yml hack/tests/test_ansible_layer.py
git commit -m "🚀 feature: a default-deny firewall that knows what k3s needs"
```

---

### Task 6: The `nfs` role — nine exports become eight, root gets squashed

**Files:**
- Create: `ansible/roles/nfs/{tasks,templates,defaults,meta}/main.yml`
- Create: `ansible/roles/nfs/templates/exports.j2`
- Modify: `ansible/playbooks/site.yml`
- Test: `hack/tests/test_ansible_layer.py` (modify)

**Interfaces:**
- Consumes: the four node addresses, which live in `group_vars/all.yml` as `cluster_addresses` (add it in this task).
- Produces: the rendered `/etc/exports`, whose eight lines Task 6 and no other task owns.

- [ ] **Step 1: Write the failing test**

```python
def test_exports_drop_mnt_and_squash_root():
    tpl = (ANSIBLE / "roles/nfs/templates/exports.j2").read_text()
    assert "/mnt/storage/nfs/plexmedia" in tpl
    assert "no_root_squash" not in tpl
    assert "insecure" not in tpl
    assert "root_squash" in tpl
    lines = [l for l in tpl.splitlines() if l.strip() and not l.strip().startswith("#")]
    assert len(lines) == 8, f"expected the eight the cluster mounts, got {len(lines)}"
    assert not any(l.split()[0] == "/mnt" for l in lines), "/mnt must not be exported"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest -q hack/tests/test_ansible_layer.py -k exports`
Expected: FAIL.

- [ ] **Step 3: Write the template and the role**

`exports.j2` renders eight lines, each `{{ path }} {{ client_list }}(rw,sync,root_squash,no_subtree_check,fsid={{ fsid }})`, with `sync` rather than `async` (the audit's `async` is what makes a client believe a write landed when it has not) and `root_squash` in place of `no_root_squash`. `fsid` values are kept exactly as they are today — 2, 4, 5, 7, 8, 9, 11, 12 — because they are what the running mounts were made with and changing one forces every client to remount.

The comment block at the top of the template records the two facts that decide the procedure:

- `/mnt` is exported today and nothing in this repository mounts it. Before the first apply, the operator runs `showmount -a` on `nas` and writes the output into the commit message; any client that appears there and is not one of the four nodes is either added to the list deliberately or refused deliberately.
- `root_squash` makes a root write from a pod fail with `EACCES` rather than succeed silently. Every pod in the media stack runs with `PUID=1100`, so its writes are unaffected; a pod that runs as root and writes to NFS is the one to find during the first apply.

`tasks/main.yml`: assert the host is in `servers`; render `exports.j2` to `/etc/exports` with `backup: true` and **no `validate`** — there is no validator for an exports file, and a wrong one is worse than none, so the `exportfs -ra` that follows is the validator; `systemd` `nfs-server` started and enabled; `command: exportfs -ra` with `changed_when: false`; then a read-back task that runs `exportfs -v` and fails if `no_root_squash` or a bare `/mnt ` export appears in the output.

- [ ] **Step 4: Prove it without breaking a mount**

```bash
ssh nas sudo showmount -a                          # record; expected: the four nodes only
cd ansible && ansible-playbook playbooks/site.yml --check --diff --limit nas --tags nfs
ssh nas sudo exportfs -v                            # before the apply, for the comparison
```

Expected from the diff: eight lines rendered, `no_root_squash` gone, `/mnt` gone. Then the operator applies and, for each of the four nodes, confirms a pod that mounts NFS is still `Running` and can read its claim:

```bash
kubectl -n media-stack exec deploy/sonarr -- ls /config
kubectl -n media-stack exec deploy/qbittorrent -- touch /downloads/.write-test && echo ok
```

The rollback is `git checkout` of the previous `/etc/exports` content kept in `/etc/exports.bak` by the role's `backup: true`, then `exportfs -ra`.

- [ ] **Step 5: Commit**

```bash
git add ansible/roles/nfs ansible/group_vars/all.yml ansible/playbooks/site.yml hack/tests/test_ansible_layer.py
git commit -m "🚀 feature: export the eight directories that are mounted, and squash root"
```

---

### Task 7: The snapraid runner — make the nightly parity run again

**Files:**
- Create: `ansible/roles/snapraid/{tasks,meta}/main.yml`
- Create: `ansible/roles/snapraid/files/snapraid-sync.sh`
- Modify: `ansible/host_vars/nas.yml`
- Modify: `ansible/playbooks/site.yml`
- Test: `hack/tests/test_ansible_layer.py` (modify)

**Interfaces:**
- Consumes: `packages_absent` on `nas` (Task 4).
- Produces: `/usr/local/sbin/snapraid-sync` and `/etc/cron.d/snapraid`, replacing the `python2` line in root's crontab.

- [ ] **Step 1: Write the failing test**

```python
def test_snapraid_runner_does_not_need_python2():
    text = (ANSIBLE / "roles/snapraid/tasks/main.yml").read_text()
    assert "python2" not in text
    assert "/etc/cron.d/snapraid" in text
    script = (ANSIBLE / "roles/snapraid/files/snapraid-sync.sh").read_text()
    assert "snapraid sync" in script
    assert "snapraid status" in script
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest -q hack/tests/test_ansible_layer.py -k snapraid`
Expected: FAIL.

- [ ] **Step 3: Write the runner and the role**

`files/snapraid-sync.sh` — `#!/bin/bash`, `set -euo pipefail`, run `/usr/bin/snapraid sync`, then `/usr/bin/snapraid status`, then a scrub on the first Sunday of the month (`date +%d` between 1 and 7 and `date +%u` = 7). It writes nothing but snapraid's own content files and logs to the journal via `logger -t snapraid`.

The decision this encodes: **`/opt/snapraid-runner` is retired rather than ported.** Its only job was running `sync`, `scrub` and mailing the result; `python2` does not exist on 22.04's replacement and will not on 24.04 either, this homelab has no mail path for a host, and the journal already reaches Loki. Record that in the commit message.

`tasks/main.yml`: assert `ansible_hostname == "nas"`; remove root's crontab line with `ansible.builtin.cron` `name: snapraid`, `state: absent`; write `/etc/cron.d/snapraid` with `17 3 * * * root /usr/local/sbin/snapraid-sync`; `file` with `mode: "0755"` on the script; `file` `mode: "0644"` on the cron file; then run `snapraid status` and fail if its output does not contain `SnapRAID status report`.

**Move the time.** The current entry is `08:00`, which is during the day; `03:17` is before the 05:00 backup job on `nuc0` and after the 02:00 window. Put the reasoning in the task file.

- [ ] **Step 4: Prove it by running it once**

```bash
cd ansible && ansible-playbook playbooks/site.yml --check --diff --limit nas --tags snapraid
ssh nas sudo /usr/local/sbin/snapraid-sync; echo exit=$?
ssh nas sudo snapraid status | tail -20
ssh nas sudo journalctl -t snapraid -n 20
```

Expected: exit 0, a status report with the same 786,116 files and no `WARNING` about an out-of-date parity, and both entries in the journal.

- [ ] **Step 5: Commit**

```bash
git add ansible/roles/snapraid ansible/host_vars/nas.yml ansible/playbooks/site.yml hack/tests/test_ansible_layer.py
git commit -m "🐛 fix: run snapraid from cron without a python2 that is not there"
```

---

### Task 8: The Pi script — four verbs, and the state a reflash destroys

**Files:**
- Create: `ansible/pi/hosts-pi.sh`
- Create: `ansible/pi/README.md`
- Test: `hack/tests/test_pi_script.py`

**Interfaces:**
- Consumes: `ansible/playbooks/templates/runners/rbp0/` and `rbp1/`.
- Produces: `hosts-pi.sh render|setup|verify|backup|restore <host>`, honouring `PI_ROOT` (default `/`) so every verb can be pointed at a directory and tested. `render` is the only verb that writes nothing.

- [ ] **Step 1: Write the failing test**

```python
# hack/tests/test_pi_script.py
import os
import pathlib
import subprocess

REPO = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = REPO / "ansible/pi/hosts-pi.sh"


def run(*args, root=None):
    env = dict(os.environ)
    if root:
        env["PI_ROOT"] = str(root)
    return subprocess.run([str(SCRIPT), *args], capture_output=True, text=True, env=env)


def test_render_prints_the_pinned_images_for_each_pi():
    for host, port in (("rbp0", "51821"), ("rbp1", "51822")):
        out = run("render", host)
        assert out.returncode == 0, out.stderr
        assert "jacklul/pihole:2026.11.0" in out.stdout
        assert "wg-easy:15" in out.stdout
        assert port in out.stdout


def test_render_refuses_a_host_it_does_not_know():
    out = run("render", "rbp9")
    assert out.returncode == 2
    assert "rbp9" in out.stderr


def test_setup_is_idempotent(tmp_path):
    root = tmp_path
    (root / "home/vronteix/dockers").mkdir(parents=True)
    (root / "home/vronteix/dockers/pihole.env").write_text("WEBPASSWORD=x\n")
    (root / "home/vronteix/dockers/wg-easy.env").write_text("WG_HOST=x\n")
    (root / "proc/mounts").write_text("LABEL=writable / ext4 rw 0 0\n")
    first = run("setup", "rbp1", root=root)
    assert first.returncode == 0, first.stderr
    second = run("setup", "rbp1", root=root)
    assert second.returncode == 0, second.stderr
    assert "changed=0" in second.stdout


def test_setup_refuses_when_an_env_file_is_missing(tmp_path):
    (tmp_path / "home/vronteix/dockers").mkdir(parents=True)
    out = run("setup", "rbp0", root=tmp_path)
    assert out.returncode == 1
    assert "pihole.env" in out.stderr


def test_verify_names_what_is_missing(tmp_path):
    (tmp_path / "home/vronteix/dockers").mkdir(parents=True)
    out = run("verify", "rbp0", root=tmp_path)
    assert out.returncode == 1
    assert "docker-compose.yml" in out.stdout


def test_the_root_device_is_read_not_assumed():
    text = SCRIPT.read_text()
    assert "sda" not in text
    assert "/proc/mounts" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest -q hack/tests/test_pi_script.py`
Expected: FAIL — `ansible/pi/hosts-pi.sh` does not exist.

- [ ] **Step 3: Write the script**

One bash file, `set -euo pipefail`, a `HOSTS="rbp0 rbp1"` guard, `PI_ROOT="${PI_ROOT:-}"`, and one function per verb. The shape:

- `render <host>` — print the compose file and the boot files as `--- <path>` blocks. It reads the template from `playbooks/templates/runners/<host>/docker-compose.yml` next to the script, so the image tags have one source.
- `setup <host>` — for each owned file: if absent or different, write it and count a change; if identical, count nothing. Owned files are `docker-compose.yml`, `/etc/hostname`, `/etc/hosts`, and `/boot/firmware/{config.txt,cmdline.txt}` **only when they already exist** in `PI_ROOT` — the script reproduces them after a reflash, it does not invent them from nothing on a running host. Print `changed=N` as the last line.
- `verify <host>` — print every difference and exit 1 if there is one; check `docker compose ps` reports each service `running` when `PI_ROOT` is `/`, and print the root device from `/proc/mounts` so a wrong disk is visible.
- `backup <dir> <host>` — copy `$STACK/*` and the two boot files into `<dir>`, refuse if `<dir>` is on the same filesystem as the stack, and print the byte count.
- `restore <dir> <host>` — copy back, then run `verify`.

The env files are read-only inputs: `setup` fails naming the missing file and never writes one.

`ansible/pi/README.md`: the four commands, the three files the script owns, the two it refuses to write, and the reflash sequence `backup → image write → boot → setup → verify`.

- [ ] **Step 4: Run the tests, then the real verbs on a host**

Run: `python3 -m pytest -q hack/tests/test_pi_script.py`
Expected: PASS, 6 tests.

Then, on `rbp1` only: `sudo ./hosts-pi.sh verify rbp1` must list the current drift (pihole 2026.07.2, wg-easy 14) before `setup` fixes it, and `backup` must copy `etc-pihole` and `etc-pihole/gravity.db` — the check is that the byte count is above 100 MB, because anything smaller means the database was skipped.

- [ ] **Step 5: Commit**

```bash
git add ansible/pi hack/tests/test_pi_script.py
git commit -m "🚀 feature: one script for the two Pis, with the state a reflash destroys"
```

---

### Task 9: The `rbp0` Traefik — decide it, then make the template say so

**Files:**
- Modify: `ansible/playbooks/templates/runners/rbp0/docker-compose.yml`
- Modify: `ansible/pi/hosts-pi.sh` (only if the decision is removal)
- Test: `hack/tests/test_pi_script.py` (modify)

**Interfaces:**
- Consumes: `render` from Task 8.
- Produces: a template that describes what should run, and a test that pins it.

- [ ] **Step 1: Write the failing test**

```python
def test_the_rbp0_template_does_not_start_a_second_router():
    out = run("render", "rbp0")
    assert out.returncode == 0
    assert "traefik" not in out.stdout, (
        "rbp0 must not run its own Traefik: the cluster's router owns 80/443 "
        "for *.kub.home.ashudev.com and the Pi template serves --api.insecure=true"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest -q hack/tests/test_pi_script.py -k traefik`
Expected: FAIL — the template declares `traefik:v3.7.19`.

- [ ] **Step 3: Remove the service, and say why in the file**

Delete the `traefik:` service from `rbp0/docker-compose.yml`, keep `providers/providers.yaml.dist` (it is the record of what the old router served), and put a three-line comment where the service was:

- the cluster's Traefik already answers `*.kub.home.ashudev.com` on `192.168.1.46`, and the Pi's copy had no certificate store of its own;
- its only configuration was `--api.insecure=true`, which serves the dashboard on 8080 with no authentication;
- it has not run for three months, which is what `docker ps` on `rbp0` shows.

If the operator decides instead that it should run, the task changes shape: it gets a TLS store, loses `--api.insecure`, and the test asserts `--api.insecure` is absent. **Do not leave the decision implicit.**

- [ ] **Step 4: Run the test and the render for both hosts**

Run: `python3 -m pytest -q hack/tests/test_pi_script.py` then `./ansible/pi/hosts-pi.sh render rbp0 | head -30`
Expected: 7 tests pass, and the rendered output has four services.

- [ ] **Step 5: Commit**

```bash
git add ansible/playbooks/templates/runners/rbp0 ansible/pi hack/tests/test_pi_script.py
git commit -m "🔧 chore: stop declaring a second router on rbp0"
```

---

### Task 10: Close the card — the evidence, the docs, the drift

**Files:**
- Modify: `docs/how-it-works/nodes.md` (only if a measured fact changed)
- Modify: `ansible/README.MD` (replace the "this does not work" section)
- Modify: `docs/security/security-baseline-2026-11-24.md` (a superseding line only — the page is a record)
- Test: `hack/tests/test_ansible_layer.py` (modify)

**Interfaces:**
- Consumes: every role from Tasks 1–7 and the script from Task 8.
- Produces: nothing for later tasks; this is the card's acceptance.

- [ ] **Step 1: Write the failing test**

```python
def test_readme_no_longer_claims_the_layer_does_not_work():
    text = (REPO / "ansible/README.MD").read_text()
    assert "does not currently work" not in text
    assert "make check" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest -q hack/tests/test_ansible_layer.py -k readme`
Expected: FAIL.

- [ ] **Step 3: Rewrite `ansible/README.MD` and record the run**

The README keeps its history section, gains the working commands (`make check`, `make apply`, `make lint`, `./pi/hosts-pi.sh verify rbp1`), and states the one rule an executor needs: **`make check` is the drift report; a host that is correct prints no `changed`.**

Then collect the acceptance evidence: `host-audit.sh` on all six into `/tmp/opencode/host-audit/` as before, and a `make check` run. The card is accepted on the diff between this run and the 2026-11-24 record, not on the roles existing.

- [ ] **Step 4: Run the whole suite**

Run: `python3 -m pytest -q hack/tests && bash hack/check-docs.sh`
Expected: all tests pass, `check-docs.sh` exits 0.

- [ ] **Step 5: Commit**

```bash
git add ansible/README.MD docs/how-it-works/nodes.md docs/security/security-baseline-2026-11-24.md hack/tests/test_ansible_layer.py
git commit -m "📝 docs: the host layer works, and here is the evidence"
```

- [ ] **Step 6: Move the card**

`#480` goes to `Validation` with the branch, the pull request link and the collector diff in its description. It does not go to `Done`; only the operator accepts it, and `#481` stays blocked until they do.

---

## Self-review notes

**Spec coverage.** The spec's five #480 outcomes map to: the export rewrite (Task 6), the snapraid cron (Task 7), `ufw` and the time zone (Tasks 3 and 5), the Pis' SSH and containers (Tasks 2, 8, 9), the package and DKMS trims (Tasks 3 and 4). The layer's shape (D3) is Tasks 1–5; the script's shape (D4) is Task 8; D5's "the release upgrade is not automated" is honoured because no task runs `do-release-upgrade`.

**One spec line this plan corrected rather than followed.** The spec first said the script owns `pihole.env` and `wg-easy.env`. It must not: both hold secrets and both are gitignored, so the script requires them and never writes them. The spec now says so, in the same change as this plan.

**Proportion.** Ten tasks, each with one deliverable and its own verification. The two that carry real risk — the firewall (Task 5) and the exports (Task 6) — are the only ones with a rollback line, because they are the only two that can take the cluster down.
