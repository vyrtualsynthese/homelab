# The VueTorrent installer — repair instead of rebake

2026-11-20 · branch `feature/qbittorrent-vuetorrent-pref-repair` · board #320 · no image published,
by the operator's choice on 2026-11-20

## Why

#320 asked for the VueTorrent files to be baked into a pinned image so a first boot needs no network.
The operator declined the image on 2026-11-20: it would need a package published to a registry the
cluster can pull without a secret, which is a new operational surface for a static asset of six
megabytes.

What survives is the defect the card found beside it, and it is the one that bites in practice: when
`/config/ui` is missing, qBittorrent writes `WebUI\AlternativeUIEnabled=false` to its own config. The
next boot installs the files again but the preference stays false, so the stock WebUI is served over
a perfectly good installation until someone runs the recovery `curl` in `values.yaml`.

Two smaller defects ride along: a `/config/ui` whose `version.txt` matches but whose `public/index.html`
is gone (the hand-flattened tree of 2026-11-18) is read as "already installed" and left broken; and a
failed download leaves nothing to retry with once the pod has network again.

## What success looks like

- An install that succeeds leaves qBittorrent serving VueTorrent, with no manual step: the preference
  is set before the app reads it.
- A half-populated `/config/ui` is reinstalled, not trusted.
- A failed download costs nothing: the next boot retries, and a zip fetched once can be reused
  offline.
- A **first** boot with no route to github.com still serves the stock WebUI. That is accepted, not a
  regression: the card's first "Done when" is out of scope with the image declined.

## Facts the fix rests on

- `qBittorrent.conf` is at `/config/qBittorrent/qBittorrent.conf`. The LinuxServer image's own service
  script greps exactly that path (`root/etc/s6-overlay/s6-rc.d/svc-qbittorrent/run`).
- The release asset extracts to `vuetorrent/version.txt` and `vuetorrent/public/index.html`, and the
  installer copies `vuetorrent/` to `/config/ui`. So the two things worth checking are
  `/config/ui/version.txt` and `/config/ui/public/index.html`.
- The init container runs before the app container starts, as root, and nothing else holds the config
  file at that moment. That is the only safe window to write it: qBittorrent rewrites the whole file
  from memory on every preference change and on exit.

## The change

All of it is inside `init-qbittorrent` in `cluster/helm/media-stack/qbittorrent/templates/deployment.yaml`,
plus the comments and README that describe it.

1. **Install condition** — the no-op branch needs both facts: `version.txt` equals `vueTorrent.version`
   **and** `public/index.html` exists. Version equal but file missing logs why and reinstalls.
2. **Integrity before swap** — after `cp -a` into `ui.next`, require `ui.next/public/index.html`. If it
   is absent, discard the staging directory and leave the old UI in place.
3. **Preference repair** — after a verified install, set `WebUI\AlternativeUIEnabled=true` in the
   config file, then `chown` it to `PUID`/`PGID` so the app can still save preferences:
   - key present: replace its value;
   - key absent: insert it under `[Preferences]`;
   - file absent (a fresh claim): create it with a `[Preferences]` header and that one key.

   Only after an install, so an operator who turns the alternative UI off in the WebUI keeps it off.
4. **Zip cache** — keep the fetched archive as `/config/vuetorrent-<version>.zip`; when a later boot
   needs the same version and the download fails, unzip the cached copy instead of falling back to
   the stock UI.
5. **Real ownership of the environment** — the init container gains `PUID` and `PGID` from `.Values.env`,
   which it needs for the `chown`.

## Non-goals

- Publishing an image, adding CI, or vendoring the tree in git.
- Making the very first boot work offline.
- Changing `vueTorrent.version`, `initTag` or the Renovate annotations: both are pinned and tracked.
- Touching the two `WebUI\Auth*` preferences: they live on the claim and are re-applied by hand on a
  rebuilt volume, as the README says.

## Verification

- `helm template` renders; the script runs under `sh` with stubs for `wget`/`apk`/`unzip`, on four
  fixtures: fresh conf, key false, key absent, no conf at all. Each must end with the key `true` and
  the file owned by 1100.
- The no-op path with a matching `version.txt` and a missing `index.html` reinstalls.
- The download-failure path with a cached zip and no network installs from the cache.
- `hack/check-exposure.sh` and `hack/check-app-version.sh` still pass; no image tag changes.
- On the cluster: one rollout, init log reads `installed` or `nothing to do`, and the WebUI serves
  VueTorrent after a config rewrite.
