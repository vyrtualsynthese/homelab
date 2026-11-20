# atlantis

Atlantis reads the pull requests of `infra-main`, runs `tofu plan` on every stack
a pull request touches, and comments the result. It does not apply: `apply` is
disabled and also left out of the command allowlist, so a merge is the only thing
that changes infrastructure.

The wrapper chart adds the routes; everything else is the upstream `atlantis`
chart, pinned in `Chart.yaml`.

## Two hosts, two audiences

| Host | Path | Gate | Certificate |
|---|---|---|---|
| `atlantis.kub.home.ashudev.com` | `/events` only, everything else 404 | none — GitHub must post here | Let's Encrypt (`myresolver`) |
| `atlantis.kub.local` | everything | `kube-system-lan-only` | the cluster's internal certificate |

The webhook route carries no middleware because GitHub cannot complete a sign-in
redirect, and it is narrowed to `/events` so the UI is not reachable through it.
The UI has no gate beyond the network floor; Atlantis has no sign-in of its own
beyond optional basic auth, which this chart does not enable.

Both names resolve already: `*.home.ashudev.com` is a wildcard record in the
`ashudev.com` zone, and `*.kub.local` is the homelab resolver's wildcard.

## The Secret

Created by hand, never in git:

```bash
kubectl create namespace atlantis --dry-run=client -o yaml | kubectl apply -f -
kubectl -n atlantis create secret generic atlantis-env \
  --from-literal=OVH_ENDPOINT=ovh-eu \
  --from-literal=OVH_APPLICATION_KEY=... \
  --from-literal=OVH_APPLICATION_SECRET=... \
  --from-literal=OVH_CONSUMER_KEY=... \
  --from-literal=AWS_ACCESS_KEY_ID=... \
  --from-literal=AWS_SECRET_ACCESS_KEY=... \
  --from-literal=AWS_DEFAULT_REGION=gra \
  --from-literal=ATLANTIS_GH_TOKEN=... \
  --from-literal=ATLANTIS_GH_WEBHOOK_SECRET=...
```

`AWS_*` holds the OVH Object Storage pair, not AWS credentials: the state backend
speaks the S3 API, and no stack in `infra-main` declares the AWS provider any
more. `ATLANTIS_GH_TOKEN` doubles as `TF_VAR_GIT_TOKEN`, because the `github`
provider manages branch protection and a workflow token cannot.

`ATLANTIS_GH_WEBHOOK_SECRET` must be the same value registered on the GitHub
webhook. Without it Atlantis refuses every delivery.

Rotating either credential pair is a Secret update and a pod restart; nothing in
this chart holds a credential, so the chart does not change.

## The webhook

Repository settings of `vyrtualsynthese/infra-main` → Webhooks:

- Payload URL `https://atlantis.kub.home.ashudev.com/events`
- Content type `application/json`
- Secret: the same `ATLANTIS_GH_WEBHOOK_SECRET`
- Events: pull requests, pull request reviews, issue comments, push

Atlantis can create it itself when `ATLANTIS_GH_TOKEN` carries `admin:repo_hook`;
the hand-made one is the way to recover if that scope is missing.

## What a failed plan looks like

`atlantis/plan` is not a required status check yet. Until it is, a red plan on a
pull request informs the reviewer but does not block the merge — the apply still
runs from CI and prints the same plan before applying it. Making it required is
a branch-protection change in `infra-main`, not a change here.

## Bumping

Renovate bumps `image.tag` in `values.yaml` and the dependency version in
`Chart.yaml`. `defaultTFVersion` is not Renovate's business: it is the other half
of `infra-main`'s `.opentofu-version` pin, and the two move in the same change.
