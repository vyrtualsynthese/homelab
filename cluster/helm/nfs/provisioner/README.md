# nfs-provisioner

One wrapper chart for every NFS export the cluster mounts, built on the upstream
[`nfs-subdir-external-provisioner`](https://github.com/kubernetes-sigs/nfs-subdir-external-provisioner) chart.
Each export is a separate release of this chart with its own values file in `values/`; the sharing is the
chart, not the release, because each provisioner needs its own name and StorageClass.

## Adding or changing an export

1. Copy `values/<closest export>.yaml` to `values/<release>.yaml` and set `nfs.path`,
   `nfs.volumeName`, `storageClass.name`, and `storageClass.accessModes` if the new export differs.
2. Add an Application under `cluster/argocd/projects/nfs/` whose `path` is
   `cluster/helm/nfs/provisioner` and whose `helm.valueFiles` names that file. Its `metadata.name` is the
   Helm release name.

`storageClass.accessModes` copies the live pair, it is not derived from how many pods mount the
export: `plex-media` is read by eight applications and is still `ReadWriteOnce`, because all eight
run on the same node, while `nfs-audio` and `nfs-books` are `ReadWriteMany`.

## Two fields that must not drift

`fullnameOverride` and `nameOverride` are what keep the live names stable: `pv-<fullname>`,
`pvc-<fullname>`, the Deployment, the ServiceAccount and the `app:` selector.

- Change `fullnameOverride` and the sync succeeds while quietly creating a second PV and PVC; the
  applications that mount `pvc-<fullname>` by name are left on the old claim.
- Change `nameOverride` and the sync fails: it moves the Deployment's `app:` selector, which is
  immutable.

Read both from the cluster before editing.

## `mountOptions` is a switch, not a setting

The upstream chart renders the static PV and PVC only while `nfs.mountOptions` is non-empty. Emptying the
list removes both objects from the render and the next sync prunes them.

`charts/` and `Chart.lock` are built by ArgoCD at sync time and ignored by git; nothing here is applied by
hand.
