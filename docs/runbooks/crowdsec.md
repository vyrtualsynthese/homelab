# CrowdSec: reading the bans, and undoing them

> **In one line.** The agent reads `/var/log/traefik/access.log` on `streaming`, the LAPI holds the
> decisions on `nas`, and the firewall bouncer writes them into `nftables` on `streaming` so a ban
> covers every forwarded port.
>
> **Use it when** a chatty address is being refused, a legitimate visitor was banned, or the
> `SecurityStackBlind` alert says the stack has stopped seeing anything · **Verified** not yet: the
> four proofs below have not been run · **Kind:** how-to
>
> **Sources** `cluster/helm/security/crowdsec/values.yaml`, `ansible/roles/crowdsec/tasks/main.yml`,
> `ansible/roles/crowdsec/templates/bouncer.yaml.j2`, `cluster/helm/monitoring/prometheus/values.yaml`

**Status: pending — the four proofs below have not been run.**

Three pieces do the work. The **agent** reads one file and turns a pattern it recognises into a
**decision** (CrowdSec's instruction to refuse one address for a while). The **LAPI** (CrowdSec's
local API) is the database of decisions and the endpoint every enforcer reads. The **firewall
bouncer** is the host service that turns a decision into a dropped packet. The agent and the bouncer
run on `streaming` because that is where the log and the routing are; the LAPI runs on `nas` because
its SQLite is state, and `nas` is where state is backed up.

## What is where

| Piece | Where | How to look |
|---|---|---|
| Agent | Deployment `crowdsec-agent`, node `streaming` | `kubectl -n security logs deploy/crowdsec-agent` |
| LAPI | Deployment `crowdsec-lapi`, node `nas` | `kubectl -n security logs deploy/crowdsec-lapi` |
| Decisions | LAPI's SQLite, `local-path` claim on `nas` | `kubectl -n security exec deploy/crowdsec-lapi -- cscli decisions list` |
| Bans, as the kernel has them | `nftables` on `streaming`, table `crowdsec` | `nft list set ip crowdsec crowdsec-blacklists` |
| Bouncer | host service on `streaming` | `systemctl status crowdsec-firewall-bouncer` |
| Ban count, and the bouncer's liveness | the bouncer's own listener | `curl -s http://127.0.0.1:60601/metrics \| grep fw_bouncer_banned_ips` |

Three ports, and they are not interchangeable: `30500` is the LAPI's API, the NodePort the bouncer
calls as `http://192.168.1.46:30500/` because a host service cannot resolve cluster DNS; `60601` is
the bouncer's own metrics listener, the one Prometheus scrapes from `nas`; `6060` is the metrics
listener inside each pod. To read the address the bouncer was configured with:
`sudo grep '^api_url' /etc/crowdsec/bouncers/crowdsec-firewall-bouncer.yaml`.

`kubectl exec` needs the admin kubeconfig; the agent's shells are read-only and answer `403`. The
`cscli` calls below are the operator's. The `nft`, `systemctl`, `tail` and `127.0.0.1:60601`
commands run on `streaming`, not on a laptop; the `kubectl` ones run from a laptop with the admin
kubeconfig. The bursts in proofs 1 and 2 come from a phone on mobile data, the one in proof 3 from a
LAN machine.

## The four proofs, run once when this was built

1. **A decision is created.** From an address outside the LAN — a phone on mobile data, against
   `https://ashuvidz.com` — request ten paths that do not exist, inside about ten seconds
   (`for i in $(seq 10); do curl -so /dev/null -w '%{http_code} ' https://ashuvidz.com/no-such-$i; done`).
   `crowdsecurity/http-probing` is five 404s with a ten-second leak, so the burst crosses it.
   `cscli decisions list` names the phone's address with that scenario — the rule that recognised
   the pattern. Undo it afterwards with the command below, or the phone stays refused for the
   decision's four hours.
2. **A decision is enforced.** `nft list set ip crowdsec crowdsec-blacklists` holds the address, the
   phone's browser can no longer open the same URL, and the same URL still answers from a LAN
   machine. That pair is what proves the ban is the firewall's and not Traefik's.
3. **A whitelisted address is never decided.** The same burst from `192.168.1.0/24` creates **no**
   decision at all — the whitelist is a postoverflow (a check that runs before a decision exists, so
   nothing is created and then excused). This is the one proof that costs nothing to run and the one
   that matters most.
4. **A restart keeps the bans.** Restart the agent; the `nft` set is unchanged, because the set lives
   in the kernel and the bouncer, not the agent, owns it. Restart the bouncer too: it re-reads the
   LAPI's decisions and rebuilds the same set, which is the half a naive `nft` script would lose.

## When these were last run

Not yet. The page ships with the layer, so nothing above has been observed on this cluster: it is
what the code is meant to do, not a record of a ban. After the stack converges the operator runs the
four proofs, writes one dated line per proof under the list above with the command and the output it
gave, and the shelf row's status becomes `proved <date>`; in the same commit the banner's
**Verified** and this page's **Status:** line are replaced by that date, so a `proved` shelf row
never sits above a body that still says the proofs were never run. One proof that fails is a fix to
the chart or the role, not a note here.

## Undo a ban

```bash
kubectl -n security exec deploy/crowdsec-lapi -- cscli decisions delete --ip <address>
```

A false positive is the cost of a detector: FileBrowser share links are opened from outside by
people with no account, so a visitor who hammers dead links can trip `http-probing`. Deleting the
decision is enough — the bouncer re-reads the list every ten seconds and the set loses the address
on that pass.

## Turn the whole layer off

On `streaming`, as root:

```bash
sudo systemctl stop crowdsec-firewall-bouncer
sudo nft flush table ip crowdsec
sudo systemctl disable crowdsec-firewall-bouncer   # only if the chart is being removed too
```

The door is then exactly as it was: nothing is dropped, the router still serves, the log keeps being
written and the LAPI keeps recording decisions — they simply have no enforcer. That is the state to
choose while a false positive is being understood, rather than deleting decisions one at a time.
Re-enable by starting the service again: the bouncer re-reads the current decisions, including any
that accumulated while it was stopped.

## When a rule says the stack is blind

`SecurityStackBlind` fires when no new line reaches the log, when the agent has not parsed one, when
the LAPI is unreachable, or when the bouncer's listener is down. In order:

1. `kubectl -n security get pods` — an `Init:0/1` agent is `wait-for-lapi`, so check the LAPI first.
2. `kubectl -n security logs deploy/crowdsec-agent | grep -i 'file\|parse\|acquis'` — a parser that
   matches nothing says so once and then stays quiet.
3. `tail -c 200 /var/log/traefik/access.log` on `streaming` — no recent timestamp means the router
   stopped writing, which is a permission or a rotation problem, not a detector problem.
4. `systemctl status crowdsec-firewall-bouncer` and the `/metrics` curl above.
