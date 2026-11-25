# Security detection, bans, dashboard and alerts — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record who is knocking, refuse the ones that behave like attackers at the host firewall, put one screen in front of the operator that says how bad it is, and make every part of that shout when it stops working.

**Architecture:** Traefik writes its full access log — path and user agent included — to a root-owned file on `streaming`, because two consumers need different things from it: CrowdSec needs the path, the 30-day store must not have it. Fluent Bit strips the two fields on the way to Loki; CrowdSec's agent reads the file on the same node, the LAPI holds decisions on `nas`, and `crowdsec-firewall-bouncer-nftables` writes them into `nftables` on `streaming` so a ban covers every forwarded port. One small stdlib-Python exporter turns the cluster's own state and authenticated-identity events into Prometheus families, one dashboard reads them beside the gated log store, and seven rules in the two existing groups deliver to the existing contact point.

**Tech Stack:** Traefik chart 42.4.1 / Traefik v3.7.19, CrowdSec chart 0.24.3 (app v1.8.2) as a pattern-D wrapper, `crowdsec-firewall-bouncer-nftables` from the CrowdSec packagecloud repo via Ansible, Fluent Bit 5.2.0, Python 3.13 stdlib for the exporter, Grafana 13's provisioning API, pytest for the checks that run without a host.

**Spec:** [`docs/superpowers/specs/2026-11-25-security-detection-design.md`](../specs/2026-11-25-security-detection-design.md)

## Global Constraints

- **No task writes to a host from this session.** Every host command is run by the operator; each task names the command and the output that means it passed. The MCP ServiceAccount and the shell's `KUBECONFIG` are read-only (§9 of `AGENTS.md`).
- **No secret value in the repository.** The bouncer key, the authentik token and the Grafana token are read from `~/.config/secrets/homelab.env` at apply time, or from a Kubernetes Secret created by hand.
- **`cluster/argocd/**` is CRLF.** Every file created there keeps CRLF; a `\n` file diffs the whole object.
- **YAML 2 spaces, lowercase keys, except `cluster/argocd/**` which is 2 spaces and CRLF.**
- **Nothing here is accepted from a `helm template`.** Each phase names the live proof it needs, because the failure this plan exists to prevent is a detector that stopped working and looks healthy.
- **The whitelist is a safety control, not a convenience.** A ban that reaches `192.168.1.0/24`, `10.8.0.0/24`, `10.42.0.0/16`, `10.43.0.0/16` or a node address is the one failure that costs more than the attack. Task 5 writes it, Task 7 proves it with a burst that must create nothing.
- **Dashboards and rules live in Grafana's database.** They are created through the HTTP API with `$GRAFANA_TOKEN` and exported as a review copy under `cluster/helm/monitoring/grafana/export/security/`; nothing applies the copy.
- **Every chart pins its image and every chart's `appVersion` names it** (`hack/check-app-version.sh`). `v1.8.2` is app version `1.8.2`.
- **New wrapper charts get `.gitignore` entries** for `charts/` and `Chart.lock`, and the Application records no `syncPolicy.automated`.
- **Commit prefixes:** `🚀 feature:`, `🔧 chore:`, `🐛 fix:`, `📝 docs:`, `⬆️ upgrade:`.

## Review Focus

The conditions this plan does not inherit from the spec, each the most likely first failure, each with a test in the task that owns it.

1. **Two readers, one file, two field lists.** Traefik has one field configuration per target, and after `filePath` the target is the file — so `RequestPath` and the `User-Agent` header are in the file *and nowhere else*. Fluent Bit's key removal is the only thing keeping them out of the 30-day store, and it must be scoped to the new tag or it strips fields from every container's JSON. Task 1's test pins the file's field list; Task 2's pins the removal and its scope.
2. **CrowdSec's Traefik parser does not read `ClientAddr` for the source address.** It reads `ClientHost` (`remote_addr`) and uses `ClientAddr` as the destination. A plan that un-drops only `ClientAddr` produces an agent that parses nothing and reports nothing. Task 1 keeps both, and Task 5's first verification is a parsed line, not a running pod.
3. **`client_ip` in authentik is behind a page size.** `GET /api/v3/events/events/` is paginated; a 15-minute window with no `page_size` counts the first page and silently under-reports a burst. Task 10's test drives the window arithmetic, not just the HTTP call.
4. **The exporter is the one place that could copy the allowlist.** `hack/check_exposure.py` owns the public hosts. The chart keeps the same list for the posture count, and Task 9's test fails the moment the two disagree — a copy with a tripwire, not a second authority.
5. **A logrotate that renames the file can silence both tailers.** The pod has its own PID namespace, so the host cannot send Traefik the `SIGUSR1` its `Handler.Rotate` listens for; a `create`-based rule would rename the file and leave the writer on a deleted inode. Task 3 therefore truncates in place, Task 4 corrects the design, and Task 7's acceptance is a ban *after* a rotation.
6. **The bouncer's liveness is the weakest signal here.** It publishes its own Prometheus endpoint (`fw_bouncer_banned_ips`, and more) but nothing scrapes `streaming`'s host ports today, so Task 6 adds one static job and one narrow firewall rule — and if that proves unacceptable, the gap goes on the dashboard rather than behind a rule that pretends to watch it.
7. **Both CrowdSec pods probe `/metrics` on 6060, and the chart configures no listener.** A values file that sets the port but not `prometheus.listen_addr` produces pods that never become Ready and die on the startup probe — and the failure looks like a slow start, not a bad value. Task 5 sets both blocks and tests them.

---

## Phase 0 — see the client, without widening the store

### Task 1: Traefik writes the full line to a file, and the store keeps today's fields

**Files:**
- Modify: `cluster/helm/platform/traefik/values.yaml`
- Test: `hack/tests/test_traefik_access_log.py`

**Interfaces:**
- Produces: `/var/log/traefik/access.log` on `streaming`, one JSON line per request, carrying `ClientAddr`, `ClientHost`, `RequestPath` and `request_User-Agent` — and nothing else new. Produces the pod-side hostPath mount Task 3 owns the directory for and Task 5 reads.
- Consumes: nothing.

- [ ] **Step 1: Write the failing test**

```python
# hack/tests/test_traefik_access_log.py
"""The access log is read by two consumers with opposite requirements.

CrowdSec's `crowdsecurity/traefik-logs` parser reads the source address from
`ClientHost` (it uses `ClientAddr` as the *destination*, `Split(..., ':')[0]`)
and the user agent from `request_User-Agent`. Loki must hold neither the path
nor any header. Both are true only because the log goes to a file and Fluent
Bit strips the two keys on the way out, so this test pins the field list that
makes the file usable and the flag that makes the file exist.
"""

import pathlib
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
VALUES = ROOT / "cluster/helm/platform/traefik/values.yaml"


class AccessLogTests(unittest.TestCase):
    def setUp(self):
        self.traefik = yaml.safe_load(VALUES.read_text())["traefik"]
        self.access = self.traefik["accessLog"]

    def test_the_log_is_json_at_a_file_on_the_host(self):
        self.assertTrue(self.access["enabled"])
        self.assertEqual(self.access["format"], "json")
        self.assertEqual(self.access["filePath"], "/var/log/traefik/access.log")

    def test_the_host_directory_is_mounted_read_write(self):
        volumes = {v["name"]: v for v in self.traefik["deployment"]["additionalVolumes"]}
        self.assertEqual(volumes["traefik-access-log"]["hostPath"]["path"], "/var/log/traefik")
        # DirectoryOrCreate, not Directory: an emptyDir-style hostPath of type
        # Directory refuses to mount when the path is missing, and the pod that
        # refuses to start is the router — the whole house offline to add a log.
        # Traefik itself survives a file it cannot open (accesslog.NewHandler
        # logs "Unable to create access logger" and returns a nil handler); the
        # kubelet does not survive a mount it cannot make. The Ansible role then
        # sets 2770 65532:65532 on the directory the kubelet created as root.
        self.assertEqual(volumes["traefik-access-log"]["hostPath"]["type"], "DirectoryOrCreate")
        mounts = {m["name"]: m for m in self.traefik["additionalVolumeMounts"]}
        self.assertEqual(mounts["traefik-access-log"]["mountPath"], "/var/log/traefik")
        self.assertFalse(mounts["traefik-access-log"].get("readOnly", False))

    def test_the_parser_can_find_the_client_and_the_user_agent(self):
        names = self.access["fields"]["names"]
        self.assertEqual(names["ClientHost"], "keep")
        self.assertEqual(names["ClientAddr"], "keep")
        self.assertEqual(names["RequestPath"], "keep")
        self.assertEqual(self.access["fields"]["headers"]["names"], {"User-Agent": "keep"})

    def test_everything_that_names_a_resource_stays_dropped(self):
        names = self.access["fields"]["names"]
        for field in ("ClientUsername", "ClientPort", "RequestReferer"):
            self.assertEqual(names[field], "drop", field)
        self.assertEqual(self.access["fields"]["headers"]["defaultMode"], "drop")
        self.assertEqual(self.access["fields"]["queryParameters"]["defaultMode"], "drop")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run it and watch it fail**

```bash
uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_traefik_access_log.py
```

Expected: four failures, `KeyError: 'filePath'` first.

- [ ] **Step 3: Change the values**

In `cluster/helm/platform/traefik/values.yaml`, under `traefik.accessLog`, replace the existing block with:

```yaml
  accessLog:
    enabled: true
    format: json
    # The line is written to a file instead of stdout, and that is the whole
    # design: CrowdSec needs the path and the user agent to recognise probing,
    # and the 30-day store must not hold either. Fluent Bit tails this file and
    # removes those two keys before Loki (cluster/helm/monitoring/fluent-bit),
    # so the store keeps exactly the field list it had, plus the client address.
    #
    # Measured 2026-11-22: ~15 MB a day, 769 bytes a line. `bufferingSize` hands
    # each line to a writer goroutine, so a slow disk delays the log, never a
    # response.
    filePath: /var/log/traefik/access.log
    bufferingSize: 100
    fields:
      defaultMode: keep
      names:
        # Both, and not by accident. `crowdsecurity/traefik-logs` reads
        # `ClientHost` into `remote_addr` — the field CrowdSec turns into the
        # decision's source address — and uses `ClientAddr` as dest_addr.
        # Un-dropping only one of them leaves the agent parsing nothing.
        ClientHost: keep
        ClientAddr: keep
        # Kept for the file's two readers; removed by Fluent Bit before Loki.
        RequestPath: keep
        # A URL in the query string can carry a FileBrowser share token or a
        # Plex token. It stays out of the log entirely, which is also why
        # `RequestPath` carries no `?...` from v3.7.3 on.
        ClientUsername: drop
        ClientPort: drop
        # The Referer header is only a core field in the CLF format, but this is
        # where it would live if Traefik started writing it.
        RequestReferer: drop
      headers:
        # One header, kept on purpose: the user agent is how a scanner is
        # recognised. Everything else — Cookie, Authorization, Referer — is
        # dropped, so a stolen session never reaches this file or the store.
        defaultMode: drop
        names:
          User-Agent: keep
      queryParameters:
        defaultMode: drop
```

Then add the volume **inside the existing `deployment:` block** — the file already has that key
(`kind` and `replicas`), and a second `deployment:` key would silently drop them — and the mount as a
new top-level key beside `accessLog`. That split is the chart's: volumes under
`deployment.additionalVolumes`, mounts at `additionalVolumeMounts` (confirmed in `_podtemplate.tpl`
of chart 42.4.1).

```yaml
traefik:
  deployment:
    kind: Deployment
    replicas: 1
    # ... existing keys stay ...
    additionalVolumes:
      - name: traefik-access-log
        hostPath:
          # A directory, not a file, and `DirectoryOrCreate` rather than
          # `Directory`: the kubelet refuses to mount a Directory that does not
          # exist, and a router that will not start is the house offline to add a
          # log. Created here as root 0755, then corrected to 2770 65532:65532 by
          # the Ansible role; until that role runs, Traefik logs "Unable to
          # create access logger" and serves on without writing a line, which
          # the Phase 0 gate catches.
          path: /var/log/traefik
          type: DirectoryOrCreate

  additionalVolumeMounts:
    - name: traefik-access-log
      mountPath: /var/log/traefik
```

- [ ] **Step 4: Run the tests and the chart gate**

```bash
uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_traefik_access_log.py
helm template test cluster/helm/platform/traefik -f cluster/helm/platform/traefik/values.yaml | grep -c -- '--accesslog.filepath=/var/log/traefik/access.log'
helm template test cluster/helm/platform/traefik -f cluster/helm/platform/traefik/values.yaml | grep -c -- '--accesslog.fields.headers.names.User-Agent=keep'
```

Expected: 4 passed, then `1` and `1`. The second render also proves the chart accepts `fields.headers.names`, which its schema validates.

- [ ] **Step 5: Commit**

```bash
git add cluster/helm/platform/traefik/values.yaml hack/tests/test_traefik_access_log.py
git commit -m "🚀 feature: Traefik writes its access log to a root-only file on streaming"
```

---

### Task 2: Fluent Bit tails the file and removes the path and the user agent

**Files:**
- Modify: `cluster/helm/monitoring/fluent-bit/values.yaml`
- Test: `hack/tests/test_fluent_bit_security.py`

**Interfaces:**
- Consumes: `/var/log/traefik/access.log` from Task 1, mounted from the host.
- Produces: the Loki stream `{job="fluent-bit", namespace="kube-system", container="traefik", node="streaming", source="accesslog"}`, whose **line** carries `ClientHost`, `ClientAddr`, `DownstreamStatus`, `RequestHost`, `RouterName` and no `RequestPath`, no `request_User-Agent`, no query and no header. Every existing `{job="fluent-bit", container="traefik"}` query keeps working because the static labels match what the container pipeline set.

- [ ] **Step 1: Write the failing test**

```python
# hack/tests/test_fluent_bit_security.py
"""The access-log pipeline, and the key removal that keeps the store honest.

Fluent Bit's config is a string, so a values edit is untyped and a silent
regression. This test reads the three blocks and checks the two properties that
matter: the removal is scoped to the new tag, and the labels keep every existing
panel query resolving.
"""

import pathlib
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
VALUES = ROOT / "cluster/helm/monitoring/fluent-bit/values.yaml"
TAG = "traefik.access"


def config():
    return yaml.safe_load(VALUES.read_text())["fluent-bit"]["config"]


class AccessLogPipelineTests(unittest.TestCase):
    def test_a_second_input_tails_the_host_file(self):
        inputs = config()["inputs"]
        self.assertIn("Path /var/log/traefik/access.log", inputs)
        self.assertIn(f"Tag {TAG}", inputs)

    def test_the_path_and_the_user_agent_never_reach_loki(self):
        filters = config()["filters"]
        block = filters.split("[FILTER]")
        scoped = [b for b in block if f"Match {TAG}" in b]
        self.assertEqual(len(scoped), 1, "the removal must be its own, tag-scoped filter")
        self.assertIn("Remove RequestPath", scoped[0])
        self.assertIn("Remove request_User-Agent", scoped[0])
        # And the container pipeline must not have picked up the rule.
        for other in block:
            if other.strip() and f"Match {TAG}" not in other:
                self.assertNotIn("Remove RequestPath", other)

    def test_the_labels_keep_every_existing_query_resolving(self):
        outputs = config()["outputs"]
        block = [b for b in outputs.split("[OUTPUT]") if f"Match {TAG}" in b]
        self.assertEqual(len(block), 1)
        for label in ("job=fluent-bit", "namespace=kube-system", "container=traefik", "node=streaming"):
            self.assertIn(label, block[0])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run it and watch it fail**

```bash
uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_fluent_bit_security.py
```

Expected: three failures on the missing `traefik.access` tag.

- [ ] **Step 3: Add the input, the filter and the output**

In `cluster/helm/monitoring/fluent-bit/values.yaml`, append to `config.inputs` — **after** the existing `kube.*` block, in the same string:

```
      # Traefik's access log, written to a file on the host because two readers
      # disagree about its contents (cluster/helm/platform/traefik). Parser json
      # splits the line into fields so the next filter can remove two of them;
      # Line_Format json puts the rest back as one JSON object, which is what
      # Loki's `| json` panels already expect.
      #
      # No `Time_Key`: Traefik writes RFC3339 with nanoseconds and an offset, and
      # a `Time_Format` that does not match it is a silent fallback to the wrong
      # timestamp or a rejected line. The record is stamped at ingestion, a
      # fraction of a second later, and the line's own `time` field stays in the
      # JSON for anything that needs the request's own clock.
      [INPUT]
          Name tail
          Path /var/log/traefik/access.log
          Tag traefik.access
          Parser json
          Mem_Buf_Limit 5MB
          Skip_Long_Lines On
```

Append to `config.filters`:

```
      # The only difference between the file and the store. `RequestPath` holds
      # FileBrowser's /share/<hash> and Plex's token when one slips past the
      # query-parameter drop; `request_User-Agent` is a fingerprint nobody needs
      # for 30 days. Scoped by tag: a bare `modify` filter would strip these keys
      # from every container's JSON in the kube.* pipeline.
      [FILTER]
          Name modify
          Match traefik.access
          Remove RequestPath
          Remove request_User-Agent
          Remove RequestReferer
```

Append to `config.outputs`, as a second `[OUTPUT]` block:

```
      # Static labels, chosen to match the container pipeline exactly: the
      # Traefik dashboard's rows and docs/reference/log-budget.md query
      # `{job="fluent-bit", container="traefik"}`, and they keep resolving
      # without an edit. `source` is the one addition, so the file's stream can
      # be told apart from a container that logs to stdout.
      [OUTPUT]
          Name loki
          Match traefik.access
          Host loki.monitoring.svc.cluster.local
          Port 3100
          Line_Format json
          Compress gzip
          Retry_Limit no_limits
          Labels job=fluent-bit, namespace=kube-system, container=traefik, node=streaming, source=accesslog
```

- [ ] **Step 4: Render and run**

```bash
helm template test cluster/helm/monitoring/fluent-bit -f cluster/helm/monitoring/fluent-bit/values.yaml >/dev/null
uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_fluent_bit_security.py
```

Expected: `3 passed`. `hack/check-config-keys.sh` is not required here — this chart is not in `hack/config-key-checks.yaml` — but Phase 0's gate runs `hack/check-charts.sh`, which covers the render.

- [ ] **Step 5: Commit**

```bash
git add cluster/helm/monitoring/fluent-bit/values.yaml hack/tests/test_fluent_bit_security.py
git commit -m "🚀 feature: ship the access log to Loki without the path or the user agent"
```

---

### Task 3: The directory and its rotation, on `streaming` only

**Files:**
- Create: `ansible/roles/traefik_logs/tasks/main.yml`
- Create: `ansible/roles/traefik_logs/meta/main.yml`
- Create: `ansible/roles/traefik_logs/defaults/main.yml`
- Create: `ansible/roles/traefik_logs/templates/logrotate.j2`
- Modify: `ansible/playbooks/site.yml`
- Test: `hack/tests/test_ansible_streaming_roles.py`

**Interfaces:**
- Produces: `/var/log/traefik`, mode `2770`, owner and group `65532:65532` (Traefik's uid/gid); the file mode `0660`; `/etc/logrotate.d/traefik-access` rotating at 200 MB, seven copies, truncated in place because the host cannot signal a process inside the pod's PID namespace.
- Consumes: nothing. Task 6 adds the second role in the same test file and the same `when:` guard.

- [ ] **Step 1: Write the failing test**

```python
# hack/tests/test_ansible_streaming_roles.py
"""The two roles that exist only on `streaming`: the log directory and the bouncer.

Neither is a role for every host. `site.yml` runs one play over `all`, so a role
that is not for every host carries its own guard — and a guard that names a group
nobody is in is a role that silently never runs.
"""

import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
ANSIBLE = ROOT / "ansible"


class TraefikLogsRoleTests(unittest.TestCase):
    def setUp(self):
        self.role = ANSIBLE / "roles/traefik_logs"

    def test_the_role_exists_with_metadata_and_a_logrotate_template(self):
        self.assertTrue((self.role / "tasks/main.yml").is_file())
        self.assertTrue((self.role / "templates/logrotate.j2").is_file())
        self.assertIn("role_name: traefik_logs", (self.role / "meta/main.yml").read_text())

    def test_the_directory_belongs_to_traefiks_uid(self):
        tasks = (self.role / "tasks/main.yml").read_text()
        defaults = (self.role / "defaults/main.yml").read_text()
        self.assertIn("path: \"{{ traefik_logs_dir }}\"", tasks)
        self.assertIn('mode: "2770"', tasks)
        self.assertIn("owner: \"{{ traefik_logs_uid }}\"", tasks)
        self.assertIn("traefik_logs_dir: /var/log/traefik", defaults)
        self.assertIn("traefik_logs_uid: 65532", defaults)
        self.assertIn("traefik_logs_gid: 65532", defaults)

    def test_rotation_is_bounded_and_truncates_in_place(self):
        template = (self.role / "templates/logrotate.j2").read_text()
        defaults = (self.role / "defaults/main.yml").read_text()
        self.assertIn("size {{ traefik_logs_size }}", template)
        self.assertIn("rotate {{ traefik_logs_rotate }}", template)
        self.assertIn("traefik_logs_size: 200M", defaults)
        self.assertIn("traefik_logs_rotate: 7", defaults)
        # The pod has its own PID namespace, so a `postrotate` on the host cannot
        # signal Traefik: `create` plus SIGUSR1 would rotate the name and leave
        # the writer holding the old inode. Copy-then-truncate is the mechanism
        # that works from outside the namespace.
        self.assertIn("copytruncate", template)
        self.assertNotIn("kill -USR1", template)
        # The directory is group-writable for Traefik's gid, and logrotate
        # refuses to rotate a file there without being told which account runs.
        self.assertIn("su root root", template)

    def test_site_runs_it_and_says_which_host(self):
        site = (ANSIBLE / "playbooks/site.yml").read_text()
        self.assertIn("role: traefik_logs", site)
        block = site.split("role: traefik_logs")[1].split("- ")[0]
        self.assertIn("inventory_hostname == 'streaming'", block)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run it and watch it fail**

```bash
uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_ansible_streaming_roles.py
```

Expected: `FileNotFoundError` on the role directory.

- [ ] **Step 3: Write the role**

`ansible/roles/traefik_logs/meta/main.yml`:

```yaml
---
galaxy_info:
  role_name: traefik_logs
  author: operator
  description: The access-log directory on streaming, its ownership and its rotation
  license: MIT
  min_ansible_version: "2.18"
  platforms:
    - name: Ubuntu
      versions:
        - all
```

`ansible/roles/traefik_logs/defaults/main.yml`:

```yaml
---
# Traefik runs as 65532:65532 with `readOnlyRootFilesystem: true` and no
# privileged capability, so it can only write where the host already lets it.
traefik_logs_dir: /var/log/traefik
traefik_logs_file: access.log
traefik_logs_uid: 65532
traefik_logs_gid: 65532
traefik_logs_size: 200M
traefik_logs_rotate: 7
```

`ansible/roles/traefik_logs/tasks/main.yml`:

```yaml
---
# The one directory in this repository that holds a URL for a week. `/var/log`
# itself is 0755 and root-owned, so the mode here is what keeps the file out of
# every other account's reach: 2770 with the group set to Traefik's gid, and the
# setgid bit so the file the container creates inherits it.
- name: Create the access-log directory
  ansible.builtin.file:
    path: "{{ traefik_logs_dir }}"
    state: directory
    owner: "{{ traefik_logs_uid }}"
    group: "{{ traefik_logs_gid }}"
    mode: "2770"

- name: Install logrotate where it is absent
  ansible.builtin.apt:
    name: logrotate
    state: present

- name: Install the rotation rule
  ansible.builtin.template:
    src: logrotate.j2
    dest: /etc/logrotate.d/traefik-access
    owner: root
    group: root
    mode: "0644"

# `--debug` parses the rule and prints what it would do, so a syntax error fails
# this task instead of the first rotation three days later. It reads the whole
# config, not the drop-in, because that is what cron hands to logrotate, and a
# stanza can parse alone and still be skipped once the include and the global
# settings are applied around it. On a real apply the template above has just
# written the stanza, so the parse covers it; under `--check` the template writes
# nothing and the parse covers only the host's existing config.
#
# The absolute path matches the look-up below: a bare `logrotate` resolved from
# `PATH` while the guard checks one path is a proof that disappears silently if
# the two ever differ. A drop-in this role never wrote — apt, dpkg, rsyslog — can
# be the one that fails a task named after this rule; read the error before
# blaming `traefik-access`.
- name: Look for the rotation tool
  ansible.builtin.stat:
    path: /usr/sbin/logrotate
  register: traefik_logs_logrotate
  check_mode: false

- name: Prove the rotation rule parses
  ansible.builtin.command:
    cmd: /usr/sbin/logrotate --debug /etc/logrotate.conf
  changed_when: false
  check_mode: false
  when: traefik_logs_logrotate.stat.exists
```

`ansible/roles/traefik_logs/templates/logrotate.j2`:

```
{{ traefik_logs_dir }}/{{ traefik_logs_file }} {
    size {{ traefik_logs_size }}
    rotate {{ traefik_logs_rotate }}
    missingok
    notifempty
    compress
    delaycompress
    # `su root root`: the directory is group-writable for Traefik's gid (2770),
    # and logrotate refuses to touch a file in such a directory unless it is
    # told which account should do the work.
    su root root
    # `copytruncate`, not `create` plus a signal. Traefik's access-log handler
    # does reopen its file on SIGUSR1 (Handler.Rotate, "to allow for rotation by
    # an external source") — but the pod runs in its own PID namespace, as every
    # pod does and as the chart offers no `hostPID` to change, so a `postrotate`
    # on the host cannot find the process to signal. Copy-then-truncate leaves
    # Traefik's descriptor valid and needs no cooperation from it at all.
    #
    # The cost is written down rather than hidden: a line written between the
    # copy and the truncate is lost, which is a handful of lines per 200 MB.
    # Both tailers see the size shrink and continue from the start of the file;
    # the acceptance test in Task 7 is a ban *after* a rotation.
    copytruncate
}
```

Neither `create` nor a signal is used, so the new file keeps the mode and ownership
Traefik gave it: `0660`, `65532:65532`, inherited from the setgid directory.

In `ansible/playbooks/site.yml`, append after the `snapraid` role:

```yaml
    # The access log the detector reads: one directory, one owner, one rotation
    # rule, and only on the node that runs the router. It comes before the
    # bouncer's role, which is also streaming-only.
    - role: traefik_logs
      when: "inventory_hostname == 'streaming'"
```

- [ ] **Step 4: Lint and run**

```bash
cd ansible && ansible-lint roles/traefik_logs
cd .. && uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_ansible_streaming_roles.py
cd ansible && ansible-playbook playbooks/site.yml --check --limit streaming --tags all 2>&1 | tail -20
```

Expected: lint clean, `4 passed`, and a `--check` run whose only changes on `streaming` are the directory and the rotation file — the drift contract from the hosts plan.

- [ ] **Step 5: Commit**

```bash
git add ansible/roles/traefik_logs ansible/playbooks/site.yml hack/tests/test_ansible_streaming_roles.py
git commit -m "🚀 feature: own /var/log/traefik and its rotation on streaming"
```

---

### Task 4: The field list, written down, and the two cards it supersedes

**Files:**
- Modify: `docs/observability-conventions.md`
- Modify: `docs/reference/metrics.md` (the access-log paragraph, if it names the fields)
- Modify: `docs/superpowers/specs/2026-11-25-security-detection-design.md` (three corrections: `ClientHost`, not `ClientAddr`, is the source address; rotation truncates in place because the host cannot signal a process in the pod; the mount creates the directory because a router that will not start is worse than a log that does not)
- Test: `hack/tests/test_check_docs.py` runs as part of `hack/check-docs.sh`

**Interfaces:**
- Consumes: the field list from Task 1 and the removal list from Task 2.
- Produces: the page the next agent reads instead of re-deriving the log's shape, and the board comment that closes #410 / #544.

- [ ] **Step 1: Amend the page**

In `docs/observability-conventions.md`, replace the access-log section (currently "The URL and the client are not in it") with the new shape:

```markdown
**The URL is not in the store, and the client is.** Since 2026-11-25 Traefik writes its access log
to `/var/log/traefik/access.log` on `streaming` — a file owned by `65532:65532`, mode `0660`, rotated
at 200 MB with seven copies — and Fluent Bit tails it. The file carries the path and the user agent
because CrowdSec's `crowdsecurity/traefik-logs` parser needs both to recognise probing;
[the security design](superpowers/specs/2026-11-25-security-detection-design.md) says why the
detector was worth that. Fluent Bit removes `RequestPath`, `request_User-Agent` and
`RequestReferer` before Loki, so what the 30-day store holds is:

| Field | In the store | Why |
|---|---|---|
| `ClientHost`, `ClientAddr` | yes | the source address, and the one CrowdSec reads |
| `DownstreamStatus`, `RouterName`, `RequestHost`, `RequestMethod`, `Duration`, `TLSVersion` | yes | as before |
| `RequestPath` | **no** | FileBrowser share tokens, Plex tokens; removed by Fluent Bit |
| every header except `User-Agent` | no | dropped by Traefik before the file is written |
| `User-Agent` | **no** | in the file for the detector, removed by Fluent Bit |
| `ClientUsername`, `ClientPort` | no | dropped by Traefik |

Two consequences worth knowing before writing a query: `{job="fluent-bit", container="traefik"}`
still resolves, because the file's output carries that label statically, and **a request the bouncer
dropped has no line at all** — it never reaches Traefik, so a banned address disappears from the log
rather than appearing with a 403.
```

- [ ] **Step 2: Correct the design's two wrong statements**

In `docs/superpowers/specs/2026-11-25-security-detection-design.md` §6.1, replace "`ClientAddr` leaves the drop list" with:

```markdown
1. `ClientHost` **and** `ClientAddr` leave the drop list. CrowdSec's Traefik parser reads the source
   address from `ClientHost` (`remote_addr`) and treats `ClientAddr` as the destination, so
   un-dropping one of them alone leaves the agent parsing nothing. `ClientUsername`, `ClientPort`,
   `RequestPath`, `RequestReferer`, the query parameters and every header except `User-Agent` stay
   dropped **for the store**; the file is a different target, and §6.1's third point is where the
   path and the user agent are removed again before Loki.
```

Then the rotation paragraph (§6.1, "**Rotation.**") says `create` and a `postrotate` sending
`SIGUSR1`. Replace it, and the matching risk-table row and open question, with what is true:

```markdown
- **Rotation.** `logrotate` on `streaming`, 200 MB, seven copies, `copytruncate` under `su root root`.
  Traefik's access-log handler does reopen its file on `SIGUSR1` (`Handler.Rotate`), but the pod runs
  in its own PID namespace — as every pod does, and as the chart offers no `hostPID` to change — so a
  `postrotate` on the host cannot find the process to signal. Truncating in place leaves the writer's
  descriptor valid and asks nothing of the router. The cost is a handful of lines lost between the
  copy and the truncate, per 200 MB; both tailers see the size shrink and continue from the start of
  the file, and the acceptance test is a ban *after* a rotation, not a ban before one.
```

The risk row becomes: *Traefik's writer and the two tailers disagree after rotation* → **Low**
(copy-then-truncate keeps the same inode, so there is no inode to lose; the residual cost is a few
lines per rotation), and the open question about the inode is answered and deleted.

Then the **Ownership** bullet in §6.1 says the Ansible role *pre-creates* the directory. It does not —
a `hostPath` of type `Directory` that is missing keeps the pod from starting, so the chart declares
`DirectoryOrCreate` and the role corrects what the kubelet made. Replace the bullet with:

```markdown
- **Ownership, and who creates the directory.** The mount is `DirectoryOrCreate`, because a
  `hostPath` of type `Directory` that is missing refuses to mount and the pod that refuses to start
  is the router: the whole house offline to add a log. The kubelet creates it as root; the Ansible
  role then sets `2770` and `65532:65532`, and Traefik creates the file inside it. Until the role
  runs, Traefik logs "Unable to create access logger" and serves on without a line — the failure
  this section already says is silent, and the reason the gate reads the file rather than the pod.
```

- [ ] **Step 3: Run the documentation checks**

```bash
hack/check-docs.sh --base origin/develop
```

Expected: no error. The staleness report at the end may name pages whose banner is older than a file they cite; that list prints and never fails.

- [ ] **Step 4: Comment on the two cards**

Through the Vikunja MCP (the bot's token, `$VIKUNJA_MCP_TOKEN`), add one comment to the card behind #410 and one to #544:

```markdown
Superseded on 2026-11-25 by the access-log change in `docs/superpowers/plans/2026-11-25-security-detection.md`
Task 1–4. The store's field list is unchanged except that `ClientHost` and `ClientAddr` are kept;
`RequestPath` and `User-Agent` are written to a root-only file on `streaming` for CrowdSec and
removed before Loki. `docs/observability-conventions.md` is the new list, and #544's "all fields are
0 or absent" verification is replaced by Task 4 Step 3.
```

- [ ] **Step 5: Commit**

```bash
git add docs/observability-conventions.md docs/superpowers/specs/2026-11-25-security-detection-design.md
git commit -m "📝 docs: the access log's new field list, and which half lives where"
```

**Phase 0 gate, before Phase 1 starts.**

```bash
helm template test cluster/helm/platform/traefik -f cluster/helm/platform/traefik/values.yaml >/dev/null
helm template test cluster/helm/monitoring/fluent-bit -f cluster/helm/monitoring/fluent-bit/values.yaml >/dev/null
uv run --with-requirements hack/tests/requirements.txt python -m pytest -q \
  hack/tests/test_traefik_access_log.py hack/tests/test_fluent_bit_security.py
hack/check-docs.sh --base origin/develop
```

The live proof, on `streaming`, is the whole point of the phase — and it is what Task 7's proofs
depend on:

```bash
ls -ld /var/log/traefik                 # drwxrws--- 65532 65532
ls -l  /var/log/traefik/access.log      # -rw-rw---- 65532 65532, growing
tail -c 400 /var/log/traefik/access.log # one JSON line with RequestPath and request_User-Agent
```

and from Grafana, a line from the store with neither:

```bash
curl -skG -H "Authorization: Bearer $GRAFANA_TOKEN" \
  "https://grafana.kub.local/api/datasources/proxy/uid/P8E80F9AEF21F6940/loki/api/v1/query" \
  --data-urlencode 'query={job="fluent-bit", container="traefik", source="accesslog"} | json' \
  | jq -r '.data.result[0].values[-1][1]'
```

A line that still carries `RequestPath` means the tag-scoped removal is not scoped to the new tag;
a file that is not mode `0660` means Task 3 has not run on that host. Both are fixes here, not
findings to carry into the ban layer.

---

## Phase 1 — detect and ban

### Task 5: CrowdSec in the cluster, with the whitelist that must never fail

**Files:**
- Create: `cluster/helm/security/crowdsec/Chart.yaml`
- Create: `cluster/helm/security/crowdsec/values.yaml`
- Create: `cluster/helm/security/crowdsec/templates/lapi-lan-service.yaml`
- Create: `cluster/argocd/projects/security/crowdsec.yaml`
- Modify: `.gitignore`
- Modify: `docs/how-it-works/applications.md` (one catalogue row — `hack/check-docs.sh` fails without it)
- Test: `hack/tests/test_crowdsec_chart.py`

**Interfaces:**
- Consumes: `/var/log/traefik/access.log` from Task 1, on `streaming`.
- Produces: the LAPI Service at `crowdsec-service.security.svc:8080`, the NodePort `30500` on every node for the host-side bouncer (`security/crowdsec-lapi-lan`, 30500 → 8080), the bouncer key Secret `crowdsec-bouncer-key` (key `api-key`) that Task 6 reads from the operator's environment, and the whitelist postoverflow every later proof depends on.

- [ ] **Step 1: Write the failing test**

```python
# hack/tests/test_crowdsec_chart.py
"""The chart's values, checked where a wrong value is expensive.

Three of them are load bearing and silent when wrong: the agent must be a
Deployment pinned to the node that owns the log file, the acquisition must set
the label the Traefik parser filters on, and the whitelist must cover the four
ranges and the four node addresses before any of this bans the operator's own
LAN.
"""

import pathlib
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
CHART = ROOT / "cluster/helm/security/crowdsec"
APP = ROOT / "cluster/argocd/projects/security/crowdsec.yaml"

WHITELIST_RANGES = ("192.168.1.0/24", "10.8.0.0/24", "10.42.0.0/16", "10.43.0.0/16")


class ChartTests(unittest.TestCase):
    def setUp(self):
        self.chart = yaml.safe_load((CHART / "Chart.yaml").read_text())
        self.crowdsec = yaml.safe_load((CHART / "values.yaml").read_text())["crowdsec"]

    def test_the_chart_pins_the_upstream_version_and_the_image(self):
        dep = self.chart["dependencies"][0]
        self.assertEqual((dep["name"], dep["version"]), ("crowdsec", "0.24.3"))
        self.assertEqual(self.chart["appVersion"], "1.8.2")
        self.assertEqual(self.crowdsec["image"]["tag"], "v1.8.2")

    def test_the_agent_is_a_deployment_on_streaming_with_the_log_mounted(self):
        agent = self.crowdsec["agent"]
        self.assertTrue(agent["isDeployment"])
        self.assertEqual(agent["replicas"], 1)
        self.assertEqual(agent["nodeSelector"], {"kubernetes.io/hostname": "streaming"})
        volumes = {v["name"]: v for v in agent["extraVolumes"]}
        self.assertEqual(volumes["traefik-logs"]["hostPath"]["path"], "/var/log/traefik")
        mounts = {m["name"]: m for m in agent["extraVolumeMounts"]}
        self.assertTrue(mounts["traefik-logs"]["readOnly"])
        # One file, not the machine: the image's own /var/log mount is off.
        self.assertFalse(agent["hostVarLog"])
        self.assertEqual(len(agent["extraVolumeMounts"]), 1)

    def test_the_acquisition_sets_the_label_the_traefik_parser_filters_on(self):
        acquisition = self.crowdsec["agent"]["additionalAcquisition"]
        self.assertEqual(len(acquisition), 1)
        entry = acquisition[0]
        self.assertEqual(entry["filenames"], ["/var/log/traefik/access.log"])
        # `crowdsecurity/traefik-logs` filters on `evt.Parsed.program startsWith
        # 'traefik'`; the label is what fills that field for a file source.
        self.assertEqual(entry["labels"]["program"], "traefik")
        self.assertEqual(entry["labels"]["type"], "traefik")

    def test_the_collection_that_supplies_the_parser_is_installed(self):
        env = {e["name"]: e.get("value") for e in self.crowdsec["agent"]["env"]}
        self.assertIn("crowdsecurity/traefik", env["COLLECTIONS"])

    def test_the_lapi_keeps_its_state_on_local_path(self):
        lapi = self.crowdsec["lapi"]
        self.assertEqual(lapi["nodeSelector"], {"kubernetes.io/hostname": "nas"})
        for name in ("data", "config"):
            claim = lapi["persistentVolume"][name]
            self.assertTrue(claim["enabled"])
            self.assertEqual(claim["storageClassName"], "local-path")

    def test_the_bouncer_key_comes_from_a_secret_and_not_from_values(self):
        env = {e["name"]: e for e in self.crowdsec["lapi"]["env"]}
        ref = env["BOUNCER_KEY_firewall"]["valueFrom"]["secretKeyRef"]
        self.assertEqual((ref["name"], ref["key"]), ("crowdsec-bouncer-key", "api-key"))

    def test_the_central_api_is_off(self):
        # Without this the LAPI registers with CrowdSec's central API on first
        # start and subscribes to the shared blocklist — a decision to take after
        # measuring our own traffic, and the only part of this that talks to a
        # company.
        env = {e["name"]: e.get("value") for e in self.crowdsec["lapi"]["env"]}
        self.assertEqual(env["DISABLE_ONLINE_API"], "true")

    def test_both_workloads_listen_on_6060(self):
        # The chart's probes GET `/metrics` on port 6060 for both workloads, and
        # it configures no listener of its own. An agent without this block never
        # becomes Ready; a LAPI without it does the same.
        agent = self.crowdsec["config"]["agent_config.yaml.local"]
        self.assertIn("listen_port: 6060", agent)
        self.assertIn("listen_addr: 0.0.0.0", agent)
        lapi = self.crowdsec["config"]["config.yaml.local"]
        self.assertIn("listen_port: 6060", lapi)
        self.assertIn("auto_registration", lapi)

    def test_the_whitelist_covers_every_range_a_ban_must_never_reach(self):
        files = self.crowdsec["config"]["postoverflows"]["s01-whitelist"]
        self.assertEqual(len(files), 1)
        text = next(iter(files.values()))
        for cidr in WHITELIST_RANGES:
            self.assertIn(cidr, text)
        for node in ("192.168.1.41", "192.168.1.27", "192.168.1.46", "192.168.1.48"):
            self.assertIn(node, text)

    def test_the_whitelist_is_a_postoverflow_so_no_decision_is_ever_created(self):
        # The chart mounts `config.postoverflows.s01-whitelist` in the agent; a
        # whitelist written as a scenario or a parser would excuse a decision
        # instead of preventing it.
        self.assertNotIn("whitelist", self.crowdsec["config"].get("scenarios", {}))
        self.assertNotIn("whitelist", self.crowdsec["config"].get("parsers", {}))

    def test_the_application_deploys_into_security_and_has_no_auto_sync(self):
        app = yaml.safe_load(APP.read_text())
        self.assertEqual(app["spec"]["destination"]["namespace"], "security")
        self.assertEqual(app["spec"]["source"]["path"], "cluster/helm/security/crowdsec")
        self.assertNotIn("automated", yaml.safe_dump(app["spec"]["syncPolicy"]))

    def test_the_lan_service_is_the_only_new_port(self):
        template = (CHART / "templates/lapi-lan-service.yaml").read_text()
        self.assertIn("type: NodePort", template)
        self.assertIn("nodePort: 30500", template)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run it and watch it fail**

```bash
uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_crowdsec_chart.py
```

Expected: `FileNotFoundError: cluster/helm/security/crowdsec`.

- [ ] **Step 3: Create the chart**

`cluster/helm/security/crowdsec/Chart.yaml`:

```yaml
apiVersion: v2
name: crowdsec
description: CrowdSec - attack detection and the decision list the bouncer reads
type: application
version: 0.1.0
appVersion: "1.8.2"
dependencies:
  - name: crowdsec
    version: 0.24.3
    repository: https://crowdsecurity.github.io/helm-charts
```

`cluster/helm/security/crowdsec/values.yaml`:

```yaml
# The upstream chart's values, all of them under the dependency's name.
#
# Two workloads and a separation that is not a preference: the **agent** must
# run on `streaming`, because the file it reads is a hostPath that only exists
# there; the **LAPI** lives on `nas` with the rest of the observability stack,
# because its SQLite is state (decisions and registered bouncer keys) and
# `nas` is where state is backed up.
crowdsec:
  image:
    repository: crowdsecurity/crowdsec
    tag: "v1.8.2"

  # Only the file source below is used. There is no pod-log acquisition, so this
  # key is inert; it is set to the runtime that writes the container logs anyway,
  # so a future acquisition does not silently claim the wrong parser.
  container_runtime: containerd

  config:
    # The agent's listener, and it is not optional: the chart's default
    # liveness, readiness and startup probes all GET `/metrics` on port 6060, so
    # an agent that does not listen there never becomes Ready and dies on the
    # startup probe. The chart ships no listener configuration of its own, only
    # the container port.
    agent_config.yaml.local: |
      prometheus:
        listen_addr: 0.0.0.0
        listen_port: 6060

    # A second copy of the LAN boundary, on purpose, in the one place where it is
    # a safety control rather than a routing decision.
    #
    # The image installs `crowdsecurity/whitelists`, whose ranges (127/8, 10/8,
    # 172.16/12, 192.168/16, ::1) already cover every address below. That copy is
    # not in this repository, it is not a reviewable diff, and a hub change is
    # not a change anyone here approves. This one is, and the test beside it
    # names the ranges.
    #
    # It is a **postoverflow**, so it runs before a decision exists: a
    # whitelisted address is never banned, as opposed to banned-then-excused.
    # The bouncer has no allow-list of its own — its configuration has no such
    # key — so this is the only enforcement point, which is why it is the one
    # thing here with a test that names addresses.
    postoverflows:
      s01-whitelist:
        whitelist-lan.yaml: |
          name: homelab/whitelist-lan
          description: "never create a decision for the LAN, the tunnel, the pods or a node"
          whitelist:
            reason: "the operator's own network"
            ip:
              - "192.168.1.41"
              - "192.168.1.27"
              - "192.168.1.46"
              - "192.168.1.48"
            cidr:
              - "192.168.1.0/24"
              - "10.8.0.0/24"
              - "10.42.0.0/16"
              - "10.43.0.0/16"

    # The chart's default value here holds the auto-registration block that lets
    # the agent register itself with the LAPI. Overriding this key replaces it,
    # so it is repeated verbatim and the metrics listener is added beside it.
    config.yaml.local: |
      api:
        server:
          auto_registration:
            enabled: true
            token: "${REGISTRATION_TOKEN}"
            allowed_ranges:
              - "127.0.0.1/32"
              - "192.168.0.0/16"
              - "10.0.0.0/8"
              - "172.16.0.0/12"
      prometheus:
        # The default binds 127.0.0.1, which the kubelet's scrape cannot reach.
        # Prometheus here scrapes pods by annotation, so the listener has to be
        # on the pod address.
        listen_addr: 0.0.0.0
        listen_port: 6060

  lapi:
    replicas: 1
    nodeSelector:
      kubernetes.io/hostname: nas
    strategy:
      type: Recreate
    # The image's entrypoint registers every `BOUNCER_KEY_<name>` env var at
    # start-up (`cscli bouncers add <name> -k <key>`), which is how the host
    # bouncer gets a key without anyone running `kubectl exec`. The value is a
    # Secret created by hand; nothing here holds it.
    env:
      - name: BOUNCER_KEY_firewall
        valueFrom:
          secretKeyRef:
            name: crowdsec-bouncer-key
            key: api-key
      # CAPI off. Without this the LAPI registers with CrowdSec's central API on
      # its first start and subscribes to the shared blocklist, which is a
      # decision to take after a few weeks of measuring our own traffic — and
      # the only part of this that sends anything to a company. The agent gets
      # the same variable from the chart itself.
      - name: DISABLE_ONLINE_API
        value: "true"
    persistentVolume:
      data:
        enabled: true
        storageClassName: local-path
        size: 1Gi
      config:
        enabled: true
        storageClassName: local-path
        size: 100Mi
    podAnnotations:
      prometheus.io/scrape: "true"
      prometheus.io/port: "6060"
    resources:
      requests:
        cpu: 50m
        memory: 128Mi
      limits:
        memory: 512Mi
    metrics:
      enabled: true

  agent:
    # A DaemonSet would land on rbp2 (ARM64) and on `nas`, where the file does
    # not exist. One Deployment, one node, the one that owns the file.
    isDeployment: true
    replicas: 1
    nodeSelector:
      kubernetes.io/hostname: streaming
    env:
      # `crowdsecurity/traefik` pulls `crowdsecurity/traefik-logs` and
      # `crowdsecurity/base-http-scenarios` (probing, crawl, bad user agent,
      # generic brute force, path traversal). The image itself only ships
      # `crowdsecurity/linux` and the whitelists.
      - name: COLLECTIONS
        value: crowdsecurity/traefik
    additionalAcquisition:
      - source: file
        filenames:
          - /var/log/traefik/access.log
        labels:
          # `program` is what `crowdsecurity/traefik-logs` filters on
          # (`evt.Parsed.program startsWith 'traefik'`); `type` names the source
          # for everything else it does.
          type: traefik
          program: traefik
    extraVolumes:
      - name: traefik-logs
        hostPath:
          path: /var/log/traefik
          type: Directory
    extraVolumeMounts:
      - name: traefik-logs
        mountPath: /var/log/traefik
        readOnly: true
    # No mount of the host's whole /var/log: this agent reads one file, and the
    # default `true` would hand it every log on the machine for nothing.
    hostVarLog: false
    # The image's entrypoint is not a non-root one: it rsyncs
    # /staging/etc/crowdsec into /etc/crowdsec, runs `cscli hub update` and
    # installs the collection there, then writes /var/lib/crowdsec. Forcing a
    # uid would produce a CrashLoop on the first sync. The trade is accepted and
    # narrow: root inside one pod, no host mount but the one file, read-only.
    # If a later image supports it, set podSecurityContext here and prove the
    # collection still installs.
    podSecurityContext: {}
    metrics:
      enabled: true
    podAnnotations:
      prometheus.io/scrape: "true"
      prometheus.io/port: "6060"
    resources:
      requests:
        cpu: 50m
        memory: 128Mi
      limits:
        memory: 512Mi

  # CAPI is off: the first weeks measure our own traffic, and switching it on is
  # one setting to review against what it adds. It is also the only part of this
  # that talks to a company.
  # tls stays disabled: no cert-manager, no mutual TLS between two pods on the
  # same cluster, and a bouncer outside that would need it has a CA to install.
```

`cluster/helm/security/crowdsec/templates/lapi-lan-service.yaml`:

```yaml
# The one new door this plan opens, and it is a LAN one by topology rather than
# by policy: the house router forwards only 80, 443, 25565, 22000 and 21027, so
# a NodePort is unreachable from the internet. The bouncer runs on `streaming`
# as a host service and cannot use the cluster's DNS, and a NodePort is the
# stable address a file in git can name.
#
# `ufw` does not filter it: the CNI's portmap rewrites in NAT and FORWARD before
# INPUT is consulted (ansible/roles/firewall says so). What protects it is the
# bouncer key: `/v1/decisions` needs one, and a bouncer key can only read.
apiVersion: v1
kind: Service
metadata:
  name: {{ .Release.Name }}-lapi-lan
  namespace: {{ .Release.Namespace }}
  labels:
    app.kubernetes.io/name: {{ .Chart.Name }}
spec:
  type: NodePort
  selector:
    k8s-app: {{ .Release.Name }}
    type: lapi
  ports:
    - name: api
      port: 8080
      targetPort: 8080
      nodePort: 30500
      protocol: TCP
```

`cluster/argocd/projects/security/crowdsec.yaml` — **written with CRLF**:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: crowdsec
  namespace: argocd
spec:
  project: default
  destination:
    server: 'https://kubernetes.default.svc'
    namespace: security
  syncPolicy:
    syncOptions:
      - CreateNamespace=true
  source:
    repoURL: 'https://github.com/vyrtualsynthese/homelab.git'
    targetRevision: develop
    path: cluster/helm/security/crowdsec
```

`Chart.lock` is generated by `helm dependency build` and gitignored, so append to `.gitignore`, beside the other wrapper charts:

```
# CrowdSec's wrapper chart renders from a pinned dependency.
cluster/helm/security/*/charts/
cluster/helm/security/*/Chart.lock
```

Then the catalogue row. `hack/check_docs.py` compares `docs/how-it-works/applications.md` row-for-row
against the `Application` files in both directions, so an Application with no row fails the phase gate.
It is a table row in the same shape as the ones around it:

```markdown
| `crowdsec` | `security` | nas | D | attack detection: reads the router's access log on `streaming`, keeps the decisions on `nas`; the banning is a host service, see the CrowdSec runbook |
```

- [ ] **Step 4: Render, lint, test**

```bash
helm dependency build cluster/helm/security/crowdsec
helm template test cluster/helm/security/crowdsec -f cluster/helm/security/crowdsec/values.yaml > /tmp/opencode/crowdsec-render.yaml
grep -c "BOUNCER_KEY_firewall" /tmp/opencode/crowdsec-render.yaml
grep -c "whitelist-lan.yaml" /tmp/opencode/crowdsec-render.yaml
hack/check-charts.sh
uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_crowdsec_chart.py
```

Expected: `1`, `1`, chart gate clean, `12 passed`. `file cluster/argocd/projects/security/crowdsec.yaml` must say `CRLF line terminators`. The first sync's real proof is `kubectl -n security get pods`: two `2/2` or `1/1` pods and no `CrashLoopBackOff`, because a missing listener block shows up as a failing startup probe, not as a log line.

- [ ] **Step 5: Create the bouncer key Secret by hand, then commit**

```bash
# On the operator's machine, never in git. Any 64-character random string; the
# same value goes into ~/.config/secrets/homelab.env as CROWDSEC_BOUNCER_KEY
# for Task 6, and the LAPI registers the bouncer with it at start-up.
openssl rand -hex 32
kubectl -n security create secret generic crowdsec-bouncer-key --from-literal=api-key=<the value>
```

```bash
git add cluster/helm/security/crowdsec cluster/argocd/projects/security/crowdsec.yaml .gitignore docs/how-it-works/applications.md hack/tests/test_crowdsec_chart.py
git commit -m "🚀 feature: CrowdSec in the cluster, with the whitelist before any decision"
```

---

### Task 6: The bouncer on `streaming`, and the signal that it is alive

**Files:**
- Create: `ansible/roles/crowdsec/tasks/main.yml`
- Create: `ansible/roles/crowdsec/templates/bouncer.yaml.j2`
- Create: `ansible/roles/crowdsec/defaults/main.yml`
- Create: `ansible/roles/crowdsec/handlers/main.yml`
- Create: `ansible/roles/crowdsec/meta/main.yml`
- Modify: `ansible/playbooks/site.yml`
- Modify: `cluster/helm/monitoring/prometheus/values.yaml` (one static scrape job)
- Test: `hack/tests/test_ansible_streaming_roles.py` (extends Task 3's file)

**Interfaces:**
- Consumes: `http://192.168.1.46:30500/` and `CROWDSEC_BOUNCER_KEY` from Task 5.
- Produces: `nft` table `crowdsec`, set `crowdsec-blacklists` for IPv4; the scrape target `192.168.1.46:60601` publishing `fw_bouncer_banned_ips{origin,ip_type}`, `fw_bouncer_dropped_packets`, `fw_bouncer_dropped_bytes`.

- [ ] **Step 1: Write the failing test**

Add to `hack/tests/test_ansible_streaming_roles.py`:

```python
class BouncerRoleTests(unittest.TestCase):
    def setUp(self):
        self.role = ANSIBLE / "roles/crowdsec"

    def test_the_role_installs_the_nftables_bouncer_and_not_the_iptables_one(self):
        tasks = (self.role / "tasks/main.yml").read_text()
        self.assertIn("crowdsec-firewall-bouncer-nftables", tasks)
        self.assertNotIn("crowdsec-firewall-bouncer-iptables", tasks)

    def test_the_key_is_required_and_comes_from_the_environment(self):
        tasks = (self.role / "tasks/main.yml").read_text()
        self.assertIn("CROWDSEC_BOUNCER_KEY", tasks)
        self.assertIn("lookup('env', 'CROWDSEC_BOUNCER_KEY')", tasks)
        self.assertIn("no_log: true", tasks)

    def test_the_config_file_carries_the_lan_address_and_no_plaintext_key(self):
        template = (self.role / "templates/bouncer.yaml.j2").read_text()
        self.assertIn("api_url: {{ crowdsec_lapi_url }}", template)
        self.assertIn("api_key: {{ crowdsec_bouncer_key }}", template)
        for key in ("table: crowdsec", "chain: crowdsec-chain", "deny_action: DROP"):
            self.assertIn(key, template)

    def test_its_own_prometheus_endpoint_is_on_and_scrapeable(self):
        template = (self.role / "templates/bouncer.yaml.j2").read_text()
        self.assertIn("enabled: true", template)
        self.assertIn("listen_addr: 0.0.0.0", template)
        self.assertIn("listen_port: 60601", template)
        prometheus = (ROOT / "cluster/helm/monitoring/prometheus/values.yaml").read_text()
        self.assertIn("crowdsec-bouncer", prometheus)
        self.assertIn("192.168.1.46:60601", prometheus)

    def test_site_runs_it_only_on_streaming(self):
        site = (ANSIBLE / "playbooks/site.yml").read_text()
        block = site.split("- role: crowdsec")[1].split("- ")[0]
        self.assertIn("inventory_hostname == 'streaming'", block)
```

- [ ] **Step 2: Run it and watch it fail**

```bash
uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_ansible_streaming_roles.py
```

Expected: `FileNotFoundError: ansible/roles/crowdsec`.

- [ ] **Step 3: Write the role**

`ansible/roles/crowdsec/meta/main.yml`:

```yaml
---
galaxy_info:
  role_name: crowdsec
  author: operator
  description: The CrowdSec firewall bouncer on the node every forwarded packet reaches
  license: MIT
  min_ansible_version: "2.18"
  platforms:
    - name: Ubuntu
      versions:
        - all
```

`ansible/roles/crowdsec/defaults/main.yml`:

```yaml
---
# The LAPI's NodePort on the cluster, reached through `streaming` itself: the
# bouncer is a host service and does not resolve cluster DNS. The address is read
# from the inventory rather than written here — `hack/tests/test_ansible_layer.py`
# fails any host address repeated in a second file — and the NodePort itself is
# fixed by the CrowdSec chart's Service.
crowdsec_lapi_url: "http://{{ hostvars['streaming'].ansible_host }}:30500/"
crowdsec_package: crowdsec-firewall-bouncer-nftables
crowdsec_keyring: /etc/apt/keyrings/crowdsec.asc
# The distro-agnostic repository: `suites` below is `[any]`, and that pair must
# agree — `<repo>/ubuntu` has no `dists/any`. This one also survives a host release
# upgrade, where a codename suite would stop matching.
crowdsec_repo: https://packagecloud.io/crowdsec/crowdsec/any
crowdsec_metrics_port: 60601
# The only host that scrapes. `ufw` filters the bouncer's listener like any other
# INPUT port, so this is an allow rule, not a formality.
crowdsec_scraper: "{{ hostvars['nas'].ansible_host }}"
```

`ansible/roles/crowdsec/tasks/main.yml`:

```yaml
---
# The bouncer is the half of CrowdSec that costs a host change, and it is here
# rather than in the cluster because CrowdSec publishes no container for it. The
# decision list is pulled from the LAPI every ten seconds; a cluster that is down
# leaves the last known bans in place, which is the failure to prefer.

- name: Refuse to run without the bouncer key
  ansible.builtin.assert:
    that:
      - lookup('env', 'CROWDSEC_BOUNCER_KEY') | length >= 32
    fail_msg: >-
      CROWDSEC_BOUNCER_KEY is empty or too short: it must be exactly the value the
      `api-key` holds in the crowdsec-bouncer-key Secret. That key is not issued by
      any command in this repo — the LAPI registers it at start-up from the chart's
      BOUNCER_KEY_firewall environment variable. Mint a new one with
      `openssl rand -hex 32` and set the same value in both places.

# `deb822_repository` is a thin wrapper over python3-debian. Under `--check` this
# task installs nothing, so the module still fails its import; the first `--check`
# on a host without the helper stops at the repository task and the role has to be
# run for real once before check mode shows a plan.
- name: Install the helper deb822_repository needs
  ansible.builtin.apt:
    name: python3-debian
    state: present

- name: Install the repository's signing key
  ansible.builtin.get_url:
    url: https://packagecloud.io/crowdsec/crowdsec/gpgkey
    dest: "{{ crowdsec_keyring }}"
    owner: root
    group: root
    mode: "0644"

- name: Add the CrowdSec repository
  ansible.builtin.deb822_repository:
    name: crowdsec
    types: [deb]
    uris: "{{ crowdsec_repo }}"
    suites: [any]
    components: [main]
    signed_by: "{{ crowdsec_keyring }}"
    state: present

- name: Install the nftables bouncer
  ansible.builtin.apt:
    name: "{{ crowdsec_package }}"
    state: present
    update_cache: true

- name: Configure it
  ansible.builtin.template:
    src: bouncer.yaml.j2
    dest: /etc/crowdsec/bouncers/crowdsec-firewall-bouncer.yaml
    owner: root
    group: root
    mode: "0600"
  no_log: true
  notify: Restart crowdsec-firewall-bouncer

- name: Open the metrics listener to the one host that scrapes it
  community.general.ufw:
    rule: allow
    port: "{{ crowdsec_metrics_port }}"
    proto: tcp
    from_ip: "{{ crowdsec_scraper }}"
    comment: "crowdsec bouncer metrics, scraped by prometheus on nas"

- name: Start it
  ansible.builtin.service:
    name: crowdsec-firewall-bouncer
    state: started
    enabled: true

# This proves the bouncer's own listener is up, not that it has authenticated to
# the LAPI: the exporter is unauthenticated and answers 200. The link to the LAPI
# is proven by the `fw_bouncer_banned_ips` series in Prometheus, which only exists
# once the bouncer has pulled a decision list.
- name: Restart the bouncer before it is probed
  ansible.builtin.meta: flush_handlers

- name: Prove the listener answers
  ansible.builtin.uri:
    url: "http://127.0.0.1:{{ crowdsec_metrics_port }}/metrics"
    status_code: 200
  retries: 6
  delay: 5
  register: crowdsec_metrics
  until: crowdsec_metrics.status == 200
```

`ansible/roles/crowdsec/templates/bouncer.yaml.j2`:

```yaml
# mode: nftables, and not iptables: `nft list sets` is the audit, and the set is
# in the kernel, so a ban outlives the pod that created it.
mode: nftables
update_frequency: 10s
log_mode: file
log_dir: /var/log/
log_level: info
log_compression: true
log_max_size: 100
log_max_backups: 3
log_max_age: 30
api_url: {{ crowdsec_lapi_url }}
api_key: {{ crowdsec_bouncer_key }}
insecure_skip_verify: false
deny_action: DROP
deny_log: false
supported_decisions_types:
  - ban
nftables:
  ipv4:
    enabled: true
    set-only: false
    table: crowdsec
    chain: crowdsec-chain
    priority: -10
  # Nothing on this host listens on IPv6 that the router forwards, and an
  # enabled IPv6 set would create a second place to look for a ban.
  ipv6:
    enabled: false
# `input` covers the host's own services (SSH, the bouncer's listener) and
# `forward` covers the CNI's portmap, so a NodePort or a published container
# port is covered by the same decision.
nftables_hooks:
  - input
  - forward
pf:
  anchor_name: ""
prometheus:
  enabled: true
  listen_addr: 0.0.0.0
  listen_port: {{ crowdsec_metrics_port }}
```

`ansible/roles/crowdsec/handlers/main.yml`:

```yaml
---
- name: Restart crowdsec-firewall-bouncer
  ansible.builtin.service:
    name: crowdsec-firewall-bouncer
    state: restarted
```

`crowdsec_bouncer_key` is defined in `site.yml`'s role entry so the role's own defaults never hold it:

```yaml
    # The ban layer. It needs the LAPI's NodePort, so it is applied after
    # ArgoCD has synced the chart and `cscli bouncers list` shows `firewall`.
    - role: crowdsec
      when: "inventory_hostname == 'streaming'"
      vars:
        crowdsec_bouncer_key: "{{ lookup('env', 'CROWDSEC_BOUNCER_KEY') }}"
```

In `cluster/helm/monitoring/prometheus/values.yaml`, add one entry to the `prometheus.scrapeConfigs` map
(it is a map of job name to job, like the `kubernetes-*` entries beside it, so there is no `job_name`
key):

```yaml
prometheus:
  scrapeConfigs:
    # The bouncer's own numbers: `fw_bouncer_banned_ips{origin,ip_type}` is the
    # only proof that a decision became a dropped packet, and `up` on this target
    # is the only proof the bouncer is running. A static target because the
    # bouncer is a host service on a named address, not a pod — the same shape as
    # the `rbp0` and `rbp1` entries above, keyed by job name.
    crowdsec-bouncer:
      enabled: true
      static_configs:
        - targets:
            - 192.168.1.46:60601
```

- [ ] **Step 4: Lint, test, then apply with the operator**

```bash
cd ansible && ansible-lint roles/crowdsec
cd .. && uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_ansible_streaming_roles.py
helm template test cluster/helm/monitoring/prometheus -f cluster/helm/monitoring/prometheus/values.yaml | grep -c "crowdsec-bouncer"
```

Expected: lint clean, `9 passed`, `1`.

Then the operator, on `streaming`, one host at a time. The first run is real, not `--check`: on a
host without `python3-debian` the dry run stops at the repository task by design, so check mode only
shows a plan after the role has run once.

```bash
export CROWDSEC_BOUNCER_KEY=...   # the value in ~/.config/secrets/homelab.env
cd ansible && ansible-playbook playbooks/site.yml --limit streaming
cd ansible && ansible-playbook playbooks/site.yml --limit streaming --check
# and the proof that it is running:
systemctl is-active crowdsec-firewall-bouncer
curl -s http://127.0.0.1:60601/metrics | grep fw_bouncer_banned_ips
```

Expected: `active`, and the metric present with `0` until something is banned.

- [ ] **Step 5: Commit**

```bash
git add ansible/roles/crowdsec ansible/playbooks/site.yml \
        cluster/helm/monitoring/prometheus/values.yaml hack/tests/test_ansible_streaming_roles.py
git commit -m "🚀 feature: ban attacking addresses at the firewall on streaming"
```

---

### Task 7: The runbook, and the proofs only the operator can run

**Files:**
- Create: `docs/runbooks/crowdsec.md`
- Modify: `docs/runbooks/README.md` (one row on the shelf)
- Modify: `mkdocs.yml`

**Interfaces:**
- Consumes: Tasks 5 and 6.
- Produces: the page that holds the ban/unban commands, the rollback, and the four proofs. Every later task that says "a ban was observed" cites it.

- [ ] **Step 1: Write the runbook**

`docs/runbooks/crowdsec.md`, with these sections and these exact commands (they are the ones Task 14's `SecurityStackBlind` and the dashboard assume):

```markdown
# CrowdSec: reading the bans, and undoing them

> **In one line.** The agent reads `/var/log/traefik/access.log` on `streaming`, the LAPI holds the
> decisions on `nas`, and the firewall bouncer writes them into `nftables` on `streaming` so a ban
> covers every forwarded port.

## What is where

| Piece | Where | How to look |
|---|---|---|
| Agent | Deployment `crowdsec-agent`, node `streaming` | `kubectl -n security logs deploy/crowdsec-agent` |
| LAPI | Deployment `crowdsec-lapi`, node `nas` | `kubectl -n security logs deploy/crowdsec-lapi` |
| Decisions | LAPI's SQLite, `local-path` claim on `nas` | `kubectl -n security exec deploy/crowdsec-lapi -- cscli decisions list` |
| Bans, as the kernel has them | `nftables` on `streaming`, table `crowdsec` | `nft list set ip crowdsec crowdsec-blacklists` |
| Ban count, and the bouncer's liveness | the bouncer's own listener | `curl -s http://127.0.0.1:60601/metrics \| grep fw_bouncer_banned_ips` |

`kubectl exec` needs the admin kubeconfig; the agent's shells are read-only and answer `403`. The
`cscli` calls below are the operator's.

## The four proofs, run once when this was built

1. **A decision is created.** From an address outside the LAN — a phone on mobile data, against
   `https://ashuvidz.com` — request ten paths that do not exist, inside about ten seconds
   (`for i in $(seq 10); do curl -so /dev/null -w '%{http_code} ' https://ashuvidz.com/no-such-$i; done`).
   `crowdsecurity/http-probing` is five 404s with a ten-second leak, so the burst crosses it.
   `cscli decisions list` names the phone's address with that scenario. Undo it afterwards with the
   command below, or the phone stays refused for the decision's four hours.
2. **A decision is enforced.** `nft list set ip crowdsec crowdsec-blacklists` holds the address, the
   phone's browser can no longer open the same URL, and the same URL still answers from a LAN
   machine. That pair is what proves the ban is the firewall's and not Traefik's.
3. **A whitelisted address is never decided.** The same burst from `192.168.1.0/24` creates **no**
   decision at all — the whitelist is a postoverflow, so nothing is created to excuse later. This is
   the one proof that costs nothing to run and the one that matters most.
4. **A restart keeps the bans.** Restart the agent; the `nft` set is unchanged, because the set lives
   in the kernel and the bouncer, not the agent, owns it. Restart the bouncer too: it re-reads the
   LAPI's decisions and rebuilds the same set, which is the half a naive `nft` script would lose.

## Undo a ban

```bash
kubectl -n security exec deploy/crowdsec-lapi -- cscli decisions delete --ip <address>
```

A false positive is the cost of a detector: FileBrowser share links are opened from outside by
people with no account, so a visitor who hammers dead links can trip `http-probing`.

## Turn the whole layer off

```bash
sudo systemctl stop crowdsec-firewall-bouncer
sudo nft flush table ip crowdsec
sudo systemctl disable crowdsec-firewall-bouncer   # only if the chart is being removed too
```

The door is then exactly as it was: nothing is dropped, the router still serves, and the log keeps
being written. Re-enable by starting the service again — the bouncer re-reads the current decisions.

## When a rule says the stack is blind

`SecurityStackBlind` fires when no new line reaches the log, when the agent has not parsed one, when
the LAPI is unreachable, or when the bouncer's listener is down. In order:

1. `kubectl -n security get pods` — an `Init:0/1` agent is `wait-for-lapi`, so check the LAPI first.
2. `kubectl -n security logs deploy/crowdsec-agent | grep -i 'file\|parse\|acquis'` — a
   parser that matches nothing says so once and then stays quiet.
3. `tail -c 200 /var/log/traefik/access.log` on `streaming` — no recent timestamp means the router
   stopped writing, which is a permission or a rotation problem, not a detector problem.
4. `systemctl status crowdsec-firewall-bouncer` and the `/metrics` curl above.
```

- [ ] **Step 2: Add the page to the nav and to the shelf**

In `mkdocs.yml`, add under the runbooks section, beside `traefik-release.md`:

```yaml
      - Banning an attacking address: runbooks/crowdsec.md
```

`docs/runbooks/README.md` calls itself the shelf, and a runbook that is not on it is a page nothing
points at. Add a row to its table, in the Running it tab (the shelf's own routing rule keeps Security
for identity and certificates), with the honest status:

```markdown
| [`crowdsec.md`](crowdsec.md) | Running it | pending — the four proofs have not been run | Chatty addresses are being refused at the firewall, a legit visitor was banned, or `SecurityStackBlind` fired. |
```

- [ ] **Step 3: Run the docs checks**

```bash
hack/check-docs.sh --base origin/develop
```

Expected: no error. A runbook that is not in the nav fails this check, which is why Step 2 is part of the task.

- [ ] **Step 4: Run the four proofs with the operator, and record the outcome**

Each proof's command and observed output goes into the page, below the list, as a dated line — the way `docs/runbooks/restore.md` records its drills — and the shelf row's status becomes `proved <date>`. A proof that fails becomes a fix in Tasks 1–6, not a footnote here.

- [ ] **Step 5: Commit**

```bash
git add docs/runbooks/crowdsec.md docs/runbooks/README.md mkdocs.yml
git commit -m "📝 docs: the CrowdSec runbook, with the four proofs and the rollback"
```

---

**Phase 1 gate, before Phase 2 starts.** The four proofs in the runbook are the acceptance, not a
green test run:

```bash
uv run --with-requirements hack/tests/requirements.txt python -m pytest -q \
  hack/tests/test_crowdsec_chart.py hack/tests/test_ansible_streaming_roles.py
hack/check-charts.sh
hack/check-app-version.sh
hack/check-docs.sh --base origin/develop
```

On the cluster and on `streaming`: `kubectl -n security get pods` shows both pods Ready,
`cscli decisions list` answers without an error, `nft list table ip crowdsec` exists, and
`up{job="crowdsec-bouncer"}` is `1` in Prometheus. A phase that ends with a missing scrape target is a
ban layer nobody can see, which is what Phase 4's rules exist to catch — and catching it here is
cheaper.

---

## Phase 2 — posture, and the hosts

### Task 8: Reconcile what renders with what runs, and write the constants

**Files:**
- Modify: `docs/reference/routes.md`
- Create: `cluster/helm/security/security-exporter/values.yaml` (the `expected:` block only — the chart arrives in Task 9)
- Test: `hack/tests/test_security_exporter.py` (`ReconciledConstantsTests`)

**Interfaces:**
- Produces: three integers — live `IngressRoute` count, public host count, privileged workload count — and the explanation of the four routes that render but do not exist live. Tasks 9 and 14 read them.
- Consumes: nothing.

- [ ] **Step 1: Measure both sides**

```bash
# What the charts render, from the repository
hack/check-exposure.sh 2>&1 | tail -5
for c in $(find cluster/helm -name Chart.yaml | xargs -n1 dirname); do
  helm template test "$c" -f "$c/values.yaml" 2>/dev/null | grep -c '^kind: IngressRoute'
done | awk '{s+=$1} END {print "rendered:", s}'

# What the cluster has, through the read-only MCP ServiceAccount
kubectl get ingressroute -A --no-headers | wc -l
kubectl get ingressroute -A -o json | jq -r '.items[].metadata.namespace + "/" + .metadata.name' | sort > /tmp/opencode/live-routes.txt
```

Expected today: 77 rendered, 73 live. Dump the rendered names the same way and `comm -3` them. Each difference is one of four causes: a chart not yet synced to `develop`, an Application whose `path` holds no chart, a retired app whose Application still exists, or a route in an Application that is not enabled. **Write down which, for each one, in `docs/reference/routes.md`** — that sentence is the deliverable, not the number.

- [ ] **Step 2: Write the failing test**

```python
# hack/tests/test_security_exporter.py
"""The security exporter's values, and the one tie that keeps them honest.

`hack/check_exposure.py` owns the definition of a public host. The chart needs
the same list to count what answers the internet, and a copy that drifts is a
posture panel that says the door is shut while a route answers the world. This
test reads both and fails on a difference.
"""

import pathlib
import sys
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "hack"))

import check_exposure  # noqa: E402

VALUES = ROOT / "cluster/helm/security/security-exporter/values.yaml"


class ReconciledConstantsTests(unittest.TestCase):
    def setUp(self):
        self.values = yaml.safe_load(VALUES.read_text())["securityExporter"]

    def test_the_public_host_list_is_the_checker_s_own(self):
        self.assertEqual(set(self.values["publicHosts"]), set(check_exposure.PUBLIC_HOSTS))

    def test_the_expected_counts_are_present_and_explained(self):
        expected = self.values["expected"]
        self.assertEqual(expected["ingressRoutes"], 73)
        self.assertEqual(expected["publicHosts"], 9)
        self.assertEqual(expected["privilegedWorkloads"], 4)
        # Every number is a claim about the cluster today; the note says when it
        # was read, so a stale constant is visible rather than plausible.
        self.assertIn("2026-11-25", self.values["expectedNote"])


class WhitelistTests(unittest.TestCase):
    def test_the_exporter_carries_no_network_range_at_all(self):
        # The exporter counts; it never decides. A cidr in its values would be a
        # second place a policy could live, and the first place to drift.
        text = VALUES.read_text()
        self.assertNotIn("cidr", text)
        self.assertNotIn("192.168", text)
```

- [ ] **Step 3: Run it and watch it fail**

```bash
uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_security_exporter.py
```

Expected: `FileNotFoundError` on the values file.

- [ ] **Step 4: Write the values block**

`cluster/helm/security/security-exporter/values.yaml` starts with the expectations and nothing else (Task 9 completes it):

```yaml
# The numbers the posture families are compared against. They are claims about
# the cluster on a date, not a policy: a change here is a change to what the
# `SecurityExposureDrift` rule treats as normal, so it moves only with the route
# it describes, and `expectedNote` says when the live side was last read.
#
# Read on 2026-11-25 through the read-only MCP ServiceAccount: 73 IngressRoute
# objects live against 77 rendered (the four are named in
# docs/reference/routes.md), 9 public hosts, 4 pods with `privileged: true`.
securityExporter:
  expected:
    ingressRoutes: 73
    publicHosts: 9
    privilegedWorkloads: 4
  expectedNote: "read 2026-11-25 from the live cluster; see docs/reference/routes.md"
  # The same list as `PUBLIC_HOSTS` in hack/check_exposure.py, which owns it.
  # test_security_exporter.py fails the moment the two differ, because a copy in
  # a chart is how a posture panel starts lying.
  publicHosts:
    - plex.kub.home.ashudev.com
    - seerr.kub.home.ashudev.com
    - ashuvidz.com
    - www.ashuvidz.com
    - ashudev.com
    - www.ashudev.com
    - dashy.ashudev.com
    - authentik.kub.home.ashudev.com
    - filebrowser.kub.home.ashudev.com
```

- [ ] **Step 5: Run the tests and commit**

```bash
uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_security_exporter.py
hack/check-docs.sh --base origin/develop
```

Expected: `3 passed`, docs clean. If the live count is not 73 on the day this runs, write the number that is true and say why in `expectedNote` — a constant copied from this plan without measuring is exactly the failure this task exists to prevent.

```bash
git add cluster/helm/security/security-exporter/values.yaml docs/reference/routes.md hack/tests/test_security_exporter.py
git commit -m "🔧 chore: reconcile the rendered routes with the live ones and record the counts"
```

---

### Task 9: The exporter, and the four numbers that say the door is still shut

**Files:**
- Create: `cluster/helm/security/security-exporter/Chart.yaml`
- Create: `cluster/helm/security/security-exporter/values.yaml` (completes Task 8's block)
- Create: `cluster/helm/security/security-exporter/templates/{deployment,service,rbac,serviceaccount}.yaml`
- Create: `cluster/helm/security/security-exporter/files/exporter.py`
- Create: `cluster/argocd/projects/security/security-exporter.yaml`
- Modify: `docs/how-it-works/applications.md` (one catalogue row — `hack/check-docs.sh` fails without it)
- Test: `hack/tests/test_security_exporter.py` (`PostureTests`)

**Interfaces:**
- Consumes: the expectations from Task 8.
- Produces: `:9105/metrics` with `homelab_security_ingress_routes`, `homelab_security_ungated_routes`, `homelab_security_public_hosts`, `homelab_security_privileged_workloads`, plus `homelab_security_exporter_last_success_timestamp_seconds` for the silence rule. Task 10 adds the authentik families to the same process; Task 13 reads the rest.

- [ ] **Step 1: Write the failing test**

```python
class PostureTests(unittest.TestCase):
    """The counting is pure, so it is tested without a cluster."""

    def load(self):
        import importlib.util
        path = ROOT / "cluster/helm/security/security-exporter/files/exporter.py"
        spec = importlib.util.spec_from_file_location("security_exporter", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_each_route_is_counted_against_the_gate_and_the_public_list(self):
        exporter = self.load()
        public = {"plex.kub.home.ashudev.com", "filebrowser.kub.home.ashudev.com"}
        documents = [
            # A public host whose only middleware is the header set: intended,
            # because the app authenticates itself.
            {"metadata": {"name": "filebrowser"},
             "spec": {"routes": [{"match": "Host(`filebrowser.kub.home.ashudev.com`)",
                                  "middlewares": [{"name": "security", "namespace": "kube-system"}]}]}},
            # An internal host with its LAN gate: the normal case.
            {"metadata": {"name": "sonarr"},
             "spec": {"routes": [{"match": "Host(`sonarr.kub.local`)",
                                  "middlewares": [{"name": "lan-only", "namespace": "kube-system"}]}]}},
            # An internal host with nothing at all: this is the finding.
            {"metadata": {"name": "leak"},
             "spec": {"routes": [{"match": "Host(`leak.kub.local`)", "middlewares": []}]}},
            # A host nobody put on the public list, with no gate: also a finding,
            # because it answers the internet the same way the nine do.
            {"metadata": {"name": "surprise"},
             "spec": {"routes": [{"match": "Host(`new.kub.home.ashudev.com`)", "middlewares": []}]}},
        ]
        posture = exporter.posture(documents, public)
        self.assertEqual(posture.routes, 4)
        self.assertEqual(posture.ungated, 2)   # leak + surprise
        self.assertEqual(posture.public, 2)    # the listed one + the surprise

    def test_one_ingressroute_with_two_hosts_counts_both(self):
        exporter = self.load()
        documents = [{"spec": {"routes": [
            {"match": "Host(`a.kub.local`) || Host(`b.kub.local`)", "middlewares": []}]}}]
        self.assertEqual(exporter.posture(documents, set()).routes, 2)

    def test_the_gate_is_a_middleware_name_not_a_route_shape(self):
        exporter = self.load()
        # Traefik's CRD carries `middlewares` as {name, namespace} objects; the
        # render of a chart can also carry the legacy `namespace-name@kubernetescrd`
        # string. Both name the same middleware.
        self.assertTrue(exporter.gated({"spec": {"routes": [
            {"middlewares": [{"name": "lan-only", "namespace": "kube-system"}]}]}}))
        self.assertTrue(exporter.gated({"spec": {"routes": [
            {"middlewares": ["kube-system-lan-only@kubernetescrd"]}]}}))
        self.assertFalse(exporter.gated({"spec": {"routes": [
            {"middlewares": [{"name": "security", "namespace": "kube-system"}]}]}}))
```

- [ ] **Step 2: Run it and watch it fail**

```bash
uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_security_exporter.py::PostureTests
```

Expected: `FileNotFoundError` on `exporter.py`.

- [ ] **Step 3: Write the exporter**

`cluster/helm/security/security-exporter/files/exporter.py` — stdlib only, no pip, mirroring `alert-relay/files/relay.py`:

```python
#!/usr/bin/env python3
"""Posture and identity in one /metrics page.

Two rules shaped it. Nothing here may put a visitor's address in a Prometheus
label: Prometheus answers unauthenticated on the LAN, so the per-address views
live in the gated log store instead. And every number is either read from the
cluster under a read-only ServiceAccount or compared against a value this
repository already states — no second policy, and the one copied list has a test
that fails the moment it disagrees with `hack/check_exposure.py`.
"""

import json
import os
import ssl
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import NamedTuple

PUBLIC_HOSTS = {h.strip() for h in os.environ.get("PUBLIC_HOSTS", "").split(",") if h.strip()}
INGRESS_ROUTE_URL = "/apis/traefik.io/v1alpha1/ingressroutes"
PODS_URL = "/api/v1/pods"
LAN_GATE = "lan-only"
SA = "/var/run/secrets/kubernetes.io/serviceaccount"
PORT = int(os.environ.get("PORT", "9105"))
INTERVAL = int(os.environ.get("INTERVAL_SECONDS", "60"))


class Posture(NamedTuple):
    routes: int
    ungated: int
    public: int


def _middleware_names(document):
    """Every middleware name an IngressRoute's routes reference, normalised.

    The CRD carries `{name, namespace}` objects; a rendered chart can also carry
    the legacy `namespace-name@kubernetescrd` string. Both name the same object,
    so both are reduced to the bare name.
    """
    names = []
    for route in (document.get("spec") or {}).get("routes") or []:
        for middleware in route.get("middlewares") or []:
            name = middleware if isinstance(middleware, str) else middleware.get("name", "")
            names.append(name.split("@")[0].replace("kube-system-", ""))
    return names


def gated(document) -> bool:
    """True when any route in an IngressRoute carries a middleware named `lan-only`."""
    return LAN_GATE in _middleware_names(document)


def hosts_of(document) -> list[str]:
    """Every `Host()` a document matches. One IngressRoute can carry several."""
    out = []
    for route in (document.get("spec") or {}).get("routes") or []:
        match = route.get("match") or ""
        for chunk in match.split("Host(`")[1:]:
            out.append(chunk.split("`")[0])
    return out


def posture(documents, public: set[str]) -> Posture:
    """Count routes, the ungated ones, and the ones that answer the internet.

    A route is ungated when its IngressRoute carries no `lan-only` and its host is
    not on the public list. A host nobody listed counts as public *and* ungated:
    it answers the internet through the same router as the nine, which is the
    finding this family exists for.
    """
    total = published = ungated = 0
    for document in documents:
        gate = gated(document)
        for host in hosts_of(document):
            total += 1
            if host in public:
                published += 1
            elif not gate:
                ungated += 1
    return Posture(total, ungated, published)
```

Add the fetch, the privileged count and the HTTP server in the same file (Step 4 writes them into the plan's expected shape; the implementer completes `fetch()`, `privileged()`, `render()` and `main()` against the two interfaces above: `fetch(path)` → parsed JSON over the in-cluster CA and token, `privileged(pods)` → count of containers with `securityContext.privileged`, `render(state)` → the Prometheus text).

The families, exactly:

```
# HELP homelab_security_ingress_routes Live IngressRoute objects.
# TYPE homelab_security_ingress_routes gauge
homelab_security_ingress_routes 73
# HELP homelab_security_ungated_routes Live routes with no LAN gate and a host that is not public.
# TYPE homelab_security_ungated_routes gauge
homelab_security_ungated_routes 0
# HELP homelab_security_public_hosts Hosts that answer the internet.
# TYPE homelab_security_public_hosts gauge
homelab_security_public_hosts 9
# HELP homelab_security_privileged_workloads Running containers that declare privileged: true.
# TYPE homelab_security_privileged_workloads gauge
homelab_security_privileged_workloads 4
# HELP homelab_security_exporter_last_success_timestamp_seconds Unix time of the last successful poll.
# TYPE homelab_security_exporter_last_success_timestamp_seconds gauge
homelab_security_exporter_last_success_timestamp_seconds 1795000000
```

- [ ] **Step 4: Write the chart**

`cluster/helm/security/security-exporter/Chart.yaml`:

```yaml
apiVersion: v2
name: security-exporter
description: Posture and identity numbers for the security dashboard
type: application
version: 0.1.0
appVersion: "3.13.7"
```

`values.yaml` completes Task 8's block — **edit that file, do not overwrite it**: it already holds the
`expected:` block, and Task 8's test reads it back. Add:

```yaml
  image:
    repository: python
    tag: 3.13.7-alpine

  replicaCount: 1
  nodeSelector:
    kubernetes.io/hostname: nas
  service:
    port: 9105
  # 30 MiB was the design's estimate; the request is the relay's, and the limit
  # leaves room for one large route list.
  resources:
    requests:
      cpu: 10m
      memory: 32Mi
    limits:
      memory: 128Mi
  intervalSeconds: 60
```

`templates/rbac.yaml` — the read-only ServiceAccount, the same shape as `opencode-mcp`:

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: {{ .Release.Name }}-read
rules:
  # IngressRoutes carry the middleware references the posture count reads.
  - apiGroups: ["traefik.io"]
    resources: ["ingressroutes"]
    verbs: ["get", "list"]
  # Pods, for `privileged: true`. No pods/log, no secrets, no exec: this account
  # reads two lists and nothing else, and `kubectl auth can-i --list` as it is an
  # acceptance test in the task's verification.
  - apiGroups: [""]
    resources: ["pods"]
    verbs: ["get", "list"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: {{ .Release.Name }}-read
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: {{ .Release.Name }}-read
subjects:
  - kind: ServiceAccount
    name: {{ .Release.Name }}
    namespace: {{ .Release.Namespace }}
```

`templates/deployment.yaml` follows `alert-relay/templates/deployment.yaml` — `checksum/config`, `readOnlyRootFilesystem: true`, an `emptyDir` at `/tmp`, `runAsUser: 10001`, `prometheus.io/scrape: "true"` and `prometheus.io/port: "9105"` on the pod, `PUBLIC_HOSTS` from the values, and the code from a ConfigMap at `/app`.

`cluster/argocd/projects/security/security-exporter.yaml` — the same shape as Task 5's Application, CRLF, namespace `security`, path `cluster/helm/security/security-exporter`.

Then the catalogue row, the same obligation Task 5 carries: `hack/check_docs.py` compares
`docs/how-it-works/applications.md` row-for-row against the Application files in both directions, so a
second Application with no row fails the docs check just as the first one did.

```markdown
| `security-exporter` | `security` | nas | D | posture numbers for the security dashboard: live route count, ungated routes, public hosts, privileged workloads, sign-in outcomes |
```

- [ ] **Step 5: Run and commit**

```bash
helm template test cluster/helm/security/security-exporter -f cluster/helm/security/security-exporter/values.yaml >/dev/null
uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_security_exporter.py
hack/check-app-version.sh
```

Expected: render clean, `6 passed`, app-version check clean (`3.13.7-alpine` is app version `3.13.7`).

After the operator syncs the Application:

```bash
kubectl -n security auth can-i --list --as=system:serviceaccount:security:security-exporter
curl -s http://security-exporter.security.svc:9105/metrics | head -20
```

Expected: only `get`/`list` on `ingressroutes` and `pods`, and the four families with the values Task 8 measured. A mismatch is Task 8's constant being wrong, not the exporter.

```bash
git add cluster/helm/security/security-exporter cluster/argocd/projects/security/security-exporter.yaml docs/how-it-works/applications.md hack/tests/test_security_exporter.py
git commit -m "🚀 feature: an exporter that says whether the door is still shut"
```

---

### Task 10: The sign-in page, counted

**Files:**
- Modify: `cluster/helm/security/security-exporter/files/exporter.py`
- Modify: `cluster/helm/security/security-exporter/values.yaml`
- Modify: `cluster/helm/security/security-exporter/templates/deployment.yaml` (the token env)
- Test: `hack/tests/test_security_exporter.py` (`AuthentikTests`)

**Interfaces:**
- Consumes: `AUTHENTIK_TOKEN` from `~/.config/secrets/homelab.env`, put into a Secret by hand.
- Produces: `homelab_authentik_signins_15m{outcome}` and `homelab_authentik_signins_24h{outcome}`, `outcome` ∈ `success|failure|other`. Task 14's `AuthentikSignInFailures` reads the first.

- [ ] **Step 1: Write the failing test**

```python
class AuthentikTests(unittest.TestCase):
    """The window arithmetic, which is where a paginated API lies.

    `/api/v3/events/events/` returns a page. A burst is counted only if the
    request asks for the whole window in one page and the client keeps asking
    until the window is exhausted; a test with a fixed fixture proves the
    counting, not the transport.
    """

    def load(self):
        import importlib.util
        path = ROOT / "cluster/helm/security/security-exporter/files/exporter.py"
        spec = importlib.util.spec_from_file_location("security_exporter", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_outcomes_are_bucketed_the_way_the_rule_reads_them(self):
        exporter = self.load()
        events = [
            {"action": "login_failed"},
            {"action": "login_failed"},
            {"action": "login"},
            {"action": "logout"},
        ]
        counts = exporter.count_outcomes(events)
        assert counts["failure"] == 2
        assert counts["success"] == 1
        assert counts["other"] == 1

    def test_the_request_asks_for_the_whole_window_and_the_whole_page(self):
        exporter = self.load()
        url = exporter.events_url(datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc), 15)
        assert "page_size=1000" in url
        assert "created__gte=2026-11-25T11%3A45" in url or "created__gte=2026-11-25T11:45" in url
        assert "ordering=-created" in url
```

- [ ] **Step 2: Run it and watch it fail**

```bash
uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_security_exporter.py::AuthentikTests
```

Expected: `AttributeError: module has no attribute 'count_outcomes'`.

- [ ] **Step 3: Add the two functions and the poll**

```python
AUTHENTIK_URL = os.environ.get("AUTHENTIK_URL", "https://authentik.kub.home.ashudev.com")


def count_outcomes(events):
    counts = {"success": 0, "failure": 0, "other": 0}
    for event in events:
        action = event.get("action") or ""
        if action == "login_failed":
            counts["failure"] += 1
        elif action in ("login", "login_success"):
            counts["success"] += 1
        else:
            counts["other"] += 1
    return counts


def events_url(now, minutes, page=None):
    since = (now - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%S")
    return (f"{AUTHENTIK_URL}/api/v3/events/events/"
            f"?page_size=1000&ordering=-created&created__gte={urllib.parse.quote(since)}")
```

`fetch_authentik(token, now, minutes, cap=…)` walks pages until the window is exhausted, with a hard
cap per call (20 pages for the fifteen-minute window, 200 for the twenty-four-hour one), and returns
the events. Three measured properties of that endpoint shape it:

- `page_size` is capped at **100** by the server whatever the request asks, so a busy day is several
  pages and one answer undercounts. `pagination.next` is an **integer page number** (`2`), not a URL
  to follow.
- There is **no date filter**. The OpenAPI schema offers `action`, `actions`, `brand_name`,
  `client_ip`, the `context_*` fields, `username`, `ordering`, `search` and pagination, and nothing
  else: a `created__gte` parameter is accepted and ignored, and the answer is the whole retention
  period (~8 days on 2026-11-25). The bound stays in the URL because it is the shape of the request,
  and the window is applied client-side on each event's own `created`, the walk stopping at the first
  older event.
- Reaching the page cap before the window is exhausted **raises**: a partial list would publish a
  count that is quietly too small, which reads exactly like a quiet day.
- The two windows are sized apart, because they are not symmetrical. The 15-minute window is what
  `AuthentikSignInFailures` reads, so it is read first and a failure there is fatal. The 24-hour
  window is a report with a cap of 200 pages; when it cannot be walked, it keeps its last value and
  the freshness timestamp stops, so a day large enough to break the wide walk never silences the
  narrow count during the burst that made it large. On a first poll with nothing to carry, the wide
  failure is fatal like any other.

`render()` gains:

```
# HELP homelab_authentik_signins_15m Sign-in attempts in the last 15 minutes, by outcome.
# TYPE homelab_authentik_signins_15m gauge
homelab_authentik_signins_15m{outcome="failure"} 0
homelab_authentik_signins_15m{outcome="success"} 3
homelab_authentik_signins_15m{outcome="other"} 1
# HELP homelab_authentik_signins_24h Sign-in attempts in the last 24 hours, by outcome.
# TYPE homelab_authentik_signins_24h gauge
```

No address is a label: the counts are aggregate, and the per-address view is a Loki query against the gate's own log lines, which the dashboard names in its description.

`values.yaml` gains:

```yaml
  authentik:
    url: https://authentik.kub.home.ashudev.com
    # Name of the Secret holding an authentik API token with read on events,
    # created by hand. The exporter reads events and nothing else.
    secretName: security-exporter-authentik
    secretKey: token
```

and the Deployment maps it to `AUTHENTIK_TOKEN` with `secretKeyRef` and **`optional: true`**: the
Application syncs before the operator creates the Secret, and the pod must start posture-only rather
than sit in `CreateContainerConfigError`. With no token the exporter publishes no sign-in family at
all — never three zeros, which would read as a quiet day. The freshness family covers every
configured source, so a poll counts as successful only when the cluster read *and* the fifteen-minute
authentik read succeed; an outage of either stops the timestamp instead of leaving stale counts
looking live. The twenty-four-hour window is the one exception, and it is deliberate: it is a report,
not the trigger the rule reads, so when it cannot be walked it keeps its last value, the timestamp
still stops, and the fifteen-minute count keeps being measured.

- [ ] **Step 4: Create the Secret and prove one number by hand**

These two commands are the operator's: the Secret is created by hand and never committed.

```bash
kubectl -n security create secret generic security-exporter-authentik --from-literal=token=$AUTHENTIK_TOKEN
# The same count the exporter publishes, from the API, so a "0" is a measurement —
# which it is not if the token or the network is bad, hence -f and jq -e:
SINCE=$(date -u -d '24 hours ago' +%Y-%m-%dT%H:%M:%S)
PAGES=$(curl -sf -H "Authorization: Bearer $AUTHENTIK_TOKEN" \
  "https://authentik.kub.home.ashudev.com/api/v3/events/events/?page_size=100&ordering=-created" \
  | jq -er '.pagination.total_pages') || { echo "cannot read the first page; check the token" >&2; exit 1; }
for p in $(seq 1 "$PAGES"); do
  curl -sf -H "Authorization: Bearer $AUTHENTIK_TOKEN" \
    "https://authentik.kub.home.ashudev.com/api/v3/events/events/?page_size=100&page=$p&ordering=-created";
done | jq -s --arg since "$SINCE" \
  '[.[] | (.results // [])[] | select(.action == "login_failed" and .created >= $since)] | length'
```

Every page, because the server caps a page at 100 events; the window filtered here, because the
endpoint has no date parameter; `-f` and `jq -e` so a bad token or an unreachable host fails loudly
instead of printing `0`. `total_pages` is read first so the loop stops at the last page rather than
collecting `404 Invalid page.` bodies.

Expected: the same number as `homelab_authentik_signins_24h{outcome="failure"}` after one poll, once
the pod has restarted with the token in place. Verified on 2026-11-25: `0`, and exit 1 with a bad
token.

- [ ] **Step 5: Run and commit**

```bash
uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_security_exporter.py
git add cluster/helm/security/security-exporter hack/tests/test_security_exporter.py
git commit -m "🚀 feature: count sign-in outcomes without putting an address in Prometheus"
```

Expected: green. The file held 26 tests after this task: Task 8's three, Task 9's ten, and these.

---

### Task 11: The journal, for SSH and the firewall

**Files:**
- Modify: `cluster/helm/monitoring/fluent-bit/values.yaml`
- Modify: `docs/reference/log-budget.md` (the measured 24 h line count beside the access-log line)
- Test: `hack/tests/test_fluent_bit_security.py` (`JournalTests`)

**Interfaces:**
- Consumes: nothing. Produces the streams `{job="fluent-bit", source="journal", unit="ssh"}` and `{…, unit="kernel"}`, which Task 13's panel and Task 14's two host rules read. Every label is a constant, so nothing depends on a record accessor resolving.

- [ ] **Step 1: Write the failing test**

```python
class JournalTests(unittest.TestCase):
    def test_two_inputs_select_ssh_and_kernel(self):
        inputs = config()["inputs"]
        self.assertEqual(inputs.count("Name systemd"), 2, "one input per selection")
        self.assertIn("Tag host.ssh", inputs)
        self.assertIn("Tag host.kernel", inputs)
        self.assertIn("Systemd_Filter _SYSTEMD_UNIT=ssh.service", inputs)
        self.assertIn("Systemd_Filter _TRANSPORT=kernel", inputs)

    def test_each_input_resumes_rather_than_replaying(self):
        # `DB` lives on an emptyDir, so the position dies with the pod; without
        # Read_From_Tail a restart ships the whole persistent journal again.
        inputs = config()["inputs"]
        self.assertEqual(inputs.count("Read_From_Tail On"), 2)

    def test_each_output_carries_a_static_unit_label(self):
        outputs = config()["outputs"]
        for tag, unit in (("host.ssh", "ssh"), ("host.kernel", "kernel")):
            block = [b for b in outputs.split("[OUTPUT]") if f"Match {tag}" in b]
            self.assertEqual(len(block), 1, tag)
            self.assertIn(f"unit={unit}", block[0])
            self.assertIn("source=journal", block[0])
```

- [ ] **Step 2: Run it and watch it fail**

```bash
uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_fluent_bit_security.py::JournalTests
```

Expected: three failures.

- [ ] **Step 3: Add the two inputs and the two outputs**

Two inputs, not one. The systemd plugin **ANDs filters that name different fields and ORs ones that
name the same field**, so a single input carrying both `_SYSTEMD_UNIT=ssh.service` and
`_TRANSPORT=kernel` selects nothing at all — which is the sort of pipeline that looks configured and
ships zero lines.

```yaml
  # In the DaemonSet's `inputs` string:
      # The host's own journal: `ssh` refusals and the kernel's ufw block lines.
      # Fluent Bit runs as a DaemonSet, so each node reads its own journal and the
      # `_HOSTNAME` field in the line says which. UFW rate-limits its own logging,
      # so a burst is visible as a burst and the exact packet count is not.
      #
      # `ssh.service`, not `sshd`: Ubuntu's OpenSSH server unit is `ssh.service`,
      # and a filter that names the wrong unit ships nothing and says nothing.
      [INPUT]
          Name systemd
          Tag host.ssh
          Path /var/log/journal
          DB /var/lib/fluent-bit/ssh.db
          Read_From_Tail On
          Max_Fields 200
          Systemd_Filter _SYSTEMD_UNIT=ssh.service

      [INPUT]
          Name systemd
          Tag host.kernel
          Path /var/log/journal
          DB /var/lib/fluent-bit/kernel.db
          Read_From_Tail On
          Max_Fields 200
          Systemd_Filter _TRANSPORT=kernel
```

and two `[OUTPUT]` blocks, each with constant labels:

```
      # Constant labels, one per unit, so the rules select on `unit="ssh"` and
      # `unit="kernel"` without depending on a record accessor resolving to the
      # right key. The node is not a label: it is `_HOSTNAME` inside the line,
      # and adding it as a label is a change to make once it is proved to
      # resolve, not a guess to ship.
      [OUTPUT]
          Name loki
          Match host.ssh
          Host loki.monitoring.svc.cluster.local
          Port 3100
          Line_Format json
          Compress gzip
          Retry_Limit no_limits
          Labels job=fluent-bit, source=journal, unit=ssh

      [OUTPUT]
          Name loki
          Match host.kernel
          Host loki.monitoring.svc.cluster.local
          Port 3100
          Line_Format json
          Compress gzip
          Retry_Limit no_limits
          Labels job=fluent-bit, source=journal, unit=kernel
```

The upstream chart already mounts the host's `/var/log` into the DaemonSet, so `/var/log/journal` is
reachable with no new mount. The one thing to add is a writable directory for the position file:

```yaml
fluent-bit:
  extraVolumes:
    - name: fb-journal-db
      emptyDir: {}
  extraVolumeMounts:
    - name: fb-journal-db
      mountPath: /var/lib/fluent-bit
```

`Read_From_Tail On` is what keeps an `emptyDir` from replaying history: the position file dies with the
pod, and without it the new pod starts at the oldest line it is allowed to read — the whole persistent
journal. With it, a restart ships nothing older than the restart.

`Storage=auto` in journald means the journal is persistent only where `/var/log/journal` exists, and
nothing in this repository creates it. Check before relying on the input — `ls -d /var/log/journal` on
each node — and treat a node without it as a defect of the hosts plan's journald role rather than a
detector that is quiet because there is nothing to read.

**Cost, measured rather than guessed:** read the 24 h line count from Loki
(`sum by (unit) (count_over_time({source="journal"}[24h]))`) and record it beside the access-log line
in `docs/reference/log-budget.md`. A pipeline that adds 10 % to the store is fine; one that doubles it
is not, and the number decides — if it doubles, the sshd filter becomes `_COMM=sshd` plus a line filter
on `Failed`, which costs a second iteration rather than a redesign.

- [ ] **Step 4: Render and run**

```bash
helm template test cluster/helm/monitoring/fluent-bit -f cluster/helm/monitoring/fluent-bit/values.yaml >/dev/null
uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests/test_fluent_bit_security.py
```

Expected: `6 passed`. Then, after the sync, both streams return lines:

```bash
curl -skG -H "Authorization: Bearer $GRAFANA_TOKEN" \
  "https://grafana.kub.local/api/datasources/proxy/uid/P8E80F9AEF21F6940/loki/api/v1/query" \
  --data-urlencode 'query=sum by (unit) (count_over_time({source="journal"}[1h]))'
```

A `0` for `unit="ssh"` is the interesting case: it means the filter names a unit this host does not
have, which is silence, not calm. `systemctl list-units --type=service | grep -i ssh` on a node answers
which name is right.

- [ ] **Step 5: Commit**

```bash
git add cluster/helm/monitoring/fluent-bit/values.yaml docs/reference/log-budget.md hack/tests/test_fluent_bit_security.py
git commit -m "🚀 feature: ship the host journal so sshd and ufw blocks are visible"
```

---

**Phase 2 gate, before Phase 3 starts.**

```bash
uv run --with-requirements hack/tests/requirements.txt python -m pytest -q \
  hack/tests/test_security_exporter.py hack/tests/test_fluent_bit_security.py
hack/check-charts.sh
hack/check-app-version.sh
hack/check-docs.sh --base origin/develop
```

The live proof is the exporter's own page, and it is the number Phase 3's posture panel will show:

```bash
curl -s http://security-exporter.security.svc:9105/metrics | grep '^homelab_security'
curl -s http://security-exporter.security.svc:9105/metrics | grep '^homelab_authentik'
```

Every family named in Tasks 9 and 10 appears, with values that match Task 8's measured constants —
`homelab_security_ingress_routes 73`, `homelab_security_ungated_routes 0`,
`homelab_security_public_hosts 9`, `homelab_security_privileged_workloads 4`. A `0` for
`homelab_authentik_signins_24h` when someone signed in today is the exporter reading the wrong window,
not a quiet day.

---

## Phase 3 — the screen

### Task 12: The two numbers the danger state needs, measured before they are set

**Files:**
- Modify: `docs/reference/dashboards.md` (creates the `## Homelab · Security` section with the two measured numbers; Task 13 fills in the panel table and the notes)
- Test: none — the deliverable is a measurement with a command and a date, the way `docs/reference/log-budget.md` records its own

**Interfaces:**
- Consumes: Prometheus's own request counters, which exist today, and (after this branch syncs) Task 2's stream as confirmation.
- Produces: `rejectedSharePct` and `silenceMinutes`, the two constants in Task 13's expression and Task 14's silence rule, plus the section of `docs/reference/dashboards.md` Task 13 completes.

- [ ] **Step 1: Measure the rejected share, by hour, over seven days**

From **Prometheus, not Loki**, because the number that goes into the panel is a Prometheus expression
and a threshold measured on a different source is a threshold nobody can check:

```bash
curl -sG http://prometheus.kub.local/api/v1/query_range \
  --data-urlencode 'query=100 * sum(rate(traefik_router_requests_total{code=~"4.."}[1h])) / sum(rate(traefik_router_requests_total[1h]))' \
  --data-urlencode "start=$(date -d '7 days ago' +%s)" --data-urlencode "end=$(date +%s)" \
  --data-urlencode 'step=3600' | jq -r '.data.result[0].values[] | @tsv'
```

Read the top of the band, not the average: the threshold is what separates a bad hour from a normal
one, and the busiest hour is the one that would page.

- [ ] **Step 2: Measure the longest gap between requests**

The silence rule reads access-log lines, but the *rhythm* that decides its threshold is visible today
in Prometheus — the log's lines and `traefik_router_requests_total`'s increments happen on the same
requests. Measuring from Prometheus means this task does not wait a week for the new stream:

```bash
curl -sG http://prometheus.kub.local/api/v1/query_range \
  --data-urlencode 'query=sum(rate(traefik_router_requests_total[5m]))' \
  --data-urlencode "start=$(date -d '7 days ago' +%s)" --data-urlencode "end=$(date +%s)" \
  --data-urlencode 'step=300' | jq -r '.data.result[0].values[] | @tsv'
```

Read the longest run of consecutive `0` samples: that is the longest gap between requests, in
five-minute steps, and `SecurityStackBlind` must wait longer than it. Three in a row (fifteen quiet
minutes at 04:00) is normal for a house LAN; a ten-minute threshold would page every night.

After this branch has synced, confirm against the stream itself — the two must agree within a bucket,
because a line in the file and an increment of the counter are the same request:

```bash
curl -skG -H "Authorization: Bearer $GRAFANA_TOKEN" \
  "https://grafana.kub.local/api/datasources/proxy/uid/P8E80F9AEF21F6940/loki/api/v1/query" \
  --data-urlencode 'query=min_over_time(sum(count_over_time({job="fluent-bit", container="traefik"}[5m]))[7d:5m])' \
  --data-urlencode "time=$(date +%s)"
```

- [ ] **Step 3: Write the numbers down with their command**

Create the `## Homelab · Security` section in `docs/reference/dashboards.md` with the measurement in
it; Task 13 adds the table row and the two panel notes to the same section, so neither task has to
guess what the other wrote:

```markdown
## Homelab · Security

**Both thresholds were measured, on 2026-11-25.** The rejected share, by hour over seven days from
`traefik_router_requests_total`, moved between `X %` and `Y %`; the danger state's threshold is `Z` —
the top of the observed band plus a margin, not a round number — and it is the same family the panel
queries, so anyone can re-run the check. The longest gap between requests, in five-minute steps over
the same week, was `N` steps and the quietest window held `M` requests, so `SecurityStackBlind` waits
`2N × 5` minutes: a silence rule tighter than the traffic's own rhythm is a rule that pages on a
quiet night.
```

Each `X`, `Y`, `Z`, `N`, `M` is replaced with the measured value, and the commands above are quoted
in the page so the next reader can repeat the measurement rather than trust it.

- [ ] **Step 4: Commit**

```bash
git add docs/reference/dashboards.md
git commit -m "📝 docs: measure the rejected share and the log silence before setting either"
```

---

### Task 13: `Homelab · Security`

**Files:**
- Create: `cluster/helm/monitoring/grafana/export/security/dashboard.json` (written from the API's response, not by hand)
- Create: `cluster/helm/monitoring/grafana/export/security/README.md`
- Modify: `docs/reference/dashboards.md`
- Test: every query executed through the datasource API before the dashboard is saved (Step 3)

**Interfaces:**
- Consumes: Tasks 2, 6, 9, 10, 11, 12.
- Produces: the dashboard `uid: homelab-security` in folder `homelab`, nine panels, and the danger-state expression Task 14's rules are written to agree with.

- [ ] **Step 1: Execute every query before writing a panel**

The nine panels of §6.8, one question each, and every expression run through the datasource proxy
first — a query that errors or returns nothing is a panel that lies. The expressions are the ones
below; the danger state is Step 2's, and it is written once.

| Panel | Datasource | Expression |
|---|---|---|
| Danger state | Prometheus | Step 2 |
| Bans active | Prometheus | `sum(fw_bouncer_banned_ips)` |
| Requests, public hosts, 24 h | Loki | `sum by (RequestHost) (count_over_time({job="fluent-bit", container="traefik"}[24h]))` |
| Rejected share | Prometheus | `100 * sum(rate(traefik_router_requests_total{code=~"4.."}[15m])) / sum(rate(traefik_router_requests_total[15m]))` |
| Top addresses refused | Loki | see below |
| Sign-in outcomes | Prometheus | `homelab_authentik_signins_15m` and `homelab_authentik_signins_24h` |
| Requests by public host and status | Loki | `sum by (RequestHost, DownstreamStatus) (count_over_time({job="fluent-bit", container="traefik"} \| json [15m]))` |
| Host blocks by host and port | Loki | `topk(5, sum by (unit) (count_over_time({source="journal", unit="kernel"} \|~ "UFW BLOCK" [1h])))` and the raw lines, which carry `_HOSTNAME` |
| Posture table | Prometheus | `homelab_security_ingress_routes`, `homelab_security_ungated_routes`, `homelab_security_public_hosts`, `homelab_security_privileged_workloads`, `time() - homelab_security_exporter_last_success_timestamp_seconds` |

The one per-address view on the screen, and it reads the gated log store rather than Prometheus:

```
topk(10, sum by (ClientHost) (count_over_time(
  {job="fluent-bit", container="traefik"} | json
  | DownstreamStatus >= 401 | DownstreamStatus < 500 [24h])))
```

- [ ] **Step 2: Write the danger state, once, as one expression**

The whole expression has to be Prometheus, because a Grafana target reads one datasource. That is
why the rejected share uses Traefik's own counters and not the log: `traefik_router_requests_total`
is the same number, one query, no second datasource.

```promql
# 2 = alerting, 1 = watch, 0 = calm. The panel's description carries this text,
# with the measured threshold written in it.
max(
    (count(ALERTS{alertname=~"Security.*", alertstate="firing"}) > 0) * 2
  or (delta(sum(fw_bouncer_banned_ips)[1h:1m]) > 20) * 2
  or (sum(fw_bouncer_banned_ips) > 0) * 1
  or (100 * sum(rate(traefik_router_requests_total{code=~"4.."}[15m]))
      / sum(rate(traefik_router_requests_total[15m])) > 20) * 1
  or (homelab_authentik_signins_15m{outcome="failure"} > 20) * 1
  or vector(0)
)
```

Three things about it are deliberate. `delta` on a subquery, not `increase`: the bouncer's family is
a gauge, and `increase()` assumes a counter, silently resetting on any drop. `max()` over `or`-ed
terms, not `or` alone, because the terms carry no labels and the first one that matches would win
otherwise. And `vector(0)` last, so a calm night is the number 0 rather than an empty panel.

`REJECTED_SHARE_PCT` and its `20` are Task 12's measured value, written into the panel's description
in the same words — the plan uses 20 as the provisional figure and the measurement replaces it before
the panel is saved. The thresholds are `0/1/2` on a stat panel with three value mappings — Calm, Watch,
Alerting — and no score out of ten.

- [ ] **Step 3: Apply it through the API**

```bash
jq -n --slurpfile d /tmp/opencode/security-dashboard.json \
  '{dashboard: $d[0], folderUid: "homelab", overwrite: true, message: "initial: security"}' \
  | curl -s -X POST -H "Authorization: Bearer $GRAFANA_TOKEN" -H 'Content-Type: application/json' \
      --data @- https://grafana.kub.local/api/dashboards/db | jq .
```

Then read it back and check each panel's query against the datasource API:

```bash
curl -s -H "Authorization: Bearer $GRAFANA_TOKEN" \
  https://grafana.kub.local/api/dashboards/uid/homelab-security | jq -r '.dashboard.panels[] | .title, .targets[].expr'
```

Expected: nine titles, each expression one that ran in Step 1. Force the danger state to each of its three values once (a fake alert, a rule that matches nothing, then reality) and read the screen at 1080p from where it will be watched.

- [ ] **Step 4: Export the copy and describe it**

```bash
mkdir -p cluster/helm/monitoring/grafana/export/security
curl -s -H "Authorization: Bearer $GRAFANA_TOKEN" \
  https://grafana.kub.local/api/dashboards/uid/homelab-security \
  | jq '.dashboard' > cluster/helm/monitoring/grafana/export/security/dashboard.json
```

The `README.md` beside it states what the Traefik export's does: this is a snapshot to read in a pull request and to restore after a wipe, nothing applies it, and the database stays authoritative.

`docs/reference/dashboards.md` gains the twelfth screen **in the section Task 12 created**: its row in
the table
(`| Homelab · Security | \`homelab-security\` | 9 | Danger state, bans, volume, rejected share, top
addresses, sign-in outcomes, host blocks, posture |`), and **the count in the title, the lede and the
"All eleven refresh…" sentence becomes twelve** — a page whose header says eleven while the folder
holds twelve is a page that lies about the same thing the dashboard measures. It also records the two
notes the panels must carry: **Top addresses refused shows who was *answered* `401/403`, not who the
bouncer dropped** (a dropped request never reaches Traefik and has no line), and the exact ban list is
`cscli`. *Sign-in outcomes* is a count without an address, for §6.5's reason.

- [ ] **Step 5: Commit**

```bash
git add cluster/helm/monitoring/grafana/export/security docs/reference/dashboards.md
git commit -m "🚀 feature: the security dashboard, with the danger state derived on the panel"
```

**Phase 3 gate, before Phase 4 starts.** The screen is read at the size it will be watched, and the
danger state is forced to each of its three values once (a fake alert, a rule that matches nothing,
then reality) — the same check Task 13's last step names, repeated because a dashboard edit between
the two can break one panel and leave eight looking right:

```bash
curl -s -H "Authorization: Bearer $GRAFANA_TOKEN" \
  https://grafana.kub.local/api/dashboards/uid/homelab-security | jq -r '.dashboard.title'
hack/check-docs.sh --base origin/develop
```

Expected: `Homelab · Security`, nine panels, and a docs check that finds the twelfth screen in the
nav and the table.

---

## Phase 4 — the rules

### Task 14: Seven rules, each forced to fire once

**Files:**
- Create: `cluster/helm/monitoring/grafana/export/security/rules.json` (from the API's response)
- Modify: `docs/reference/alerts.md`
- Test: `POST /api/v1/eval` per rule, then one delivered message read back

**Interfaces:**
- Consumes: every family from Tasks 5–11 and the thresholds from Task 12.
- Produces: `SecurityBanBurst`, `SecurityAttackSustained`, `AuthentikSignInFailures`, `HostSshBruteForce`, `HostFirewallBlockBurst`, `SecurityExposureDrift`, `SecurityStackBlind`, all with `for: 5m` (except drift's `10m`) in `homelab-core-fast`.

- [ ] **Step 1: Write each rule from the family that exists, not the family imagined**

Four are Prometheus expressions and two read Loki, which a Grafana rule may do. `SecurityStackBlind`
is the one that needs both, and it is written as two queries with a classic condition rather than as
one impossible expression.

Each query below is the *query only*: the threshold is the rule's condition, not a `> 20` glued to the
end of the expression, because a Grafana rule evaluates a condition over a query's result and a
comparison inside the query turns "how many" into "is it above", which the alert's message then cannot
report.

```promql
# SecurityBanBurst — warning, for 5m, Prometheus. Condition: last() > 20.
delta(sum(fw_bouncer_banned_ips)[10m:1m])

# SecurityAttackSustained — critical, for 5m, Loki. Condition: reduce(sum) > 100.
topk(1, sum by (ClientHost) (count_over_time(
  {job="fluent-bit", container="traefik"} | json
  | DownstreamStatus = 401 or DownstreamStatus = 403 or DownstreamStatus = 404 [10m])))

# AuthentikSignInFailures — warning, for 5m, Prometheus. Condition: last() > 20.
homelab_authentik_signins_15m{outcome="failure"}

# HostSshBruteForce — warning, for 5m, Loki. Condition: reduce(sum) > 10.
sum(count_over_time({source="journal", unit="ssh"} |~ "(?i)failed password|invalid user" [10m]))

# HostFirewallBlockBurst — warning, for 5m, Loki. Condition: reduce(sum) > 200.
sum(count_over_time({source="journal", unit="kernel"} |~ "UFW BLOCK" [5m]))

# SecurityExposureDrift — critical, for 10m, Prometheus. Condition: last() > 0.
# Two comparisons, `or`-ed: a route that lost its gate, and a count that moved.
homelab_security_ungated_routes > 0
  or homelab_security_public_hosts != 9
  or homelab_security_privileged_workloads != 4
```

Each rule sets its **no-data behaviour explicitly**, because the two kinds disagree. `SecurityExposureDrift`
and `SecurityAttackSustained` return nothing when nothing is wrong, so their no-data state is **OK**; a
rule that alerts on no data would fire on every quiet night and be muted within a week.
`SecurityStackBlind` is the opposite: its no-data state is **Alerting**, because "the query returned
nothing" is exactly what a dead exporter looks like.

`SecurityStackBlind` is one rule with two targets and one condition, which is how a Grafana rule
spans datasources:

```
A (Prometheus): time() - homelab_security_exporter_last_success_timestamp_seconds > 300
                or absent(homelab_security_exporter_last_success_timestamp_seconds)
                or absent(fw_bouncer_banned_ips)
                or absent(cs_active_decisions)
B (Loki):       sum(count_over_time({job="fluent-bit", container="traefik"}[10m]))
C (classic):    A > 0  ||  B == 0
```

A returns one sample when the exporter is stale or a family has vanished, and nothing when everything
is alive; `C`'s `A > 0` then reads "blind". The three families live on two pods, and both are annotated
for the annotation-based scrape Task 5 sets: `cs_active_decisions` is the LAPI's gauge,
`cs_parser_hits_ok_total` the agent's counter, and `fw_bouncer_banned_ips` comes from Task 6's static
job on `streaming`. A family that is absent from day one is a real finding, not an alert to silence.

Each message carries what identifies the event — `{{ $labels.ClientHost }}` for the address rule,
`{{ $labels.unit }}` for the two host rules, `{{ $value }}` for the number — because a message that
says "security thing happened" is a message the operator opens Grafana for. A Loki rule's message reads
its labels from the stream, and the node is not one of them: the annotation points at the panel whose
raw lines carry `_HOSTNAME`.

**`SecurityStackBlind`'s metric names are confirmed against what Prometheus actually scrapes before the
rule is saved**: `curl -sG http://prometheus.kub.local/api/v1/label/__name__/values |
jq -r '.data[] | select(startswith("cs_"))'` lists every family the scrape carries — a rule that names
one that is not in the list is silent, the exact failure this rule exists to prevent, so the check is
not optional. For a family Prometheus has not scraped yet, read the pod directly after a port-forward
(`kubectl -n security port-forward deploy/crowdsec-lapi 6060:6060`, the operator's command, then
`curl -s http://localhost:6060/metrics | grep '^cs_'`); the NodePort `30500` serves the API on 8080,
not the metrics port.
`cs_active_decisions` is confirmed in CrowdSec's own source (`pkg/metrics/global.go`, the LAPI's
gauge), and `cs_parser_hits_ok_total` is registered beside it — if that one is published by the
agent's pod, prefer it for the "is the parser seeing anything" half, because it moves on every parsed
line while the decision count sits at zero on a quiet night. Write down whichever names the list
actually shows. If the LAPI publishes nothing usable, the
honest fix is the exporter's read-only bouncer key — a decision to take then, not to guess now.

- [ ] **Step 2: Create them through the provisioning API**

```bash
# One POST per rule; the interval belongs to the group, not to the rule.
for f in /tmp/opencode/security-rules/*.json; do
  curl -s -X POST -H "Authorization: Bearer $GRAFANA_TOKEN" -H 'Content-Type: application/json' \
    --data @"$f" https://grafana.kub.local/api/v1/provisioning/alert-rules | jq -r '.uid'
done
# A group PUT replaces the whole object, so read it, change the one field and
# send it back — `{"interval": 60}` alone answers 400 and loses nothing only
# because it never applied.
curl -s -H "Authorization: Bearer $GRAFANA_TOKEN" \
  "https://grafana.kub.local/api/v1/provisioning/folder/homelab/rule-groups/homelab-core-fast" \
  | jq '.interval = 60' \
  | curl -s -X PUT -H "Authorization: Bearer $GRAFANA_TOKEN" -H 'Content-Type: application/json' \
      --data @- "https://grafana.kub.local/api/v1/provisioning/folder/homelab/rule-groups/homelab-core-fast" \
  | jq -r '.title, .interval'
```

- [ ] **Step 3: Force each one, and watch it resolve**

For each rule, `POST /api/v1/eval` with `{condition, data}` copied from its own object — the check the Traefik rules used on 2026-11-21. Then, for the two that can be provoked:

- a burst of ten missing paths from the runbook's outside address creates a decision → `SecurityBanBurst` and `SecurityAttackSustained` fire, with the address in the message;
- **the operator** scales `deploy/crowdsec-agent` to zero (`kubectl -n security scale`, a write this session must not make) → `SecurityStackBlind` fires within five minutes. **This is the one proof that is not an expression**; the rule's whole value is that a dead agent is not a quiet night, and scaling it down is how that is shown. Scale it back and watch the alert resolve.

- [ ] **Step 4: Read one Discord message and one card as the operator receives them**

`discord-homelab` for the warning, and the alert relay's card for the critical. Both are quoted into
`docs/reference/alerts.md`, which gains the seven rules in the table beside the existing 49 **and
changes the "49 rules" count in its lede to 56** — the same lie-in-the-lede problem the dashboards page
had. It also gains one paragraph on why `SecurityStackBlind` is the rule that makes the others
believable: every other rule here reports an event, and this one reports that nobody is watching for
events.

```bash
curl -s -H "Authorization: Bearer $GRAFANA_TOKEN" \
  "https://grafana.kub.local/api/v1/provisioning/folder/homelab/rule-groups/homelab-core-fast" \
  | jq -r '.[] | select(.title | startswith("Security")) | .title, .uid' > /dev/null
```

- [ ] **Step 5: Export the rules and commit**

```bash
curl -s -H "Authorization: Bearer $GRAFANA_TOKEN" \
  https://grafana.kub.local/api/v1/provisioning/alert-rules \
  | jq '[.[] | select(.title | startswith("Security"))]' \
  > cluster/helm/monitoring/grafana/export/security/rules.json
hack/check-docs.sh --base origin/develop
git add cluster/helm/monitoring/grafana/export/security/rules.json docs/reference/alerts.md
git commit -m "🚀 feature: seven security rules, and the one that says the detector is dead"
```

---

## Phase gate: before the pull request

```bash
hack/check-charts.sh
hack/check-app-version.sh
hack/check-exposure.sh
hack/check-docs.sh --base origin/develop
uv run --with-requirements hack/tests/requirements.txt python -m pytest -q hack/tests
cd ansible && ansible-lint && cd ..
```

All six must pass. `check-exposure.sh` is the one that matters most after this work: the LAPI's NodePort is not a Traefik route, so the checker will not see it — the review says why it is safe (the router forwards five ports) rather than pretending the checker covered it.

## What this plan does not do

- **The two edge Pis.** `wg-easy` on `5000/tcp` over plain HTTP, `9902` open, password SSH, no `ufw`. That is the [hosts plan](2026-11-24-hosts-stabilise.md)'s Pi script, and it is the worst remaining hole in this repository. Its card stays open. Their logs reach Loki in a later step, when a Fluent Bit container joins each compose stack.
- **The etcd snapshot schedule.** Card #506, unchanged and unblocked by anything here.
- **Login rate limits and a patch window.** Named in the design's Phase 5, not scheduled here.
- **The stale-certificate family.** The design's `homelab_security_stale_certificates` would need the exporter to read Traefik's certificate store, which is a file with the ACME account in it. Card 543 owns that number instead; the posture panel says so.
- **A WAF.** CrowdSec's AppSec component inspects request bodies and is a decision of its own, with a tutorial budget attached.
- **CAPI.** Off. Turning it on is one value change and a review of what the shared blocklist adds to *this* traffic, which is a measurement, not a task here.
