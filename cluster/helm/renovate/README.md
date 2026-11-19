# renovate

Renovate opens the pull requests that bump every pinned image and chart version in this repo. This
chart is a wrapper over the upstream `renovate` chart **46.443.0** from
`https://docs.renovatebot.com/helm-charts`, pinned to app version **44.251.0**. It is a batch job,
not a service: there is no port, no Service, no probe and no ingress, and a run's exit status is its
health signal — the same shape as `media-stack/recyclarr`.

## How this chart is deployed here

Not with `helm install`. ArgoCD renders it from `cluster/argocd/projects/renovate.yaml`, which points
at this directory only. The release name is the Application name, `renovate`, in namespace `renovate`.

```bash
helm dependency build cluster/helm/renovate
helm template renovate cluster/helm/renovate -n renovate
```

`charts/` and `Chart.lock` are **not committed**; `.gitignore` lists both paths because ArgoCD runs
`helm dependency build` at sync time. A local render needs the command once.

It renders two objects: ConfigMap `renovate-config` (the self-hosted config, `{}`) and CronJob
`renovate`.

## One source of truth for the version

Before 2026-11-19 the Application pinned the chart in its `targetRevision` while `values.yaml` pinned
an image tag, so Renovate's own chart bump landed in one file and the image bump in another. Now the
chart version lives in `Chart.yaml`'s dependency and nowhere else, and the image tag lives beside it
in `values.yaml`, kept equal to `appVersion`. Renovate's `helmv3` manager bumps the dependency and
its `docker` manager bumps the tag; nothing in `cluster/argocd/` names a version any more.

## Schedule

`03:00 Europe/Paris`, one run at a time (`Forbid`), one retry then fail (`backoffLimit: 1`), a 90
minute deadline, and three days of finished Jobs. The slot is after the *arrs' midnight housekeeping
and before the 04:30 database dump; a run has taken 18 to 39 minutes. The timezone is explicit because
the CronJob controller runs in UTC and an unqualified schedule changes its local meaning with the
offset.

## The token

The Secret `renovate` in namespace `renovate` holds the GitHub token; the chart exports every key in
it, so the key is `RENOVATE_TOKEN`. Nothing in this chart knows the value, and the read-only cluster
account cannot read Secrets, so the token's type, scopes and expiry are recorded by hand in
[`docs/runbooks/renovate.md`](../../../docs/runbooks/renovate.md).

## Why the log level stays at the default

The chart contract asks for `warning` and above. Renovate is the exception, the way `recyclarr` is:
its nightly log is 300-350 lines and about 16 KB, it is the only per-repository record of what was
scanned, and nothing else counts it. A run that reports the repositories it found is the evidence
the repair was verified against; `warning` would leave an empty log on success.

## What is not measured

`resources` is a protection ceiling, not a measured working set: a run is shorter than Prometheus's
scrape interval, so there is no steady state to size from. It is 256Mi requested and 1Gi limited.
The pod also still runs under the namespace's `default` ServiceAccount, which mounts a token Renovate
never uses; the upstream chart cannot turn the token off without an `extraObjects` manifest.
