# Reaching the apps without a click

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

## The Argo CD session, and the traps that surround it

Argo CD has no key in `~/.config/secrets/homelab.env`. The CLI keeps its own session in
`~/.config/argocd/config`, and the only way in is the authentik sign-in, which from this workstation
is one command and one paste:

```bash
argocd login argocd.kub.local --sso --sso-launch-browser=false --grpc-web
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

When no token exists, name the endpoint that would have done the work and file the gap as a card.
The Argo CD login above is what closing such a gap leaves behind: a command in a card, so the next
session starts from it instead of rediscovering `xdg-open`, the port and the flag. Argo CD was that
gap for a while, and "the sync waits for the merge and the poll" was the right answer then. What it
is not, now that the recipe is written, is "sync it in the UI".
