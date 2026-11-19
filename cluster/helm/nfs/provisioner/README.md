# nfs-provisioner

One wrapper chart for every NFS export the cluster mounts, built on the upstream
[`nfs-subdir-external-provisioner`](https://github.com/kubernetes-sigs/nfs-subdir-external-provisioner) chart.
Each export is a separate release of this chart with its own values file in `values/`; the sharing is the
chart, not the release, because each provisioner needs its own name and StorageClass.

## Adding or changing an export

1. Copy `values/<closest export>.yaml` to `values/<release>.yaml` and set `nfs.path`,
   `nfs.volumeName`, `storageClass.name`, and — for an export that several pods read at once —
   `storageClass.accessModes: ReadWriteMany`.
2. Add an Application under `cluster/argocd/projects/nfs/` whose `path` is
   `cluster/helm/nfs/provisioner` and whose `helm.valueFiles` names that file. Its `metadata.name` is the
   Helm release name.

## Two fields that must not drift

`fullnameOverride` and `nameOverride` are what keep the live names stable: `pv-<fullname>`,
`pvc-<fullname>`, the Deployment, the ServiceAccount and the `app:` selector. Existing applications mount
`pvc-<fullname>` by name, and a Deployment selector cannot be changed, so a new value means a new claim
and a failed sync. Read them from the cluster before editing.

## `mountOptions` is a switch, not a setting

The upstream chart renders the static PV and PVC only while `nfs.mountOptions` is non-empty. Emptying the
list removes both objects from the render and the next sync prunes them.

`charts/` and `Chart.lock` are built by ArgoCD at sync time and ignored by git; nothing here is applied by
hand.
