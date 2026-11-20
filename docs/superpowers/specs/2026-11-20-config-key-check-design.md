# Config-key check for wrapper charts — design

> **In one line.** A checker that renders a wrapper chart and fails any key in the rendered config
> file that the pinned upstream chart's own `values.yaml` does not declare.
> **Card:** Vikunja #431 (`#347`), `homelab` project.
>
> **Status 2026-11-20.** The mechanism is built and the manifest ships empty. FileBrowser's chart
> became hand-written on 2026-11-20 (commit `6e61060a`), so there is no pinned dependency left to
> read a schema from, and no other wrapper chart has an app that rejects an unknown config key. The
> first entry lands with the next such chart; the FileBrowser entry that proved the checker was
> removed at the rebase.

## Why

`helm template` renders any key happily; the application then refuses to start on a field it does not
know. The FileBrowser cutover failed three syncs in a row, one unknown key at a time
(`docs/superpowers/plans/2026-11-19-filebrowser-quantum.md`), and nothing local could have caught
them. The upstream chart's own `values.yaml` is written against the release's schema — it is the
list of keys the release knows — so comparing the rendered file against it catches the whole class.

## Goals

- Render each opted-in wrapper chart and report every leaf key of its config file that the pinned
  dependency's `values.yaml` does not declare, with the line of the **rendered** config file.
- One command a PR runs, in the `hack/check-app-version.sh` mould: bash driver, Python core, unit
  tests that need neither helm nor network.
- FileBrowser is the first entry; a second app is one manifest entry, no code.

## Non-goals

- Validating values (types, enums, required fields). The app does that at startup; this checks only
  that a key exists in the release at all.
- Detecting charts automatically. A chart whose defaults are not a complete schema would false-alarm,
  and a wrong failure teaches people to ignore the check.
- Checking passwords, secrets or env vars — only files the app reads as configuration.

## Approaches considered

1. **Manifest of opted-in charts** (chosen): `hack/config-key-checks.yaml` names the chart, the
   dependency, the values subtree that is the schema, and the config file key. Explicit; a new chart
   is a deliberate act with a reviewable diff.
2. **Auto-detection:** any wrapper whose dependency declares a `config:` map and whose templates
   render a `config.yaml`. Zero maintenance, but it opts charts in silently and cannot tell a full
   schema from a partial one.

## How it works

`hack/check-config-keys.sh` reads the manifest and, per entry:

1. `helm template <chart-dir-name> <chart> -f <chart>/values.yaml` — the release name is the chart
   directory name, the same convention `AGENTS.md` §6 uses.
2. Pull the dependency's `values.yaml` out of `<chart>/charts/` (extracted directory or
   `<name>-<version>.tgz`) and take the declared subtree (`config` for FileBrowser).
3. Find the rendered `ConfigMap` carrying the declared data key (`config.yaml`); exactly one must
   exist.
4. Compare the rendered config with the schema, key path by key path:
   - a map is checked key by key, recursively;
   - a list of maps is checked against the union of the schema's list-element maps, so every source
     is checked even when the schema declares one example;
   - where the schema declares a scalar (`userGroups: null`, `scopes: ""`), whatever the render puts
     there is accepted — a type change is not this check.

A missing or unbuilt dependency is reported as a setup error naming `helm dependency build`, not as a
violation: ArgoCD builds it at sync time, and §6 lists the charts that need it locally.

## Output

```
cluster/helm/filebrowser: config.yaml:37: server.cacheDirMaxSizeMB: not declared by filebrowser-quantum 2.0.2-stable
```

Exit 0 clean, 1 violations, 2 a chart could not be checked (dependency not built, config file not
found). Same shape as `check-exposure.sh`.

## Files

| File | Change |
|---|---|
| `hack/config-key-checks.yaml` | new: the manifest, FileBrowser entry |
| `hack/check_config_keys.py` | new: schema extraction, config discovery, path comparison |
| `hack/check-config-keys.sh` | new: driver, `--only`, `--self-test` |
| `hack/tests/test_check_config_keys.py` | new: unit tests for the comparison and the archive reader |
| `AGENTS.md` §6 | one paragraph and one command, next to `check-app-version.sh` |
| `docs/charts/README.md` | one requirement row: a chart whose app validates its config is listed in the manifest |

## Verification

- `hack/check-config-keys.sh` passes on `develop`.
- Unit tests: unknown key is reported with its line; a nested unknown key under a list element is
  reported; a scalar-in-schema position accepts a list; a declared key passes.
- End to end: temporarily add `server.cacheDirMaxSizeMB: 100` to the FileBrowser values, run the
  checker, see the failure naming that line, then remove it.
