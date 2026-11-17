# Reference: the pages you consult

> **In one line.** The tables of current fact — hostnames, charts, metrics, alerts, Secrets — each
> one dated, each one regenerable from the cluster, and none of them teaching you how the place
> works.
>
> **Use it when** you arrive with a question already formed: is this hostname public, which chart do
> I copy, why is this panel blank, what does this alert mean, which Secret is missing · **Verified**
> 2026-11-17 · **Kind:** living page

These pages are long on purpose. A reference page is a table of facts with a date attached and a
command that rebuilds it, so that the same question gets the same answer twice; the argument for why
the place is shaped this way lives in [How it works](../how-it-works/README.md), and the procedure
for changing it lives in [Running it](../running-it/README.md).

## Charts

How a chart is built, and what its numbers were measured against.

| Page | Answers | Read by |
|---|---|---|
| [Chart notes](chart-notes.md) | how each chart meets the contract, where it had to diverge, and which ones have not caught up | whoever is building or reviewing a chart, and wants the worked example closest to theirs |
| [Chart sizing](chart-sizing.md) | which memory requests and limits were measured against Prometheus, what they were before, and why | whoever is setting a request or answering an audit that asks which numbers are evidence |

## Network and identity

What answers on a hostname, and what stands in front of it before the application sees a request.

| Page | Answers | Read by |
|---|---|---|
| [Routes](routes.md) | every hostname Traefik answers: its application, namespace, gate, certificate, and whether it may face the internet | whoever is asking "is this reachable from outside", or debugging a 404 or a certificate |
| [SSO applications](sso-applications.md) | the sign-in mechanism each application uses, the state it is in, the hostname it answers, the traps, and how to add another | whoever is protecting a new application, or whose sign-in surprised them |

## Observability

What the stack records, what it tells you about, and where you look.

| Page | Answers | Read by |
|---|---|---|
| [Metrics](metrics.md) | the trap in each exporter, how long data is kept, and the numbers taken from the live cluster | whoever is charting a series or finding a panel blank |
| [Alerts](alerts.md) | every rule that can fire, what it protects, the silences, the pattern a rule must follow, and the holes nobody watches | whoever was woken by an alert, or is writing one |
| [Dashboards](dashboards.md) | the Homelab screens, what each is for, and the API calls that change one | whoever needs to know where to look, or is adding a panel |

## Secrets and faults

The inventory a rebuild reads first, and the fault that is still open.

| Page | Answers | Read by |
|---|---|---|
| [Secrets](../secrets.md) | every Secret the charts expect to find already in the cluster, its keys, its consumer, and where its value came from — never the value | whoever is stuck in `CreateContainerConfigError`, or rebuilding a namespace |
| [qBittorrent Web UI stalls](../qbittorrent-webui-stalls.md) | what was measured while the Web UI stopped answering, and what the evidence points at | whoever is asked to fix it next, so the session starts from data |

## How to keep a page true

Every page here states the date it was checked and, where it can, the command that reproduces it.
If a page disagrees with the cluster, the page is the older statement: re-measure, then correct the
page in the same change that touches the cluster. A reference page without a date is a claim about a
cluster nobody kept.
