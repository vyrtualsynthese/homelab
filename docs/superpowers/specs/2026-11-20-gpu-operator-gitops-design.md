# NVIDIA GPU operator from git — design

> **Status:** approved 2026-11-20 · **Card:** Vikunja #248 (homelab)
> **Verified against:** cluster state of 2026-11-20 and chart `gpu-operator` v26.3.1

## Intent

The GPU operator is the last sizeable workload applied by hand with `helm upgrade`. Nothing in git
records its chart version, its four container-toolkit settings or the image tags it runs, and
Renovate ignores `kubectl/`, so it only moves when someone remembers. This change puts it behind
the same wrapper-chart + ArgoCD Application pattern every other external chart already uses, pinned
to what is running today, and writes down the one step that stays manual: the host driver.

## What the investigation showed

- **The two restart waves were node reboots, not crashes.** `streaming` booted at 2026-11-16 03:50
  and 2026-11-17 04:21; every gpu-operator pod restarted at that instant with last state `Unknown`
  and exit 255, which is kubelet's shutdown signature. Uptime is 69 h, so nothing has happened
  since. The host-side cause (a ~04:00 reboot, unattended-upgrades being the usual suspect) needs
  `journalctl --list-boots` and `/var/log/apt/history.log` on `streaming`; the read-only MCP cannot
  read node logs.
- **The driver did not change across those reboots.** DCGM reported 580.178.04 before and after,
  from three different pod IPs.
- **`nvidia.com/gpu-driver-upgrade-state=pod-restart-required` is residue.** The operator's upgrade
  controller reports 0 nodes in every state, because its `cluster-policy` selects
  `app=nvidia-driver-daemonset` and with `deploy.driver=pre-installed` that DaemonSet is 0/0. No
  operator action will ever clear the label; it is a stale label from before the driver became
  host-managed.
- **DCGM is scraped through the `nvidia-dcgm-exporter` Service**, in Prometheus's
  `kubernetes-service-endpoints` job. The Service carries `prometheus.io/scrape: "true"` and
  nothing else; the pods carry no annotation. The ClusterPolicy CRD has no field for Service
  annotations (`service` only takes `internalTrafficPolicy` and `type`), so the annotation was set
  by hand at install.

## Goals

- The GPU stack is reproducible from git at pinned versions, reviewed like everything else.
- One GPU stays visible, Plex keeps transcoding through NVENC, DCGM keeps feeding the GPU panels
  and `GpuTemperatureHigh`.
- The migration itself changes nothing on the cluster: same operator version, same image tags, same
  toolkit paths, same runtime class.
- Every remaining hand step is written down.

## Non-goals

- Upgrading the operator. v26.7.3 is current upstream; the bump is a separate change, and the first
  thing Renovate would propose once the chart is in git.
- Moving the host driver into the operator or into Ansible.
- Deleting the `nvidia-driver-daemonset`, `nvidia-mig-manager` and MPS DaemonSets: they cost
  nothing at 0/0 and their presence is how the operator expresses an unneeded component.
- Changing the Prometheus chart.

## Decisions

1. **Keep the full operator.** Dropping NFD is not free: the `nvidia.com/gpu.deploy.*` node labels
   the operator's own DaemonSets select on come from the discovery path, so a lean install means
   hand-managing those labels to save four pods. The operator stays whole.
2. **The driver stays pre-installed and host-managed.** The P400 is Pascal and the host runs a
   moving HWE kernel (6.8.0-149 now); a containerised driver would rebuild against every kernel
   bump. `driver.enabled: false` is how that is declared.
3. **Wrapper chart, pattern D**, at `cluster/helm/platform/gpu-operator`, upstream chart pinned to
   `v26.3.1` — the version running now — with every rendered image tag pinned in `values.yaml`.
4. **NFD workers are pinned to `streaming`.** Nothing in the repo reads NFD labels, and only that
   node has a GPU. NFD master and gc are pinned to `nuc0`, where they already run.
5. **The DCGM Service annotation is declared by a Service this chart owns.** A second Service,
   `nvidia-dcgm-exporter-metrics`, selects the exporter pods on 9400 and carries
   `prometheus.io/scrape: "true"`. The operator will not touch a Service it did not create, so
   there is no ownership fight, and the scrape no longer depends on a hand-typed annotation. The
   stale annotation on the operator's Service is removed once, at cutover, so Prometheus does not
   scrape the same target twice.
6. **`pod-restart-required` is cleared at cutover** with a one-line label overwrite, and the card
   records why the pods did not need a second restart: they already restarted on 11-17.

## Approaches considered

| Approach | Trade-off |
|---|---|
| Wrapper chart + Application, pinned to v26.3.1 (chosen) | Matches every other external chart; migration is a no-op; Renovate covers it afterwards |
| Wrapper chart pinned straight to v26.7.3 | One change instead of two, but mixes a version bump into a migration, so a failure has two possible causes |
| Documented `kubectl apply -f` from `kubectl/` | No ArgoCD integration, no drift correction, keeps the hand step this card exists to remove |

For the DCGM annotation: declaring the operator's own Service in the chart would flap against the
operator forever; a separate Service is the only shape where neither controller fights the other.

## Constraints

- K3s containerd paths stay exactly as they are: config
  `/var/lib/rancher/k3s/agent/etc/containerd/config.toml`, socket
  `/run/k3s/containerd/containerd.sock`, runtime class `nvidia`, set as default.
- Every container gets memory requests and limits; CPU requests stay in the 10m-100m band.
- `cluster/argocd/**` is CRLF.
- No `syncPolicy.automated` in the Application file.

## Left to the builder

The exact memory numbers (sized from measured peaks, not guessed), the chart and Application file
contents, the runbook text, and which existing documents need a line changed.
