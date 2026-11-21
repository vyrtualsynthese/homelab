# minecraft-server

A Paper Minecraft Java server (`ghcr.io/itzg/minecraft-server`) with an
`itzg/mc-monitor` sidecar that serves Prometheus metrics, on a world claim the
chart creates.

The live Application is `cluster/argocd/projects/minecraft/minecraft-server.yaml`.
The contract this chart is held to is [`docs/charts/README.md`](../../../../docs/charts/README.md).

## State: the server is intentionally off

`replicaCount: 0`. The chart is repaired, not launched: a public game port with no
anti-cheat, no anti-grief plugin and no backup automation should not answer the
internet. The remaining work is tracked on the board; set `replicaCount: 1` when
it lands, and not before.

While it is off, ArgoCD reports the Application as **Progressing**, not
`Healthy`: the claim is `WaitForFirstConsumer`, so `minecraft-server-data` stays
`Pending` until a pod uses it. That is the expected state of this chart, not
drift — the Application is `Synced` against `develop`, and the Deployment is at
0/0.

The `IngressRouteTCP` is kept exactly as the live object has it — Traefik's `mc`
entryPoint, `HostSNI(*)`, this chart's Service. At the router, `25565` must stay
closed until the security work says otherwise. An HTTP `lan-only` middleware does
not apply to a TCP router, so the route's presence is not a gate.

## What it renders

| Object | Name |
|---|---|
| Deployment | `<fullname>` (the server plus the `-monitor` sidecar) |
| Service | `<fullname>`, ClusterIP, port `25565` |
| ServiceAccount | `<fullname>`, token not mounted |
| PersistentVolumeClaim | `<fullname>-data`, `local-path`, `20Gi` |
| IngressRouteTCP | `<fullname>-public`, entryPoint `mc` |

Nothing here is exposed over HTTP.

## Deviations from the contract, and why

- **The claim is `-data`, not `-config`, and it is `20Gi`.** It holds a Minecraft
  world, not a settings file. `local-path` is hostPath-backed, so the world lives
  on the `streaming` node's `/mnt/ssd0` and follows that node, not the cluster.
- **The pod starts as root and the image demotes itself to uid 1000.** The
  entrypoint rewrites its `minecraft` account, chowns `/data`, then `gosu`s to
  1000, so the game process is not root. It keeps `CHOWN`/`SETUID`/`SETGID` for
  that path; `allowPrivilegeEscalation: false` and the runtime seccomp profile are
  set. Running as 1000 from the start would drop those capabilities, but it
  depends on `HOME` being writable, which the image does not set, and no pod has
  run to prove it. That is a first-start experiment, not a chart default.
- **The world claim is new and empty.** The old claim `minecraft-lh-2` died with
  Longhorn; the world it held was abandoned by decision
  ([longhorn-disposition](../../../../docs/cleanup/longhorn-disposition.md)). A
  first start is a new world, not a recovery.
- **No CPU limit.** The tick loop is the workload; a throttled server is worse than
  a busy one. The CPU request is a full core because that is what the tick thread
  needs.
- **Readiness and liveness share the TCP connect to the game port.** A Minecraft
  server has no separate dependency that can fail while the JVM stays up, and no
  unauthenticated HTTP endpoint. The port opens when the world is loaded. Sharing
  is the honest choice here, not a shortcut.
- **No HorizontalPodAutoscaler.** A world is a single writer; two replicas would be
  two worlds. The old `autoscaling/v2beta1` template was removed with the rest of
  the scaffold.

## Known debt

- Anti-cheat, anti-grief and backup are **not in this chart yet**. They are the
  security programme's work, not a chart bug.
- Paper's log level has no environment variable: it lives in
  `/data/config/log4j2.xml`, which the image generates on first start and the chart
  does not own. Until a ConfigMap or an init step owns that file, Paper logs at
  `info` and Loki receives it unfiltered. Recorded here rather than hidden.
- The heap (`MAX_MEMORY: 6G`), the memory reservation and the Meowice JVM flags are
  **unmeasured**. The server has not run since the Longhorn failure.
- The four plugin URLs are pinned by hand. Renovate does not follow them.
- `mc-monitor` polls the server over the pod loopback; the exporter is scraped on
  port 8080 by the annotation-based `kubernetes-pods` job. It does not watch
  multiple servers, and it has no per-player detail beyond what the server list
  ping returns.

## First start, in order

1. Only after the anti-cheat, anti-grief, backup and access work is accepted.
2. Set `replicaCount: 1` in `values.yaml`.
3. The claim binds on the first pod that uses it (`WaitForFirstConsumer`), so the
   pod must schedule on `streaming` for the claim to exist. There is no old pod to
   compete with: the previous one has been Pending since 2026-08-28.
4. Watch the first boot: the startup probe allows ten minutes. A world download of
   the plugin jars needs egress to github.com and download.luckperms.net.
5. Confirm uid 1000 owns what it writes:
   `kubectl -n minecraft exec deploy/minecraft-server -c minecraft-server -- ls -ln /data`
6. Join from the LAN through the `mc` entryPoint. `OPS` names `vsynthese`; set the
   whitelist before the port is opened at the router.
7. The world is new. Do not expect the pre-Longhorn map.

## Verification (no cluster needed)

```bash
helm lint . --namespace minecraft
helm template minecraft-server . --namespace minecraft | kubeconform -strict -summary
./hack/check-exposure.sh --only minecraft
```

`kubeconform` has no schema for `traefik.io/v1alpha1`, so it reports
`IngressRouteTCP` as an error on every chart in this repo; the other four objects
validate. The exposure checker renders the chart and counts the TCP route, which
is not an HTTP route and carries no middleware.
