# CrowdSec coverage: CAPI, authentik brute force, AppSec — design

2026-11-27.

> **Status: for review.** Nothing is built until this is approved. The plan follows
> (`docs/superpowers/plans/`), then implementation.

## Intent

A review of the live CrowdSec stack in this cluster found the HTTP/HTTPS surface fully covered and
cheap, and three gaps worth closing. The operator chose all three:

1. **CAPI and the community blocklist** — the LAPI is deliberately offline today, so an address
   already known to attack the rest of the internet reaches this cluster untouched.
2. **authentik brute force** — the identity provider's sign-in page is public, and the current
   scenario set can never fire on it.
3. **AppSec / WAF** — CrowdSec judges behaviour today, not payloads; SQL injection and XSS pass
   through.

This document is the shape of the three, in the order they should land. It is not a plan: no step
numbers, no file-by-file diff. That is `writing-plans`, after approval.

Two rules govern the whole document, both stated by the operator:

- **A LAN or WireGuard address is never banned.** `192.168.1.0/24` and `10.8.0.0/24` stay trusted,
  including through the new blocklist.
- **Non-HTTP ports stay as they are.** SSH is not forwarded by the router, and every source that can
  reach Syncthing, Minecraft, WireGuard or SSH is a trusted address, so a CrowdSec decision on them
  could never legally exist. Their protection is protocol-native (keys, device IDs, allowlists) and
  is not changed here.

Vocabulary used below:

- **CAPI** — CrowdSec's central API. The LAPI registers with it and pulls a shared blocklist of
  addresses other installations have reported.
- **Blocklist decision** — a ban that arrives from CAPI rather than from a local log line. It is the
  one decision class the local whitelist never sees, which is why it gets its own control below.
- **AppSec** — CrowdSec's WAF component: it reads the request itself (headers and body) against a
  rule set. In-band rules block; out-of-band rules only raise an alert.
- **CRI log** — the format containerd writes container stdout in, under `/var/log/pods/`: a
  timestamp, a stream name, a tag, then the application's own line.

## 1. What is true today

Measured on the live cluster, 2026-11-26 to 2026-11-27.

| Fact | Value |
|---|---|
| Chart / engine | `crowdsec` chart 0.24.3, CrowdSec `v1.8.2` — newest published |
| Agent | one Deployment, 1m CPU / 62 MiB, pinned to `streaming`, reads `/var/log/traefik/access.log` |
| LAPI | one pod on `nas`, 4m CPU / 48 MiB, SQLite on `local-path`, backed up |
| Traffic read | ~110,000 access-log lines a day, 0 parse errors |
| Decisions | 478 alerts, 40 active decisions, 19 addresses in the kernel set |
| Enforcement | nftables bouncer on `streaming`, polls every 10 s, `input` and `forward` hooks |
| Routes | 75 HTTP routes, 0 ungated, 9 public hosts |
| CAPI | off — `cs_features{capi}=0`, `cs_capi_last_pull_timestamp=0` |
| AppSec | off — `cs_features{waf}=0` |
| authentik route, 24 h (status codes) | 3,324 × `200`, 86 × `302`, 10 × `403` |

The three gaps, each with its evidence:

**CAPI.** The LAPI runs with `DISABLE_ONLINE_API=true` in `cluster/helm/security/crowdsec/values.yaml`,
and both `cs_features{capi}` and `cs_capi_last_pull_timestamp` report zero. Everything the stack
blocks today, it learned from this one access log.

**authentik brute force.** A failed sign-in re-renders the login form with `200`, so the
`crowdsecurity/http-bf` scenario — which counts `401`/`403` — has nothing to count. And the event
line authentik writes for a failure is suppressed: `AUTHENTIK_LOG_LEVEL` is `warning` since
2026-11-22, while authentik logs `Created Event` at `INFO`. Finally, even at `info` the line is in
the authentik pod's stdout on `nuc0`, and the agent reads one host file on `streaming`. Two
independent reasons, either of which alone is enough to make the scenario silent.

**AppSec.** No component, no rule set, no bouncer plugin on Traefik. The request body is never read
by anything.

## 2. Goals, non-goals, anti-goals

**Goals.**

- A known-bad address is refused before it sends its first request, not after twenty.
- A brute-force attempt against the identity provider creates a decision and a ban like any other
  attack, and the operator is told.
- A request carrying an injection or a known-exploited CVE payload is blocked at the door, with the
  false-positive count known before the first block.
- Every new control is visible in the existing alert set, and its failure is loud.

**Non-goals.** Non-HTTP ports (§Intent). Banning a LAN address (§Intent). authentik's own reputation
policy — it blocks inside the app and lives in the database, so it cannot be reviewed in git and
cannot ban at the firewall. FileBrowser uploads in the first WAF slice (§6.2).

**Anti-goals.** A control that can lock the operator out of the identity provider. A second place
where the LAN boundary is written without a test. An alert that fires on the first blocklist load
and teaches the operator to ignore the rule.

## 3. Constraints the design obeys

- **GitOps.** Every change is chart values or a template in this repository. The one thing that is
  not is the Grafana alert edit in §4.4, which is an API call against a database the repo does not
  own.
- **No secret in git.** The bouncer key for the Traefik plugin is a hand-made Secret in two
  namespaces, mounted as a file, exactly like `crowdsec-bouncer-key` today.
- **The chart holds one agent.** Its ConfigMap names (`acquis-configmap`, `crowdsec-docker-start-script-configmap`)
  are not templated, so two releases in one namespace collide. The design works inside one release.
- **No node is tainted**, and `nuc0` is `10.42.2.0/24` while `streaming` is `10.42.3.0/24`.
- **Loki writes 117.0 MB a day** (`docs/reference/log-budget.md`). New log volume is accounted for.
- **Memory.** The stack costs 110 MiB today; `nas` and `streaming` both have headroom.

### 3.1 Checked while writing this

`helm template` of the pinned chart 0.24.3 with the values this design proposes renders what the
three workstreams need: one DaemonSet with the two host paths and both acquisitions, a
`crowdsec-capi-whitelists` ConfigMap with `CAPI_WHITELISTS_PATH` on the LAPI, and the AppSec
Deployment, its acquisition and the `7422` Service. It renders **no** CAPI registration Job, which
is the point of §4.1. Nothing was applied; this only proves the keys exist in the chart this
repository pins, under the names used below.

## 4. Workstream A — CAPI and the community blocklist

### 4.1 What changes

`DISABLE_ONLINE_API` is removed from `lapi.env`. The upstream start script registers the LAPI with
CAPI on its next start and writes `online_api_credentials.yaml` to the config claim
(`crowdsec-config-pvc`), which is already a backup row — so the identity survives a rebuild. No
registration Job is rendered: the chart only renders one when the config volume is *not* persistent,
and here it is.

With CAPI live the LAPI pulls the community blocklist on its own schedule and the bouncer receives
those decisions with every other one, in the same 10-second poll. No bouncer change.

### 4.2 Keeping the LAN unbannable

Blocklist decisions bypass the parser whitelist in `values.yaml`: that postoverflow runs before a
decision is created from a local alert, and a CAPI ban is inserted into the database directly. The
operator's rule therefore needs the control designed for it —
`api.server.capi_whitelists_path`, which filters addresses **before the blocklist is inserted**.

The chart already wires it: `config.capi_whitelists.yaml` renders a ConfigMap, mounts it at
`/etc/crowdsec_data/capi_whitelists.yaml` and exports `CAPI_WHITELISTS_PATH` pointing at it. So the
change is the same list as the postoverflow, in a second file, with the same comment that says why
there are two.

```yaml
config:
  capi_whitelists.yaml: |
    ips:
      - 192.168.1.41
      - 192.168.1.27
      - 192.168.1.46
      - 192.168.1.48
    cidrs:
      - 192.168.1.0/24
      - 10.8.0.0/24
      - 10.42.0.0/16
      - 10.43.0.0/16
```

Two limits worth writing down. The file is **deprecated upstream** in favour of the centralized
allowlists in the LAPI database; the replacement is a `cscli allowlists create` call, i.e. state
outside git, so this design takes the declarative file and records the migration as a follow-up. And
the filter applies **at pull time only**: a decision already in the database is not retroactively
removed, which matters because CAPI is being enabled on an installation that already holds 40
decisions (none of them CAPI).

A test belongs beside it, in `hack/tests/test_crowdsec_chart.py`, the same shape as the existing one
for the postoverflow: the two YAML files carry the same ranges, and both match the four ranges that
file already declares as the ones a ban must never reach.

### 4.3 What leaves the house

Enabling CAPI is the only part of this stack that talks to a company, so the design states the
exchange plainly rather than summarising it:

- **In:** the blocklist, tailored to the behaviour this engine reports.
- **Out, per alert:** the scenario's name and hash, the decision's timestamp, the machine id, the
  offending address and its geolocation. At sign-in, the list of scenarios the engine runs; and
  periodically, the names and versions of the bouncers and engines registered to the LAPI. Nothing
  else — no URL, no header, no user name.
- **The opt-outs are narrow.** `share_custom` and `share_tainted` are set false, so a locally written
  parser or a modified scenario never leaves. Everything from the official set does. Turning CAPI off
  is the only setting that sends nothing at all, which is why this is the operator's decision and not
  a setting to slip in beside the others.
- **Tier is a consequence, not a switch.** A free installation whose signals come mostly from custom
  or tainted scenarios receives the *Community Blocklist (Lite)*; contributing official, unmodified
  scenarios is what the full list rewards. The scenarios here are hub scenarios, so they count — and
  `firix/authentik-bf` (§5) is unmodified from the hub too, so its alerts carry the failing address
  upstream like any other.

So the trade is: a blocklist of the addresses that attack the rest of the internet, in exchange for
the addresses that attack this cluster. The house's own traffic is not in it, because a LAN address
is never alerted on (§4.2).

### 4.4 The alert that would misfire

`SecurityBanBurst` reads `delta(sum(fw_bouncer_banned_ips)[10m:1m]) > 20`. The kernel set is a
gauge, so the first blocklist pull — thousands of addresses at once — fires the rule. That is a
false alarm on the one alert meant to say "something is happening", and it would arrive on the same
day the change lands.

The rule moves to what it actually means: more than twenty **locally created** bans in ten minutes.

```
delta(sum(cs_active_decisions{origin="crowdsec"})[10m:1m]) > 20
```

`origin="CAPI"` is the blocklist, `origin="crowdsec"` is a scenario of ours, `origin="cscli"` is a
manual decision. The rule lives in Grafana's database, so this is an API edit followed by refreshing
`cluster/helm/monitoring/grafana/export/security/rules.json`, which is the reviewable copy.

### 4.5 Proof

- `cs_features{capi}` is 1 and `cs_capi_last_pull_timestamp` is non-zero on the LAPI.
- `cs_active_decisions{origin="CAPI"}` is non-zero, and no address in it is in `192.168.1.0/24` or
  `10.8.0.0/24` — read with `cscli decisions list -o json` from a pod, because the metric carries no
  address label.
- The kernel set grew by roughly the blocklist size, and the host's own LAN address is still absent
  from it.
- `SecurityBanBurst` does not fire across the first pull.

## 5. Workstream B — authentik brute force

### 5.1 Why nothing sees it

Two facts from §1, and both must be fixed:

1. The event line is not written at `warning`.
2. Even written, it is on `nuc0` and no agent runs there.

The line itself is confirmed from authentik's source, not guessed:
`Event.save()` logs `Created Event` at `INFO` with `action`, `context`, `client_ip` and `user` as
extra fields, which the JSON formatter lifts to top-level keys. `client_ip` comes from
`X-Forwarded-For`, which Traefik sets, so the address is the real client and not the proxy pod.
That matches the `firix/authentik-logs` parser field for field — no local parser to maintain.

### 5.2 Shapes considered

| Shape | Verdict |
|---|---|
| **One DaemonSet on `streaming` and `nuc0`, two acquisitions** | **chosen** |
| A second agent Deployment written by hand in the wrapper chart | Rejected: ~90 lines duplicating the chart's init container, volumes and registration, to avoid one host directory. It drifts on every chart bump. |
| fluent-bit ships authentik lines to a syslog listener on the existing agent | Rejected: a new pipeline and a second copy of the same log line over the network, coupled to fluent-bit's RFC5424 framing. |
| Raise the level and keep one agent on `streaming` | Impossible: the pod's stdout exists only on the node that runs it. |

The chart ships a DaemonSet template for exactly this, it honours `agent.affinity`, and it registers
each pod under its own name (`USERNAME` is `metadata.name`), so two pods are two machines and no
registration collides.

### 5.3 The change

- `agent.isDeployment: false`; `agent.affinity` restricts the DaemonSet to `streaming` and `nuc0`
  with `nodeAffinity … In [streaming, nuc0]`. No node is tainted, so no toleration is needed.
- Two host mounts, both narrow: `/var/log/pods` (a real directory on both nodes) and
  `/var/log/traefik` as `DirectoryOrCreate` — the second exists on `streaming` only, and on `nuc0`
  it becomes an empty directory that nothing writes to. `hostVarLog` stays false: the agent never
  needs the whole of `/var/log`.
- Two acquisitions: the existing Traefik file, unchanged; and
  `/var/log/pods/authentik_*/*/*.log` with `type: containerd`, `program: authentik-server`. The
  `crowdsecurity/cri-logs` parser unpacks the CRI envelope and hands `message` to the next stage.
- `COLLECTIONS: crowdsecurity/traefik firix/authentik`. The second one installs the parser and the
  two leaky scenarios (`firix/authentik-bf`, `firix/authentik-bf_user-enum`).
- `lapi.config.allowed_ranges` gains `10.42.2.0/24`. It is the registration path, so this is a real
  widening of a control narrowed on purpose by card #570 — documented next to the existing /24, and
  the reason is that a second agent pod now exists.
- `server.env` on authentik gains `AUTHENTIK_LOG_LEVEL: info`; the worker keeps `warning` through
  the global block. The worker's info lines are task logs nobody reads, and its deadlock runbook
  depends on the quiet.

`hack/tests/test_crowdsec_chart.py` asserts today that the agent is a Deployment on `streaming`,
that auto-registration admits one subnet, and that CAPI is off. Those three assertions become the
DaemonSet, the two subnets, and CAPI on with the blocklist filtered — they move, they do not
disappear.

### 5.4 Cost

The server at `info` is 28,386 lines / 16.3 MB a day, of which 17,267 are health checks. Measured
against a 117.0 MB day, that is +14 % of the store — under the budget's own doubling line, and it
buys the one event that matters. If it turns out to hurt, the iteration is a fluent-bit filter that
drops `/-/health/` for the authentik container only, not a redesign.

### 5.5 False positives

The scenario is `leaky, capacity 5, leakspeed 20s, blackhole 1m` per source address — five failures
in about a hundred seconds. A person mistyping a password twice does not reach it; the exporter's
own measurement from 2026-11-14 (three failures in nine seconds, then a success) does not either.
Every source inside the LAN is whitelisted anyway, so a ban can only land on a public address.

The `AuthentikSignInFailures` and `AuthentikSignInSuccessAfterBurst` alerts stay as they are: they
read the events API with a token and do not depend on the log level.

### 5.6 Proof

- One deliberate wrong password against the sign-in page, from a public address if one is available,
  produces an `authentik_failed_auth` event in the agent's log (`cscli metrics` shows the parser
  hit), then a decision, then the address in the kernel set — the four proofs already written in
  `docs/runbooks/crowdsec.md`.
- `cs_parser_hits_ok_total` for the authentik parser rises, and the file source for the containerd
  acquisition reports hits on `nuc0`.
- The Loki count for `{namespace="authentik"}` stays within the measured +14 %.

## 6. Workstream C — AppSec / WAF

### 6.1 Shape

Three pieces, and only the third is per-route:

1. **The component.** `appsec.enabled: true` in the same release, pinned to `nas` beside the LAPI,
   with an acquisition listening on `0.0.0.0:7422`. Its config volume is its own; the LAPI is
   local, so authentication calls do not cross the cluster.
2. **The key.** `BOUNCER_KEY_traefik` in `lapi.env` from a hand-made Secret; the same value in a
   Secret in `kube-system`, mounted into the Traefik pod as a file. AppSec validates the key against
   the LAPI, so one key serves both the AppSec call and the plugin's own authentication. Nothing in
   git holds it.
3. **The plugin and the middleware.** `experimental.plugins.crowdsec` in
   `cluster/helm/platform/traefik/values.yaml`, version `v1.8.2`, and a `Middleware` in
   `traefik-gate` (namespace `kube-system`, like `lan-only`) whose plugin config points at
   `crowdsec-appsec-service.security:7422` and reads the key from the mounted file. Routes attach it
   the way they attach every other gate.

Mode is `appsec`, not `live`: the plugin's IP check is switched off because the nftables bouncer
already drops a banned address on every port, including the ones Traefik never sees. One enforcement
point, not two.

### 6.2 The first slice, and why only one route

The middleware goes on **the authentik host, and nothing else**, for the first week.

authentik is the highest-value target: it is public, it is the sign-in page every application's SSO
flow passes through, and its request bodies (a password, a form) are small and well understood. It
is also the route where a false positive is a lockout, which is exactly why it should be the first
one measured rather than the last.

`FileBrowser` is explicitly **not** in the first slice. It is a file manager: a WAF that buffers a
body to inspect it sits in front of multi-gigabyte uploads, and the plugin's default cap is 10 MB.
Adding it later means `crowdsecAppsecExcludedLocations` for the upload endpoints — which is a WAF
bypass with its own review — or accepting the cap. Neither belongs in the same change as the first
route.

### 6.3 Rules, in two phases

The hub separates the two things people call "WAF", and the separation is the rollout plan:

- **`crowdsecurity/appsec-crs`** — *"WAF: Non-Blocking OWASP Core Rule Set"*. Every CRS rule is
  **out-of-band**: it is evaluated, it raises an alert through the `crowdsec-appsec-outofband`
  scenario, and it never blocks. Phase one installs only this. A week of alerts with zero false
  positives is the evidence for phase two.
- **`crowdsecurity/appsec-virtual-patching` and `crowdsecurity/appsec-generic-rules`** — in-band
  rules for known-exploited CVEs and generic vectors, blocking on match. Phase two adds them, with
  `crowdsecurity/crs` kept alongside so CRS stays out-of-band.

Both phases reuse the existing notification path: an AppSec alert is an alert, so it reaches Discord
without new plumbing.

### 6.4 Failure behaviour

The plugin has three failure knobs and the sign-in route sets all three to keep the identity
provider available:

- `crowdsecAppsecFailureBlock: false` and `crowdsecAppsecUnreachableBlock: false` — if AppSec is
  down, the request proceeds. A WAF that takes the login page with it is worse than no WAF.
- `crowdsecAppsecBodyLimit` stays at the 10 MB default; nothing on this route sends a larger body.
- `crowdsecAppsecUnreadableBodyBlock: false` — a body that cannot be buffered is forwarded with its
  headers only.

The trade is stated once: for these routes, an AppSec outage is silent protection loss, reported by
`SecurityStackBlind` through the AppSec metrics endpoint rather than by a 403 storm.

### 6.5 Resource and cost

Upstream asks 500m CPU and 250Mi. This design requests 100m / 192Mi and limits 768Mi, to be replaced
by the first night's measurement. Per-request latency is one cluster hop from `streaming` to `nas`
plus CRS evaluation at paranoia level 1; it is measured on the sign-in route before phase two, not
estimated here.

### 6.6 Proof

- `cs_features{waf}` is 1 and the AppSec pod is Ready.
- A benign request to the authentik host returns the same status and body as before the middleware,
  and appears in the AppSec access metrics.
- A request carrying a known CRS test payload (a path-traversal probe is enough) produces an alert
  and **does not** block in phase one, and blocks in phase two.
- Phase two is only entered after a week with zero CRS alerts from the operator's own use.

Since this touches what a browser sees, the change is verified in a browser as well: the sign-in
page loads, a real sign-in succeeds, and the failure path still renders the form.

## 7. Order and phases

A first, B second, C third. A is independent and immediate; B is the only one that changes how an
existing workload runs, so it should not share a sync window with C; C is the largest and depends on
a component and a plugin that both need their own proof before a request depends on them.

| Phase | Content | Gate to the next |
|---|---|---|
| 1 | Workstream A + the alert fix | `cs_features{capi}=1`, no LAN address banned, no `SecurityBanBurst` |
| 2 | Workstream B | the four proofs in the runbook pass |
| 3a | AppSec install, CRS out-of-band, authentik route | a week of zero false positives |
| 3b | in-band CVE rules | — |

## 8. Verification

The full test suite runs on the tree before every commit. On top of that, each phase is accepted on
observable facts, not on a rendered manifest:

- **A** — the two metrics, the decisions list read from the LAPI, the kernel set's size, and the
  absence of the LAN; `SecurityBanBurst` quiet through the first pull.
- **B** — `cscli metrics` parser and filesource hits, one decision from one deliberate failure, the
  kernel set, and the Loki byte count for the namespace.
- **C** — `cs_features{waf}`, the AppSec pod, a benign request end to end in a browser, one test
  payload out-of-band then in-band, and the alert arriving in Discord.
- **All** — `hack/check-charts.sh`, the `hack/tests` suite, and `hack/check-docs.sh` on the pages
  the change touches.

## 9. Risks

| Risk | What is done about it |
|---|---|
| A CAPI blocklist decision covers a LAN address | `capi_whitelists` filters at insertion; the proof step reads the decisions list, not a count |
| CAPI's first pull fires `SecurityBanBurst` | the rule moves to local-origin decisions before the change lands |
| A CAPI or console change alters sharing | `share_custom` / `share_tainted` false, and §4.3 says what still leaves |
| Raising authentik's log level blows the Loki budget | measured at +14 % of the day; the filter iteration is named |
| The second agent widens `allowed_ranges` | one /24, documented, and the failure mode of a missing range is a crash-looping init container, not a silent hole |
| The DaemonSet's empty `/var/log/traefik` on `nuc0` | read-only mount, no writer, noted in the values file |
| A Traefik plugin is downloaded at startup | pinned `v1.8.2`; a failed download leaves Traefik without the middleware, which the AppSec metric check catches |
| AppSec blocks the operator out of the identity provider | phase one cannot block; phase two's in-band rules are CVE-specific; fail-open on AppSec unavailability |
| A WAF false positive on a password field | phase one is exactly the instrument that finds it, on the one route users sign in through |

## 10. Documentation changes

- `docs/reference/alerts.md` — the "Known gaps" bullet that says no rule reads the bouncer poll is
  stale; `SecurityBouncerStalled` is live and in `rules.json`. Corrected in the same change.
- `docs/runbooks/crowdsec.md` — the four proofs gain the CAPI and authentik cases; the AppSec
  phase-two step is added when it lands.
- `docs/reference/log-budget.md` — the authentik level change is a new row with its number.
- `docs/secrets.md` — one row for the Traefik plugin's key, its two namespaces, and the bouncer key
  the LAPI mints for it.
- No other page describes the stack today: `docs/security/enforcement.md` and the baseline name
  neither CrowdSec nor its controls, so `alerts.md` and the runbook are the whole record. A page
  that inventories the layer is worth one, and is not in this change.

## 11. What is left to the builder

- The exact AppSec resource numbers, after one night.
- The `capi_whitelists` → centralized allowlists migration, which is a `cscli` call and therefore
  the operator's, not the plan's.
- The FileBrowser slice and its excluded locations, once the first route has a week of data.
