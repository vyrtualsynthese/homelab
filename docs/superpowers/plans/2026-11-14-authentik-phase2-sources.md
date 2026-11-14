# Authentik phase 2 — Discord and Plex as sign-in sources

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A person can sign in to authentik with Discord or Plex. Discord admits only members of the
homelab server; Plex admits the owner and anyone the AshuFlex server is shared with. Both land in
`users`.

**Architecture:** The two sources, the Discord guild-membership property mapping, the admission
policy and the two flow updates live in one blueprint file mounted through the existing ConfigMap.
Credentials arrive through a second Secret, `authentik-sso`, wired with `envFrom`; the blueprint
reads them with `!Env`. The PostSync hook from phase 1 restarts the worker, which applies the file.

**Tech Stack:** authentik 2026.8.5, Helm, ArgoCD, Discord OAuth2, plex.tv.

**Spec:** `docs/superpowers/specs/2026-11-14-authentik-sso-design.md` (§5.4, phase 2)

## Global Constraints

- Chart and image stay at `2026.8.5`; no version change in this phase.
- Namespace `authentik`; hostname `authentik.kub.home.ashudev.com`.
- Discord source slug `discord`, Plex source slug `plex`; the Discord callback is
  `https://authentik.kub.home.ashudev.com/source/oauth/callback/discord/`.
- Secrets never enter git. `authentik-sso` holds `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET`,
  `PLEX_TOKEN`. The Discord guild ID is not a secret and lives in the blueprint.
- One blueprint file, `files/sources.yaml`; the file name is what appears in the worker log.
- `users` group exists from phase 1; new source users join it through the enrollment flow's
  user-write stage.
- No `syncPolicy.automated` in the Application; the operator syncs from the UI.
- Emoji commit prefixes; never commit to `main` or `develop`.

## Review Focus

1. **A blueprint is one transaction.** One wrong field name fails the whole file and nothing in it
   takes effect, including the flow updates. The worker log names the file; Task 3 checks the login
   page and the log before calling it done.
2. **`!Env` with a missing key fails the file.** The secret must exist with all three keys before
   the sync; Task 1 lists them, Task 2 wires it.
3. **Missing `is_member` denies everyone.** The policy fails closed: if the property mapping is not
   attached to the source, `prompt_data.attributes.discord` is absent and every Discord sign-in is
   refused. Task 3 checks the attribute on the created user, not just that a login happened.
4. **Wrong stage, no buttons.** The sources are shown by updating
   `default-authentication-identification`. If the name is wrong the login page still works but
   offers only the password form. Task 3 asserts both buttons on the page.
5. **Re-applying must be a no-op.** Every entry is identified by `slug` or `name`, so a second
   sync updates rather than duplicates. Task 3 syncs twice and checks that no duplicate source
   appears.

---

### Task 1: Discord application, Plex token, guild ID, secret

**Files:** none (operator actions and cluster secret).

**Interfaces:**
- Produces: Discord client ID and secret, homelab guild ID, Plex token; Secret
  `authentik/authentik-sso` with keys `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET`, `PLEX_TOKEN`.

- [ ] **Step 1: Create the Discord application**

1. Open <https://discord.com/developers/applications> and press **New Application**.
2. Name it `Ashulab SSO`, accept the terms, **Create**.
3. Open **OAuth2**. Copy **Client ID**.
4. Press **Reset Secret**, confirm, copy the secret once.
5. Under **Redirects**, add exactly:
   `https://authentik.kub.home.ashudev.com/source/oauth/callback/discord/`
6. Save. No bot, no privileged intents, no OAuth2 URL Generator.

- [ ] **Step 2: Copy the server ID**

Discord → **User Settings → Advanced → Developer Mode** on; right-click the homelab server icon →
**Copy Server ID**. It is a long number and goes in the blueprint, not the secret.

- [ ] **Step 3: Copy the Plex token**

Sign in to <https://app.plex.tv> as the server owner. Open the browser developer tools → **Network**,
filter for `plex.tv`, reload, open any request, and copy the `X-Plex-Token` value from its request
headers. This token belongs to the owner account; authentik keeps it to check that it stays valid.
Plex admission does not use the friend list (see Step 2).

- [ ] **Step 4: Create the secret**

Run as the operator, substituting the three values. Do not paste them into a commit or an issue.

```bash
kubectl -n authentik create secret generic authentik-sso \
  --from-literal=DISCORD_CLIENT_ID='<client-id>' \
  --from-literal=DISCORD_CLIENT_SECRET='<client-secret>' \
  --from-literal=PLEX_TOKEN='<plex-token>'
kubectl -n authentik get secret authentik-sso -o json | jq -r '.data | keys[]'
```

Expected: the three key names, in one line each.

- [ ] **Step 5: Check the Discord redirect is reachable**

```bash
curl -s -o /dev/null -w '%{http_code}\n' \
  https://authentik.kub.home.ashudev.com/source/oauth/callback/discord/
```

Expected: `400` or `405` (the route exists and rejects a bare GET without a code), not `404`.

### Task 2: Wire the secret and add the sources blueprint

**Files:**
- Modify: `cluster/helm/authentik/values.yaml`
- Create: `cluster/helm/authentik/files/sources.yaml`

**Interfaces:**
- Consumes: Task 1's secret and guild ID.
- Produces: sources `discord` and `plex`, property mapping `discord-guild-membership`, policy
  `discord-guild-admission`, both sources on the login page, `users` as the enrollment group.

- [ ] **Step 1: Add the secret to the environment**

In `values.yaml`, under `authentik.global.envFrom`, keep `authentik-env` and add:

```yaml
      - secretRef:
          name: authentik-sso
```

- [ ] **Step 2: Write `files/sources.yaml`**

Replace `<guild-id>` with the number from Task 1 Step 2. Keep the rest verbatim; the field names are
the contract.

```yaml
version: 1
metadata:
  name: ashulab-sources
entries:
  - model: authentik_sources_oauth.oauthsourcepropertymapping
    id: discord-guild-membership
    identifiers:
      name: discord-guild-membership
    attrs:
      expression: |
        guild_id = "<guild-id>"
        guild_url = f"https://discord.com/api/v10/users/@me/guilds/{guild_id}/member"
        response = client.do_request("GET", guild_url, token=token)
        if response.status_code == 404:
            is_member = False
        else:
            response.raise_for_status()
            is_member = True

        return {
            "name": info.get("global_name") or info.get("username"),
            "attributes": {
                "discord": {
                    "id": info.get("id"),
                    "username": info.get("username"),
                    "guild_id": guild_id,
                    "is_member": is_member,
                },
            },
        }

  - model: authentik_sources_oauth.oauthsource
    id: discord-source
    identifiers:
      slug: discord
    attrs:
      name: Discord
      provider_type: discord
      consumer_key: !Env DISCORD_CLIENT_ID
      consumer_secret: !Env DISCORD_CLIENT_SECRET
      additional_scopes: "guilds guilds.members.read"
      promoted: true
      enabled: true
      authentication_flow: !Find [authentik_flows.flow, [slug, default-source-authentication]]
      enrollment_flow: !Find [authentik_flows.flow, [slug, default-source-enrollment]]
      user_matching_mode: identifier
      user_property_mappings:
        - !KeyOf discord-guild-membership

  - model: authentik_sources_plex.plexsource
    id: plex-source
    identifiers:
      slug: plex
    attrs:
      name: Plex
      plex_token: !Env PLEX_TOKEN
      allow_friends: false
      allowed_servers:
        - "<AshuFlex machine identifier>"
      promoted: true
      enabled: true
      authentication_flow: !Find [authentik_flows.flow, [slug, default-source-authentication]]
      enrollment_flow: !Find [authentik_flows.flow, [slug, default-source-enrollment]]
      user_matching_mode: identifier

  - model: authentik_policies_expression.expressionpolicy
    id: discord-guild-policy
    identifiers:
      name: discord-guild-admission
    attrs:
      expression: |
        from authentik.sources.oauth.models import OAuthSource

        source = request.context.get("source")
        if not isinstance(source, OAuthSource) or source.provider_type != "discord":
            return True

        discord = request.context.get("prompt_data", {}).get("attributes", {}).get("discord", {})
        if not discord.get("is_member"):
            ak_message("You are not a member of the homelab Discord server.")
            return False
        return True

  # Bind by !KeyOf, never by !Find: a policy binding's target is the flow's
  # policy-binding UUID, while !Find returns the flow's own UUID.
  - model: authentik_flows.flow
    id: source-enrollment-flow
    identifiers:
      slug: default-source-enrollment

  - model: authentik_flows.flow
    id: source-authentication-flow
    identifiers:
      slug: default-source-authentication

  - model: authentik_policies.policybinding
    identifiers:
      target: !KeyOf source-enrollment-flow
      order: 100
    attrs:
      policy: !KeyOf discord-guild-policy

  - model: authentik_policies.policybinding
    identifiers:
      target: !KeyOf source-authentication-flow
      order: 100
    attrs:
      policy: !KeyOf discord-guild-policy

  - model: authentik_stages_identification.identificationstage
    identifiers:
      name: default-authentication-identification
    attrs:
      show_source_labels: true
      sources:
        - !KeyOf discord-source
        - !KeyOf plex-source

  - model: authentik_stages_user_write.userwritestage
    identifiers:
      name: default-source-enrollment-write
    attrs:
      create_users_group: !Find [authentik_core.group, [name, users]]
```

- [ ] **Step 3: Render and assert**

```bash
helm template authentik cluster/helm/authentik -n authentik > /tmp/opencode/authentik-phase2.yaml
grep -q 'name: authentik-sso' /tmp/opencode/authentik-phase2.yaml
grep -q 'sources.yaml' /tmp/opencode/authentik-phase2.yaml
grep -q 'authentik.kub.home.ashudev.com' /tmp/opencode/authentik-phase2.yaml
```

Expected: all three succeed. The blueprint's YAML is only validated by authentik at apply time, so
Task 3 is the real test.

- [ ] **Step 4: Commit**

```bash
git add cluster/helm/authentik/values.yaml cluster/helm/authentik/files/sources.yaml
git commit -m "✨ feat(authentik): Discord and Plex sign-in sources with a guild gate"
```

### Task 3: Publish, sync and verify both sign-ins

**Files:**
- Modify: `docs/status.md`

**Interfaces:**
- Consumes: Tasks 1 and 2.
- Produces: working sources, evidence, and a status-page note.

- [ ] **Step 1: Publish**

```bash
git fetch origin && git rebase origin/develop
git push -u origin feature/authentik-phase2
```

Open the pull request against `develop`; the operator merges and syncs `authentik` from the UI.

- [ ] **Step 2: Check the worker applied the file**

```bash
kubectl -n authentik logs deploy/authentik-worker --tail=300 | grep -iE 'sources.yaml|blueprint|traceback'
```

Expected: `apply_blueprint` finishes with no exception for `sources.yaml`; no traceback. If the file
fails, the log names the failing entry and nothing in the file took effect.

- [ ] **Step 3: Check both buttons and both flows**

Open `https://authentik.kub.home.ashudev.com` in a private window. Expected: **Discord** and **Plex**
buttons above the username field.

- [ ] **Step 4: Sign in with Discord as a member**

Use the owner's Discord account. Expected: the account is created, lands in `users`, and the
authorisation succeeds. In **Directory → Users**, open it and confirm the attributes contain
`discord.is_member: true`. That attribute is what the policy reads; without it the denial is silent.

- [ ] **Step 5: Sign in with Plex as the owner or a server member**

Expected: the account is created, lands in `users`. A Plex account with no access to AshuFlex is
refused by the source itself (server access is the allowlist). Do not enable `allow_friends`: Plex
retired the friends endpoint and authentik 2026.8.5 fails with HTTP 410 when it calls it.

- [ ] **Step 6: Prove the guild gate refuses a non-member**

Use a second Discord account that is **not** in the homelab server, or remove a test account from the
server and sign in again. Expected: "You are not a member of the homelab Discord server." and no new
user. If no second account exists, record that this check was not run rather than marking it passed.

- [ ] **Step 7: Re-sync once**

Sync the Application a second time and confirm **Directory → Federation and social login** still
lists exactly one Discord and one Plex source. This proves the blueprint is idempotent.

- [ ] **Step 8: Update the status page**

In `docs/status.md`, extend the `authentik` row's note: sources `discord` and `plex`, Discord gated by
guild membership, Plex by access to the AshuFlex server.

- [ ] **Step 9: Commit the status note**

```bash
git add docs/status.md
git commit -m "📝 docs(status): authentik signs in with Discord and Plex"
git push
```

## Success, in one place

- `authentik-sso` exists with the three keys; the rendered Deployment mounts it.
- The login page offers Discord and Plex.
- A Discord member and a Plex user with access to AshuFlex both reach authentik and appear in `users`.
- A Discord account outside the server is refused with the message from the policy.
- The user's attributes carry `discord.is_member: true`.
- A second sync changes nothing.

## Rollback

Removing `files/sources.yaml` stops the blueprint but leaves the objects it created. To take the
sources off the login page without deleting them, set `enabled: false` on both sources and remove
them from the identification stage's `sources` list; the blueprint then applies that state on the
next sync. Delete the sources in the UI only if they must disappear entirely.

## Failure handling

- **Worker log names `sources.yaml`.** Read the field it complains about; a blueprint is atomic, so
  the login page is unchanged until the file is valid.
- **No buttons on the login page.** The stage name is wrong or the stage update failed; check the
  worker log and `default-authentication-identification`.
- **`!Env` error.** A key is missing from `authentik-sso`; recreate it with all three.
- **Discord login refused for a member.** The property mapping did not run: check that
  `discord-guild-membership` is attached to the source's user property mappings and that the scopes
  include `guilds.members.read`.
- **Plex refused for a server member.** The machine identifier in `allowed_servers` is wrong, or the
  account has no library access to AshuFlex. Re-read it from
  `https://plex.tv/api/v2/resources?X-Plex-Token=...&X-Plex-Client-Identifier=...`.
