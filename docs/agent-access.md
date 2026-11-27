# Reaching the apps without a click

> **In one line.** Nearly every setting an agent needs has an HTTP endpoint, and a call through it
> leaves a record a click does not; this page is how to find the credential, what a working call
> leaves behind, and the one sign-in that stays human.
>
> **Use it when** you are about to ask the operator to click something in an app's UI, a call answered
> `401` or `403` and the next move looks like theirs, or you need to know what the agent may read from
> the cluster · **Verified** 2026-11-26 for the cluster identity, 2026-11-20 for the Argo CD session,
> 2026-11-27 for the router · **Kind:** how-to
>
> **Sources** `~/.config/secrets/homelab.env`, `kubectl/mcp-readonly-rbac.yaml`,
> `~/.kube/mcp-readonly.yaml`, `~/.config/argocd/config`, the calls recorded below

An agent that hands the operator a click has usually not looked for a token. Nearly everything the
protocol asks for — a setting, a provider, a blueprint re-apply, a sync, a board move — has an
endpoint, and the endpoint leaves a record the UI click does not.

The endpoints and the credential each one takes are the table in `AGENTS.md` §9. This page is what a
table cannot carry: what a working call leaves behind, the sign-in that is genuinely the operator's,
and the traps around the one that has no key.

## Three specimens from one session, in the order they cost time

- **The setting.** `authenticationMethod` on two Radarr instances, changed twice, each attempt a
  `PUT /api/v3/config/host` with the whole object. The response said `202`, and reading the object
  back proved what had landed. The UI would have shown a form, not a value.
- **The cleanup.** Two orphaned authentik proxy providers, deleted with
  `DELETE /api/v3/providers/proxy/{pk}/` after checking that both applications already pointed at
  their replacements and that no outpost listed them. Asked to do it by hand, the operator answered
  that the token existed — it did, in the same env file as every other key.
- **The re-apply.** A blueprint whose first sync rolled back is fixed by
  `POST /blueprints/instances/{pk}/apply/`, or by any later commit that changes the file. "Press
  Apply in the UI" is a sentence for the operator's own console, not for an agent to end its turn
  on.

## What stays human, and only this

- **a Secret value the agent has never read.** `view` RBAC excludes Secrets; ask for the key and the
  Secret by name, then continue.
- **a browser sign-in.** An OIDC flow needs one person at one keyboard; verify everything up to the
  redirect and ask for the click. The Argo CD login below is the shape to copy: the agent runs the
  command, the operator pastes one URL, the agent carries on with the session.
- **a merge.** The branch and the PR are the agent's; the merge is the operator's.

## The cluster read, and the one identity behind it

Reading the cluster is not a second credential beside the MCP. The tools authenticate as
`kube-system/opencode-mcp` through `~/.kube/mcp-readonly.yaml`, and a shell reads as that account
when `KUBECONFIG` names the same file — `kubectl auth whoami` is how to find out which identity you
have. What the account may read is exactly what `kubectl/mcp-readonly-rbac.yaml` grants: the built-in
`view` role, the cluster-scoped reads `view` leaves out (nodes, persistent volumes, namespaces,
storage classes, IngressClasses, ClusterRoles and ClusterRoleBindings), the CRD groups this
repository uses, the K3s addon objects, and read on upgrade `Plan`s in `system-upgrade`. It reads no
Secret, and it writes, deletes, `exec`s and `port-forward`s nothing.

The manifest is applied by hand, so live and git drift silently until someone re-applies it. That is
how a second, older read-only identity survived a year without a record: the `mcp-reader`
ServiceAccount, a narrower `mcp-read-only` ClusterRole and `mcp-reader-binding`, left behind when the
account the tools use was replaced. Nothing referenced it and no token for it existed, but it was a
live cluster-wide read grant that no file described. It was deleted on 2026-11-26, in the same sweep
as seven `cluster-admin` bindings that dead Helm releases had left on orphaned `helm-*`
ServiceAccounts in `default` and `kube-system`; the upgrade controller's own `system-upgrade` grant
stayed, because that controller still runs.

Prove the grant, and prove it again after an edit:

```bash
kubectl apply -f kubectl/mcp-readonly-rbac.yaml           # admin; this file is not GitOps-managed
KUBECONFIG=~/.kube/mcp-readonly.yaml kubectl auth whoami                               # the account
KUBECONFIG=~/.kube/mcp-readonly.yaml kubectl auth can-i list clusterrolebindings       # yes
KUBECONFIG=~/.kube/mcp-readonly.yaml kubectl auth can-i list secrets -n kube-system    # no
```

The same read is what lets a session audit the rest: `kubectl get clusterrolebinding -o wide` names
every cluster-admin on the cluster, and on 2026-11-26 the only two left were the built-in
`system:masters` one and the upgrade controller's.

## The Argo CD session, and the traps that surround it

Argo CD has no key in `~/.config/secrets/homelab.env`. The CLI keeps its own session in
`~/.config/argocd/config`, and the only way in is the authentik sign-in, which from this workstation
is one command and one paste:

```bash
argocd login argocd.kub.internal --sso --sso-launch-browser=false --grpc-web
# it prints "To authenticate, copy-and-paste the following URL…" and then waits, on port 8085
powershell.exe -NoProfile -Command "Start-Process '<the URL that line printed>'"
```

The browser is the Windows one, reached through `powershell.exe` from the WSL shell. Four details
decide whether that works; all were measured on 2026-11-20 against Argo CD v3.6.1 with CLI v3.5.6.

- **`--sso-launch-browser=false` is not a preference.** Left at its default `true`, the login runs
  `xdg-open`, which is not installed here: it prints the URL and then dies with
  `exec: "xdg-open": executable file not found in $PATH`. The flag makes it wait instead.
- **`--grpc-web` belongs on the login line, and later calls inherit it.** The session file records
  `grpc-web: true` for that server, so `argocd app list -o name` needs no flag afterwards.
  `argocd-server` serves plain HTTP behind Traefik (`server.insecure: true`), which does not carry
  gRPC — the binary call format — to the end of the hop: a context created without the flag prints
  `Failed to invoke grpc call. Use flag --grpc-web in grpc calls` on every command, then retries
  over gRPC-web, the HTTP encoding of the same call. The call still lands; what the flag buys is no
  failed attempt and no warning in front of every answer.
- **The URL lives only as long as the login process.** It points at the listener on port 8085 that
  the waiting process opened, so the paste happens while the terminal waits.
- **The session lasts 24 hours.** The token the login stored carried `iat 01:07Z` and `exp 01:07Z`
  the next day, and nothing renews it: a session from yesterday answers an auth error and the login
  is rerun. Losing it costs one paste, not a rediscovery.

Then the caveat that makes a finished sync look broken:

- **A Replace sync of a chart with a bound claim ends `Failed`, and the Deployment is still
  replaced.** `argocd app sync --replace` replaces each object instead of patching it (a "sync with
  Replace", in the UI's words); the claim — the PersistentVolumeClaim holding the volume — cannot
  be replaced, because Kubernetes froze `spec.volumeName` when it bound, so the API refuses that
  one object. The rest applies, the Application stays `Synced`/`Healthy`, and the operation state
  reads `Failed`. Read the operation, not the health badge, and do not retry — the retry meets the
  same refusal. Replace syncs are allowed on this cluster: `server.sync.replace.allowed` is unset
  in `argocd-cmd-params-cm`, and Argo CD's default is to allow them, so the refusal comes from the
  claim and not from the server.

## The router, and the login it takes

The Livebox answers its own JSON API at `http://192.168.1.1/ws`, and `LIVEBOX_ADMIN_PASSWORD` in
`~/.config/secrets/homelab.env` is the admin login. It is worth spelling out because the forward
table was treated as "the operator's step" for a while on the strength of a credential nobody had
looked for.

1. `POST /ws` with header `Authorization: X-Sah-Login` and body
   `{"service":"sah.Device.Information","method":"createContext","parameters":{"applicationName":"webui","username":"admin","password":"<admin>"}}`.
   The answer's `data.contextID` is the session.
2. Every later call sends `Authorization: X-Sah <contextID>`, `X-Context: <contextID>` and
   `Content-Type: application/x-sah-ws-4-call+json`, with a body of
   `{"service":"…","method":"…","parameters":{…}}` or `{"object":"DHCPv4.Server.Pool.1.","method":"get"}`.

What answers `status 0`, measured 2026-11-27: `Firewall/getPortForwarding`, `setPortForwarding` (a
throwaway rule, then deleted) and `deletePortForwarding`, which needs `id` and `origin`.
`Devices/get` lists every device with its address and MAC. What does not: the DHCP pool objects
(`DHCPv4.Server.Pool.*`) answer a 504 Gateway Timeout on this firmware, so the pool's bounds and its
reservations cannot be read back — say so instead of guessing them.

A rule is written by posting the whole object back with one field changed, and read back afterwards;
that read is the proof, not the `status 0`.

When no token exists, name the endpoint that would have done the work and file the gap as a card.
The Argo CD login above is what closing such a gap leaves behind: a command in a card, so the next
session starts from it instead of rediscovering `xdg-open`, the port and the flag. Argo CD was that
gap for a while, and "the sync waits for the merge and the poll" was the right answer then. What it
is not, now that the recipe is written, is "sync it in the UI".
