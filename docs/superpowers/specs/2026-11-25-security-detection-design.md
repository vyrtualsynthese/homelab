# Security detection, response and dashboard — design

Date: 2026-11-25.
Status: **design, nothing built.** Written after the operator asked for three things: a plan to protect
the homelab and the house LAN against external attack, a security dashboard that says how dangerous
things are and what is happening, and alerts when something goes wrong. The operator chose CrowdSec
as the detection engine, with bans applied at the host firewall.
Scope: the internet-facing path (router → Traefik → route), the six hosts' own attack surface, the
identity provider's sign-in page, and the observability that reads all of it. The applications' own
vulnerabilities, the cluster's internal policy and the backup story are referenced, not re-planned.

Vocabulary used below:

- **Bouncer** — CrowdSec's word for the component that enforces a decision. A decision is "this
  address is banned for two hours"; a bouncer is what makes that true at a door.
- **Scenario** — a behaviour rule over parsed log lines ("the same address asked for ten missing
  pages in a minute"), the thing that creates a decision. It is not a regex on one line, which is
  what a fail2ban jail is.
- **LAPI** — CrowdSec's local API: the database of decisions and the endpoint every bouncer reads.
- **CAPI** — CrowdSec's central API: the shared blocklist and the signal sharing. Optional.
- **Access log** — Traefik's per-request record, one JSON line per request.

## 1. Intent

Close what is still open, see an attack while it happens, stop the address that is doing it, and prove
afterwards what it did. The operator's words: "protect my homelab but also my local network against
external attacks", "a security dashboard to track the level of danger and what is happening",
"security alerts for when something is going wrong", and on the enforcement question, "both" — see it
and ban it.

## 2. What is true today

Every line is measured or read from this repository; the evidence is named with each.

### 2.1 The door is mostly shut, and the measurement is one day old

The [security baseline of 2026-11-24](../../security/security-baseline-2026-11-24.md) graded six
boundaries. Two days later the state is:

| Boundary | State today | Evidence |
|---|---|---|
| Internet → Traefik | Holds: nine hostnames answer the world, every other route carries `lan-only` | `hack/check-exposure.sh`; 73 live `IngressRoute` objects read on 2026-11-25 against 77 rendered |
| LAN → control plane | `ufw` active with 20 rules on all four nodes since 2026-11-24 | [the host audit, "After the host layer ran"](../../audit/hosts-2026-11-24.md) |
| LAN → storage exports | Fixed: eight exports, `root_squash`, four client addresses, no `/mnt` | same page |
| Host → container | Four charts declare `privileged: true`; nothing lists them | baseline §5, still open |
| Identity | `%admins NOPASSWD` gone; password SSH off on the four nodes | baseline §6, applied |
| The two edge Pis | **Unchanged:** `wg-easy` admin on `5000/tcp` over plain HTTP, `9902` open, password SSH accepted, no `ufw` | baseline §2 and §6; the hosts plan leaves the Pis to a reflash |
| Recovery | No etcd snapshot schedule; card #506 | baseline §6 |

So the door work that remains is small, known and named: the two Pis, no login rate limit anywhere,
no patch window, no etcd schedule.

### 2.2 Nothing watches for an attack

There is no IDS, no WAF, no bouncer and no fail2ban on any of the six hosts or in the cluster — a
grep over `cluster/`, `ansible/` and `docs/` returns only the pages that say so. The 49 Grafana
rules answer "is a disk full", "is a pod crashing", "is a certificate expiring". Not one of them
answers "is somebody trying to break in".

What exists that can be read:

| Source | What it holds | Read by |
|---|---|---|
| Traefik metrics | requests, status classes, latency, certificates, per router and entry point | Prometheus, `homelab-traefik` |
| Traefik access log | one JSON line per request: hostname, method, status, sizes, durations, router, TLS version | Loki, two panels |
| authentik events | every sign-in attempt with `client_ip` and `user_agent`, kept for a year | the authentik API only |
| ufw | blocked packets, in the kernel log | **nothing**: Fluent Bit tails container logs only |
| Pi-hole | every DNS query the LAN makes | the Pi-hole exporter, counts only |

### 2.3 The access log deliberately has no client address

`cluster/helm/platform/traefik/values.yaml` drops `RequestPath`, `ClientAddr`, `ClientHost`,
`ClientPort` and `ClientUsername`, and drops query parameters and headers at the default. The reason
was the path: FileBrowser's `/share/<hash>` capability links and Plex's `?X-Plex-Token=` must not sit
in Loki for 30 days. That reason does not extend to the client address, and its absence is what stops
any address-level detection: you cannot ban an address the log never records. Measured on
2026-11-22: about 15 MB a day, 769 bytes a line.

The field list is the access-log card's decision (task 544, #410), and `docs/observability-conventions.md`
records the current shape. This design revisits it, and Phase 0 amends both in the same change.

### 2.4 What the operator's own tooling already supports

- Traefik chart 42.4.1 exposes `deployment.additionalVolumes` and `additionalVolumeMounts`, whose own
  comment names "when using a logshipper for access logs" as the case.
- Fluent Bit 5.2.0 runs as a DaemonSet with a single tail input over `/var/log/containers/*.log`; its
  inputs, filters and outputs are plain configuration strings, so a second tail and a key-removal
  filter are a values edit.
- Grafana evaluates every rule and delivers to `discord-homelab`; criticals also open a card through
  the alert relay.
- The restore-drill rule already proves the pattern for a hand-written metric: a rendered ConfigMap
  mounted read-only into the node-exporter pods, served by the textfile collector.
- The authentik API answers `GET /api/v3/events/events/` with `client_ip` and `user_agent` on each
  event; measured 2026-11-25, 294 events, three of them `login_failed`.

## 3. Goals, non-goals, anti-goals

**Goals**

1. The client address of every internet request is recorded next to its status, in a store only an
   Admin can read. The path, the query and the headers never reach that store.
2. An address that behaves like an attacker is banned automatically, and the ban is enforced on
   every forwarded port, not only HTTP.
3. A ban cannot reach the LAN, the WireGuard range, the pods, or the cluster's own addresses.
4. One screen answers: how dangerous is it right now, who is doing it, what did we do, and is the
   door still shut.
5. Every security rule names the thing to do; a silent detector is itself an alert.
6. The remaining door work — the two Pis, login rate limits, the patch window, the etcd schedule —
   is tracked on the board and not re-invented here.

**Non-goals**

- A WAF with request-body inspection. CrowdSec's AppSec component is a later decision; log-based
  scenarios are the first layer.
- Layer-3 flood protection. There is no scrubbing and no spare uplink; the answer to a flood is the
  provider, and that is written down rather than built.
- Replacing the router's configuration or reading it into git.
- Scanning images for vulnerabilities (design exists, deferred) and the Kubernetes audit log (media
  security Phase 5). Both are referenced, neither is planned here.
- A second identity provider, or MFA enforcement — MFA stays optional, the operator's decision.

**Anti-goals (failures even if the mechanism works)**

- Banning a LAN, WireGuard, pod or node address and locking the operator out of their own cluster.
  This is the one failure that costs more than the attack.
- A dashboard that is a wall of counters with no decision on it.
- A ban layer that stopped working three weeks ago and looks healthy, because nothing watched it.
- Reading "no alerts" as "no attack" while the agent is dead.
- Logging a URL, a query string, a token or a header into the 30-day store in the name of detection.
- Treating a CrowdSec decision as a block: a decision is an instruction, and the bouncer is what
  makes it true.

## 4. Constraints

- GitOps: charts plus ArgoCD `Application` objects; pattern D (wrapper chart pinning the upstream
  version); one chart per app; no shared library chart.
- Hosts are converged by Ansible (`ansible/playbooks/site.yml`), one host at a time, and `--check`
  is the drift contract. The Pis are reflashed, not patched, and their compose stack comes from
  `ansible/playbooks/templates/runners/`.
- Secrets never enter git; a chart names a Secret and its key, the value lives in the cluster and the
  password manager.
- Pinned images; EU providers only (CrowdSec is French, which fits; CAPI is optional and switchable).
- Dashboards and rules live in Grafana's database and are changed through its API; the one export
  under `cluster/helm/monitoring/grafana/export/traefik/` is a copy for review, applied by nothing.
- Grafana is the only evaluator and the only deliverer; if `nas` or Grafana dies, nothing is sent.
  Accepted since 2026-11-12, and it is why the detector needs its own heartbeat rule rather than a
  second stack.
- Everything is pinned to a named node; `nas` is the observability host, `streaming` receives every
  forwarded packet and runs Traefik.

## 5. Approaches

The detection engine was the operator's decision; the open question inside it was where the ban
lands. Both were weighed before this document.

### A. CrowdSec, ban at the host firewall on `streaming` — **chosen**

The agent parses the Traefik access log; the LAPI holds decisions; the firewall bouncer writes them
into `nftables` on the one address the router forwards to.

- **Covers every port.** HTTP, Minecraft, Syncthing, and anything forwarded later.
- **One place to look.** `nft list sets` shows what is banned, and a ban outlives the pod that made it
  because the set is in the kernel.
- **Cost:** the bouncer is a host package (`crowdsec-firewall-bouncer-nftables`), not a container —
  CrowdSec publishes no official container for it — so that half belongs to the Ansible layer, and
  the two halves meet over the LAN at the LAPI.
- **Risk:** a mistake in the whitelist bans the LAN. Mitigated in §6.2.

### B. CrowdSec, ban at the router with the Traefik plugin

A plugin middleware answers `403` instead of the firewall.

- **No host change**, and the ban is visible in the router's own metrics.
- **The plugin is fetched from GitHub when Traefik starts**, so an offline boot leaves the router
  without its bouncer, and a plugin failure is a router configuration failure.
- **HTTP only.** A banned address walks straight into Minecraft and Syncthing.
- **Verdict:** the right fallback if the host half proves impractical, and the reason the access log
  moves to a file either way.

### C. No engine: Grafana alert → webhook → `nftables`

The detection is a LogQL expression and a small relay writes the ban.

- **Reuses the alert-relay pattern**, no new engine.
- **Hand-written detection**, no scenario library, no shared blocklist, and the ban logic lives in
  alert plumbing where a rule edit changes what gets banned.
- **Verdict:** the cheap shape, rejected because the operator asked for both halves and this is the
  same work with a weaker detector.

### D. Edge-first: the MikroTik router plus a sensor on a Pi

- **The strongest network position**, but the router's rules are not in git, the Pi is a single
  point, and it is a bigger build than A for a home uplink.
- **Verdict:** recorded as the upgrade path, not built.

## 6. Design

Seven parts. Each is independently useful and independently verifiable; the order is in §7.

### 6.1 See the client, without widening the store

Traefik writes one log to one target, and two consumers need different things from it: CrowdSec needs
the path and the user agent to recognise probing, and Loki must not hold either. The two are separated
by putting the file in the middle.

`cluster/helm/platform/traefik/values.yaml` changes three ways:

1. `ClientHost` **and** `ClientAddr` leave the drop list. CrowdSec's Traefik parser reads the source
   address from `ClientHost` (`remote_addr`) and treats `ClientAddr` as the destination, so
   un-dropping one of them alone leaves the agent parsing nothing. `ClientUsername`, `ClientPort`,
   `RequestPath`, `RequestReferer`, the query parameters and every header except `User-Agent` stay
   dropped **for the store**; the file is a different target, and §6.1's third point is where the
   path and the user agent are removed again before Loki.
2. `accessLog.filePath` writes the full JSON line — path and user agent included — to
   `/var/log/traefik/access.log`, a `hostPath` mounted through `deployment.additionalVolumes` /
   `additionalVolumeMounts`. Traefik writes to the file instead of stdout, so the container's stdout
   no longer carries requests.
3. Fluent Bit gains a second tail input over that `hostPath`, parses the JSON, **removes
   `RequestPath`, `request_User-Agent` and `RequestReferer` with a `modify` filter**, and sends the
   rest to Loki under its own tag. `RequestReferer` is already absent from the file, because Traefik
   drops it before writing; the filter names it anyway, so a later change to that drop list cannot
   leak it. The two Traefik dashboard rows that read the access log keep working, and the 30-day
   store keeps exactly today's field list plus the client address.

So the path exists in two places: `/var/log/traefik/access.log` on `streaming`, mode `0660` and owned
by `65532:65532`, rotated, and briefly in the agent's memory, where a scenario's bucket lives and
dies. It is a capability-bearing string (FileBrowser's share links) and it is not stored for thirty
days.

Two mechanical details, settled in this change rather than guessed here:

- **Ownership, and who creates the directory.** The mount is `DirectoryOrCreate`, because a
  `hostPath` of type `Directory` that is missing refuses to mount and the pod that refuses to start
  is the router: the whole house offline to add a log. The kubelet creates it as root; the Ansible
  role then sets `2770` and `65532:65532`, and Traefik creates the file inside it. Until the role
  runs, Traefik logs "Unable to create access logger" and serves on without a line — the failure
  this section already says is silent, and the reason the gate reads the file rather than the pod.
- **Rotation.** `logrotate` on `streaming`, 200 MB, seven copies, `copytruncate` under `su root root`.
  Traefik's access-log handler does reopen its file on `SIGUSR1` (`Handler.Rotate`), but the pod runs
  in its own PID namespace — as every pod does, and as the chart offers no `hostPID` to change — so a
  `postrotate` on the host cannot find the process to signal. Truncating in place leaves the writer's
  descriptor valid and asks nothing of the router. The cost is a handful of lines lost between the
  copy and the truncate, per 200 MB; both tailers see the size shrink and continue from the start of
  the file, and the acceptance test is a ban *after* a rotation, not a ban before one.

**A file that cannot be opened does not stop the router.** Traefik's startup calls
`accesslog.NewHandler`, and on failure logs `Unable to create access logger`, returns no handler, and
serves on with the chains nil-safe (`AliceConstructor` passes the request through when the handler is
nil). 3.7.19, read 2026-11-25. So a wrong owner or a missing directory costs the log, not the door —
and costs it silently. That is why the silence rule reads *lines arriving in the file*, not
"is Traefik up", and why the negative test in §8 exists.

### 6.2 CrowdSec in the cluster

One wrapper chart at `cluster/helm/security/crowdsec` (pattern D), one Application, two workloads
from `crowdsecurity/crowdsec` chart `0.24.3` (app `1.8.2`, published 2026-11-05):

- **The LAPI on `nas`**, beside the observability stack, its SQLite on a `local-path` claim: decisions
  must survive a restart, and the ban list is state.
- **The agent on `streaming`**, the node that owns the file. A pod on another node cannot mount that
  `hostPath`, so this placement is forced by §6.1 rather than chosen.
- **Acquisition:** the file, read from the tail. The first task checks the parser against a real line
  — the JSON shape, the key names, and the fields Traefik actually emits — before any scenario is
  trusted.
- **Scenarios:** `crowdsecurity/http-probing` (distinct paths), `http-bf` (login brute force),
  `http-bad-user-agent`, `http-crawl-non_statics`, plus the base collections they need. They work
  because the file carries the path and the user agent; a store-only field list would have made all
  four blind, which is why §6.1 is shaped the way it is.
- **Whitelist, enforced at the source and nowhere else:** CrowdSec's own whitelist (postoverflow
  expressions) covers `192.168.1.0/24`, `10.8.0.0/24`, `10.42.0.0/16`, `10.43.0.0/16` and the four
  node addresses, so a decision for them is **never created**. The bouncer has no allow-list of its
  own — its configuration has no such key — so a "bouncer whitelist" would have been a second
  mechanism that does not exist. One list, one enforcement point, one thing to test.
- **CAPI:** the shared blocklist is worth having and is a company outside the cluster. It is **off at
  first**, so the first weeks measure our own traffic; switching it on is one setting, reviewed
  afterwards on what it adds.
- **No `sshd` collection.** The agent sees the Traefik file, not the hosts' auth logs, and shipping
  the journal to Loki is not a CrowdSec acquisition. SSH is covered where it actually is: key-only
  login, and the `HostSshBruteForce` rule over the journal lines.

### 6.3 The bouncer on `streaming`

An Ansible role, `ansible/roles/crowdsec`, applied to `streaming` only — the one address every
forwarded packet reaches.

- Installs `crowdsec-firewall-bouncer-nftables`, pointed at the LAPI over the LAN, with a bouncer key
  read from the operator's environment at apply time and stored nowhere in git.
- `mode: nftables`; the ban list is a set in its own table, so `nft list sets` is the audit.
- Decisions are pulled, not pushed: if the cluster is down, the bouncer keeps the bans it has and
  stops updating. That is the correct failure — a blind detector leaves the last known bad addresses
  banned rather than opening the door.
- Rollback is one command: stop the service, `nft flush` its table, and the door is as it was.

**Its own liveness is the weakest signal in this design**, and the plan must not pretend otherwise.
The bouncer publishes no metric by default and the LAPI's view of "which bouncer last pulled" sits
behind an admin credential the exporter does not hold. Two honest routes, decided in the first task
by trying them: read last-contact from the LAPI with a machine credential, or enable the bouncer's
own Prometheus endpoint and give Prometheus a static job plus one firewall rule. If neither is clean,
the gap is written on the dashboard and in the runbook rather than hidden behind a rule that claims
to watch it.

### 6.4 The hosts' own attack surface

Two additions, both to the existing Ansible layer:

- **Ship the journal** (Phase 2, not Phase 5 — the dashboard panel and two rules read it). Fluent
  Bit gains a `systemd` input over a `hostPath` mount of the journal on every node, tagged `host.*`,
  reaching Loki with `unit` and `host` labels. That gives `sshd` failures and `kernel` (ufw) block
  lines. The known limit is UFW's own log rate limiting: a burst is visible as a burst, the exact
  packet count is not.
- **The Pis.** This is the [hosts plan](../plans/2026-11-24-hosts-stabilise.md)'s Pi script, not a new
  mechanism: `wg-easy` bound to `127.0.0.1:5000` and reached by SSH tunnel, `PasswordAuthentication
  no`, `ufw` on with `vpn_cidrs` filled from `wg-easy.env`. Their logs reach Loki the same way once
  a Fluent Bit container joins each compose stack — one service each, same output as the cluster's —
  and that is Phase 5, because it is convenience rather than a control.

### 6.5 The signal the cluster does not have: authentik

The sign-in page is the one public form that matters, and its events already carry `client_ip` and
`user_agent`. authentik publishes no metrics endpoint (measured: `/metrics` answers `404`), so the
design adds one small exporter rather than guessing at log lines.

`cluster/helm/security/security-exporter` — one Deployment on `nas`, one chart, `prometheus.io/scrape`,
~30 MiB. It polls three sources and publishes one family each:

| Family | Source | Why |
|---|---|---|
| `homelab_authentik_events_total{action}` | `GET /api/v3/events/events/`, the last 16 h, actions of interest only | how many sign-ins failed and succeeded |
| `homelab_crowdsec_decisions_active{origin,type}` and `homelab_crowdsec_bans_total` | the LAPI's `/v1/decisions` | how many addresses are banned, and how fast new ones arrive |
| `homelab_security_*` (see §6.6) | the live cluster | is the door still shut |

**No address is a Prometheus label.** Prometheus answers unauthenticated on the LAN and the baseline
treats the LAN as untrusted, so an attacker's address does not belong in it. The per-address views
read the gated store instead (§6.8); the exporter stays aggregate. That also removes the cardinality
question the per-IP design would have carried.

A token for authentik and a key for the LAPI are Secrets, both read-only.

### 6.6 Is the door still shut

The exposure check runs over rendered charts before a PR. It does not read the cluster, and the
baseline's own first open question is the four routes that render but do not exist live (and the
reverse, which nothing would catch). The exporter closes that with a read-only ServiceAccount — the
same shape as `opencode-mcp` — and these series:

| Series | Meaning | Healthy |
|---|---|---|
| `homelab_security_ingress_routes` | live `IngressRoute` objects | the expected count from the chart's values, after §7 Phase 2's reconciliation |
| `homelab_security_ungated_routes` | live routes with neither `lan-only` nor a name on the public list | 0 |
| `homelab_security_public_hosts` | hosts that answer the internet by allowlist | 9, and the number is a value, changed only with the allowlist |
| `homelab_security_privileged_workloads` | pods with `privileged: true` | 4, each named |
| `homelab_security_stale_certificates` | certificates in the store no route serves | trends to 0 (card 543) |

A non-zero `ungated_routes`, or a count that moved without a commit, is a critical alert. This is the
only part of the design that can catch a hand-applied route, which is exactly the gap the baseline
could not close.

The expected values live in the chart's `values.yaml`, not in a second copy of `check_exposure.py`:
the checker owns the allowlist, the chart owns the expectation, and Phase 2 begins by reconciling
77 rendered against 73 live so the expectation is true on the day it is set.

### 6.7 What this does not break: FileBrowser

Checked on 2026-11-25 because FileBrowser is the one public application whose URLs carry capability
tokens, and the change touches the router in front of it.

- Its route is unaffected: `filebrowser.kub.home.ashudev.com` on `websecure`, one middleware
  (`kube-system-security`, the header set), its own `filebrowser-tlsoption`, Service port 8080. It is
  public on purpose — the editor signs in from outside and share links go to people with no account —
  and nothing in the chart or the route reads Traefik's access log.
- Its own logging is unaffected: FileBrowser 2.0.2 writes warnings and errors to stdout, which is
  still the `kube.*` pipeline; the new key-removal filter is scoped to the Traefik file's tag, so no
  other container's JSON can lose a field to it.
- **The one real exposure is a ban, not a break.** Share links are opened by people with no account,
  from outside, and the whitelist covers only the LAN, the tunnel and the cluster — so a visitor who
  hammers dead share links could trip a scenario and be refused for the decision's lifetime. That is
  the intended behaviour with a false-positive cost, and it is why the ban list is on the dashboard
  and why `cscli decisions delete` is in the runbook.
- The share-link strings themselves now exist in `/var/log/traefik/access.log` for the seven rotated
  copies, which the operator accepted. They still never reach Loki.

### 6.8 The screen and the rules

**`Homelab · Security`**, nine panels on the first screen, one question each. Every panel carries a
unit; colour is used only for thresholds.

| Row | Panel | Question | Source |
|---|---|---|---|
| Danger | **Danger state** | how bad is it right now? | §6.8 formula |
| Danger | Bans active | how many addresses are we refusing? | decisions |
| Danger | Requests, public hosts, 24 h | is the volume normal? | access log |
| Danger | Rejected share | what proportion is being refused? | access log status |
| Who | Top addresses refused | who is pushing? | access log (Loki) |
| Who | Sign-in outcomes | is it the login page? | authentik counts |
| What | Requests by public host and status | which door are they pushing? | access log |
| What | Host blocks by host and port | is it below HTTP? | journal (ufw) |
| Door | Posture table | is the door still shut? | §6.6 |

Two notes the panels must carry, because both are easy to misread: *Top addresses refused* shows who
was **answered** `401/403/429`, not who was dropped by the bouncer (a dropped request never reaches
Traefik, so it has no line), and the exact ban list is `cscli`. *Sign-in outcomes* is a count without
an address, for the reason in §6.5.

**The danger state is derived and its formula is written on the panel**, because a number nobody can
derive is a number nobody trusts:

- **Alerting** — any security rule is firing, or more than 20 new bans in an hour.
- **Watch** — at least one ban in the last hour, or more than 20 % of public requests rejected in the
  last 15 minutes, or more than 20 failed sign-ins in 15 minutes.
- **Calm** — none of the above.

The three states are one expression with thresholds 0/1/2, one stat panel, and the formula in its
description. No score out of ten.

**The thresholds are provisional and marked so.** Bots probe nine public names all day, so a fixed
20 % rejected share may be permanently true; Traefik's quietest hour may have a gap longer than ten
minutes. Before Phase 3 fixes the numbers, two queries against today's data settle them: rejected
share by hour over 7 days, and the longest gap between access-log lines. Phase 4 does the same for
each rule's threshold. A number chosen for a quiet night is a number that gets muted.

**Rules.** Seven, added to the existing groups by the existing cadence rule (a `for` of 5 minutes or
less goes to `homelab-core-fast`); the existing contact point and the critical-to-board relay are
reused, so there is no second delivery path and no second policy.

| Rule | Condition | for | Severity |
|---|---|---|---|
| `SecurityBanBurst` | more than 20 new decisions in 10 min | 5m | warning |
| `SecurityAttackSustained` | one address with more than 100 answered `401/403/404` in 10 min | 5m | critical |
| `AuthentikSignInFailures` | more than 20 failed sign-ins in 15 min | 5m | warning |
| `HostSshBruteForce` | one address with more than 10 `sshd` failures in 10 min on any host | 5m | warning |
| `HostFirewallBlockBurst` | more than 200 `kernel` block lines in 5 min on one host | 5m | warning |
| `SecurityExposureDrift` | `homelab_security_ungated_routes > 0`, or a count moved, or a privileged workload appeared | 10m | critical |
| `SecurityStackBlind` | the agent, the LAPI, the exporter or the log path is silent, or no new access-log line in 10 min (measured, then set) | 5m | critical |

Each message names the address, the host and the value — the fields a person needs without opening
Grafana. `SecurityStackBlind` is the rule that makes the other six trustworthy; without it a dead
agent looks exactly like a quiet night.

## 7. Phases and order

| Phase | What | Why here | Blocks |
|---|---|---|---|
| 0 | §6.1, the access log: client address, file, Fluent Bit input and key removal, rotation; amend `observability-conventions.md` and supersede #410's field list | one file unblocks everything, and it is reversible alone | 1, 2, 3, 4 |
| 1 | §6.2 and §6.3, CrowdSec plus the bouncer | the operator's ask, and it needs the file | 2, 4 |
| 2 | §6.6, then §6.5 and the journal half of §6.4; reconcile the four rendered-not-live routes first | posture needs true constants; the journal feeds a panel and two rules | 3, 4 |
| 3 | §6.8, the dashboard | reads 1 and 2; useful before the rules exist | — |
| 4 | §6.8, the seven rules, each threshold measured first | last, because a rule over a series nobody has checked is a page nobody understands | — |
| 5 | The door work: the Pis, login rate limits, the patch window, the etcd schedule (#506), the Pis' log shipping | independent of the detector, and the Pis are the worst remaining hole | — |

Phases 0 and 1 are one sitting; 2 and 3 can run beside each other; 5 is the existing cards, pulled
into this plan only so the security work has one list.

## 8. Verification

Nothing here is accepted from a render. Each phase has its own proof, and the operator runs the ones
that touch a host.

**Phase 0**
- A line in `/var/log/traefik/access.log` carries `ClientAddr`, `RequestPath` and
  `request_User-Agent`; the same request in Loki carries `ClientAddr` and **no path, no user agent,
  no query, no header**.
- Both Traefik dashboard rows that read the access log still return data.
- A rotation is followed by a new line reaching both Loki and CrowdSec, and by a ban.
- **The negative test:** take the write permission off `/var/log/traefik` once and confirm the router
  keeps answering while the log stops — then that the silence rule fires — before restoring it.
- `docs/observability-conventions.md` and #410 carry the new field list; #544's "all 0" verification
  is superseded, not contradicted.

**Phase 1**
- `cscli decisions list` names a test address after a deliberate burst of missing pages.
- `nft list set` on `streaming` holds that address; a connection from it is refused, and the same
  connection from the LAN still works.
- A burst from a whitelisted address creates **no decision at all** — the source whitelist, not the
  bouncer, is what is tested.
- The agent restarting leaves the existing bans in place.
- Rollback: the service stopped and its table flushed restores the previous behaviour, tested once.

**Phase 2**
- The four rendered-but-not-live routes are explained before the expected counts are written down.
- `count()` of each new family is above zero, and one value is checked by hand against the API it
  came from (a sign-in count, a decision count, the live route count).
- No family carries an address label.
- The exporter's ServiceAccount cannot write anything: `kubectl auth can-i --list` as it.
- A journal line from `sshd` and one from `kernel` reach Loki with `host` and `unit`.

**Phase 3**
- Rejected share by hour and the longest access-log gap are measured first, and the danger-state
  numbers are set from them, with the measurement noted on the panel.
- Every panel query is executed through the datasource API before it is saved; a query that errors
  or returns nothing is a panel that lies.
- The screen is read at 1080p from the distance it will be watched, and the danger state is forced
  to each of its three values once.
- A copy is exported to `cluster/helm/monitoring/grafana/export/security/` the way the Traefik
  dashboard and its rules are: a snapshot to review in a pull request, applied by nothing.

**Phase 4**
- Each rule is forced to fire once with the labels and the value expected, and observed resolving.
- `SecurityStackBlind` is proved by stopping the agent, not by reasoning about it.
- One Discord message and one board card are read back as a person would receive them.

**Phase 5**
- The Pis: `ss -ltnp` shows `5000` on loopback only; `ssh -o PreferredAuthentications=password`
  fails; `ufw status` shows the VPN range; a WireGuard client still resolves DNS.
- The etcd schedule: a snapshot file exists with the day's date and the upload is seen in the bucket.

## 9. Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| A whitelist error bans the LAN, the pods or a node | Medium, and the worst outcome here | One list, enforced before a decision exists; a test burst from a LAN address that must create nothing; a documented one-command rollback |
| The path and user agent in the file are read by the wrong process | Low | File `0660` owned by `65532:65532`, directory `2770`, seven rotated copies; a target owner and mode check in the task |
| Traefik's file writer and the two tailers disagree after rotation | Low | Copy-then-truncate keeps the same inode, so there is no inode to lose; the residual cost is a few lines per rotation, and the acceptance test is a ban after a rotation |
| A broken log path costs the log, not the router, so the detector goes blind while every page still loads | Medium | The negative test in §8, and `SecurityStackBlind` keyed on lines arriving rather than on the router being up |
| A parser mismatch leaves the agent reading lines it cannot parse, so it detects nothing | Medium | The first task parses one real line; `SecurityStackBlind` fires when nothing arrives |
| The bouncer's liveness cannot be measured cheaply | Medium | §6.3 names the two routes and the fallback: say so on the dashboard and in the runbook |
| Loki now holds visitor addresses for 30 days | High, accepted | Store is Admins-only; path, query and headers stay out; #410 records the revised list |
| The exporter's read access is a foothold | Low | Read-only ServiceAccount on named resources; no Secret access; `can-i --list` is an acceptance test |
| A ban list that grows without bound | Low | Decisions expire (default four hours); the set is bounded by them; the panel shows the count |
| A security panel stuck on Watch because the threshold was never measured | Medium | §6.8 measures before it fixes a number, and the provisional figures say so |
| The security work is read as "the homelab is secure" | Medium | The anti-goals, and the phases each state what they do not cover |
| Grafana or `nas` down means no alert at all | Accepted since 2026-11-12 | Unchanged; `SecurityStackBlind` covers the detector, not the platform |

## 10. What is left to the builder

- The exact `accessLog` key shape in chart 42.4.1 and the JSON key names Traefik actually emits, so
  the Fluent Bit key-removal filter names the right keys.
- The CrowdSec parser and the four scenarios, proved against a real line and against normal Plex
  traffic, which is the busiest thing in the log.
- The bouncer key's route from the cluster to the host, and which of §6.3's two liveness routes works.
- The reconciled constants for `homelab_security_*`, and how the chart's values stay tied to
  `check_exposure.py` rather than drifting from it.
- The two measured numbers behind the danger state, and each rule's threshold.

## 11. Evidence

- `docs/security/security-baseline-2026-11-24.md` — the six boundaries, graded, with the source of
  each line.
- `docs/audit/hosts-2026-11-24.md` — the hosts as measured, and again after the Ansible layer ran.
- `cluster/helm/platform/traefik/values.yaml` — the drop list and the 15 MB/day measurement.
- `docs/observability-conventions.md` — the access-log fields, the store's gate, the query recipes.
- `docs/reference/alerts.md` — the 49 rules, the two groups, the delivery policy, the silence rules.
- `docs/reference/dashboards.md` — the eleven screens and the API that changes them.
- `https://crowdsecurity.github.io/helm-charts/index.yaml` — chart `crowdsec` `0.24.3`, app `1.8.2`,
  read 2026-11-25.
- `https://docs.crowdsec.net/u/bouncers/firewall` — the firewall bouncer is an OS package
  (`crowdsec-firewall-bouncer-nftables`), not a published container, read 2026-11-25.
- The live cluster, 2026-11-25: 73 `IngressRoute`, the ten `kube-system` middlewares, four nodes on
  K3s `v1.36.6+k3s1`.
- The authentik API, 2026-11-25: `GET /api/v3/events/events/` returns `client_ip` and `user_agent`;
  294 events, three `login_failed`; `/metrics` answers `404`.
- The builder check of 2026-11-25: nine questions, of which §6.1's file/store split, §6.2's single
  whitelist, §6.3's liveness gap, §6.4's phase order, §6.5's address labels and §6.6's constants are
  the changes above.
- Traefik `v3.7.19` source, read 2026-11-25: `cmd/traefik/traefik.go` `setupAccessLog` warns and
  returns no handler when the file cannot be opened; `pkg/middlewares/accesslog/logger.go`
  `Handler.Rotate` reopens on `SIGUSR1`; `AliceConstructor` passes the request through when the
  handler is nil.
- `cluster/helm/filebrowser/values.yaml`, `templates/ingressroute.yaml` and the live
  `IngressRoute/filebrowser`, read 2026-11-25: one public `websecure` route, `kube-system-security`
  only, its own `TLSOption`, Service port 8080, own OIDC, stdout logging.
