# Shaping the board

Date: 2026-11-16

**Changes the cluster:** no. This writes to the task board — projects, one view, six columns, seven
labels, two saved filters — and touches no Kubernetes object. Two scripts do it: `hack/vikunja-seed.sh`,
which shapes a board and is safe to run forever, and `hack/vikunja_migrate.py`, which was the one-shot
migration of the 152 tickets that existed on 2026-11-16 and is kept for the record.

The board (Vikunja) holds one project per GitHub repository. Only `homelab` is boarded today; the other
six exist and are empty. Its kanban view is `Board`, and its columns are `Idea`, `Shaping`, `Backlog`,
`Doing`, `Validation` and `Done`. `Done` is the view's done column: a card dragged there is marked done,
and a card marked done is moved there. That is what keeps the board and every filter honest, and it is
the one setting the `--verify` run exists to check.

The seed script is idempotent: it matches every object by title, creates only what is missing, and
prints `existing` for the rest. It never deletes a view and never deletes a column holding cards.
A column from the old shape is **renamed in place**, so its id survives — the saved filters store those
ids, and a new id would silently point the operator's queue at nothing.

Run it after [`vikunja-first-start.md`](vikunja-first-start.md), which creates the owner account the
token below belongs to.

## Names

| Thing | Value |
|---|---|
| Address | `https://vikunja.kub.local` |
| Script | `hack/vikunja-seed.sh` |
| Projects | `homelab` (boarded), then `config`, `ashuvidz-website`, `ashudev-website`, `selfhosted-helmcharts`, `ashuworld`, `ashuvidz-creator` (empty) |
| View | one kanban view per project, titled `Board` at position 1 |
| Columns | `Idea`, `Shaping`, `Backlog`, `Doing`, `Validation`, `Done`, left to right |
| Labels | `feature`, `story`, `task`; `implementer`, `reviewer` — authorship is the account, not a label |
| Saved filters | `Needs my validation`, `Agent work in flight` |

## Preconditions

- The first start is done: the owner account exists, and `https://vikunja.kub.local` answers.
- `curl`, `jq` and `python3` are installed on the workstation.
- The workstation resolves `vikunja.kub.local` to the cluster. Everything here runs from the LAN; the
  board's route carries the `lan-only` gate, so a request from outside the house is refused before it
  reaches the API.
- An operator API token, made in Step 1.

## Step 1 — the token

In the board: **Settings → API Tokens → Create a token**. Name it after the workstation, give it
write access, and copy it once — the board does not show it again.

```bash
export VIKUNJA_URL=https://vikunja.kub.local
export VIKUNJA_API_TOKEN='<the token>'
```

`VIKUNJA_URL` is optional and defaults to that address. The token is read from the environment and
never printed by the script; do not put it on the command line, where the shell records it. One
caveat: `curl` receives the token as an argument, so on this machine another local user can see it in
`ps` while a request is in flight.

One sentence about the certificate, because it applies to every call below: without `-k` curl would
reject the cluster's internal certificate, so the script passes `-k`, which accepts the certificate
**without checking it**; setting `VIKUNJA_CACERT=~/.config/secrets/kub-local/local-ca.pem` makes it
check the chain against the cluster's own authority instead.

## Step 2 — the dry run

Always first. It reads the board and prints every call it would make, sending no write.

```bash
./hack/vikunja-seed.sh --repo homelab --dry-run
```

```text
Board https://vikunja.kub.local, 1 repositories: homelab
Dry run: reading the board, sending no write.

         -> GET /api/v1/labels
         -> PUT /api/v1/labels {"title":"feature","hex_color":"#1f6feb"}
label    feature                                      would-create  #1f6feb
...
project  homelab                                      existing      id=2
view     homelab / Kanban                             adopted       id=13 -> Board
         -> GET /api/v1/projects/2/views/13/buckets
bucket   homelab / Board / Ideas                      would-rename  id=10 -> Idea
...
view     homelab / Board                              would-update  title, position, mode, default column and done column in one write

         -> GET /api/v1/projects
         -> PUT /api/v1/filters {"title":"Needs my validation",...}
filter   Needs my validation                          would-create  bucket_id = 14 && done = false
```

Read it for three things: every project line says `existing`, every column lands `existing` or
`would-rename` rather than a fresh `would-create`, and the closing count is small. A `would-create`
for a column that already exists means a title changed in the script and the board kept the old one.

## Step 3 — the real run

```bash
./hack/vikunja-seed.sh --repo homelab
```

Expected: the labels, the project, the adopted view, six columns, one view write, and two filters.

```text
label    feature                                      existing      id=1
project  homelab                                      existing      description already set
view     homelab / Kanban                             adopted       id=13 -> Board
bucket   homelab / Board / Ideas                      renamed       id=10 -> Idea
bucket   homelab / Board / Shaping                    created       id=97 position=200
bucket   homelab / Board / Backlog                    positioned    200 -> 300
view     homelab / Board                              updated       title, position, mode, default column and done column in one write
filter   Needs my validation                          updated       bucket_id = 14 && done = false
filter   Awaiting my review                           retired       id=2

seeded: 0 created, 17 already present, 3 updated, 0 kept (not created here)
```

With no argument the script shapes `homelab` and nothing else, because that is the only board the
operator decided to change. `--repo <name>` shapes one other board; `--all` shapes every board in the
script's list, which is a deliberate act, not a default. The two flags cannot be combined.

The two saved filters are account-wide but point at one board's columns, so their ids are taken from
`homelab` only — in a run that touches several boards, every other board would otherwise overwrite
them with its own ids and silently empty the operator's queue.

The view write carries **five fields**: title, position, column mode, default column and done column.
The endpoint replaces the record rather than patching it, so a body missing `view_kind` turns the board
into a list view with columns nobody can drag. That happened once, on 2026-11-16; the script now sends
the kind too, and `--verify` checks it.

## Step 4 — verify

```bash
./hack/vikunja-seed.sh --repo homelab --verify
```

Expected, eight lines, one per check:

```text
PASS labels       5 labels present
PASS homelab      view "Board" at position 1
PASS homelab      view kind kanban
PASS homelab      columns Idea, Shaping, Backlog, Doing, Validation, Done
PASS homelab      done column 12
PASS homelab      default column 13
PASS homelab      columns are dragged by hand (mode manual)
PASS homelab      206 tickets, each with exactly one kind label
PASS homelab      Doing and Validation hold task cards only
PASS filters      saved filter "Needs my validation" reads bucket_id = 14 && done = false
PASS filters      saved filter "Agent work in flight" reads bucket_id = 11 && done = false
```

Exit code 1 with the failing check named is the whole point of the run. Read the failure as the
instruction it is: `columns` means a rename did not happen, `done column` means a view write dropped a
field, `kind labels` means a migration or a hand-made card skipped a label.

## The migration, for the record

`hack/vikunja_migrate.py` reads `hack/vikunja-migrate-2026-11.json` — the reviewed mapping of 27
features, 152 tasks and the merged work — and writes it onto the board. It ran once, on 2026-11-16.

```bash
python3 hack/vikunja_migrate.py --dry-run    # print the actions, write nothing
python3 hack/vikunja_migrate.py --apply      # create features, relabel, reparent, place
```

A second run prints `existing: the board already matches the map`. If it prints actions instead, the
board was edited by hand somewhere the map does not know about; decide which side is right before
applying again.

What it does not do: it never decides that a task is finished. The mapping says which tickets are
`Done`; the script applies the map.

## Troubleshooting

| Line | What it means | What to do |
|---|---|---|
| `bucket "Ideas" holds 3 cards` | A column with cards cannot be renamed: the rename would relabel live work | Move the cards first, then re-run |
| `has no kanban view` | The project has no kanban view at all — someone deleted it, or converted it | Add a kanban view in the board UI, then re-run. The script never creates one |
| `FAIL done column` | The view write dropped `done_bucket_id` | Re-run the seed; it repairs the whole record in one write |
| `FAIL view kind` | The view is not a kanban any more | The seed repairs it; if it recurs, something else is writing to `/projects/{p}/views/{v}` |
| `FAIL saved filter ... does not read` | The filter points at a column id that changed | Re-run the seed; it rewrites both filters from the live ids |
| `FAIL ... without exactly one kind label` | A card was made by hand without `feature`/`story`/`task` | Label it, or delete it |
| `HTTP 405 GET /api/v1/filters` | An older script read the filter list directly; this API version has no such endpoint | Filters are listed through `GET /projects`, where they appear as projects with negative ids |
| `NOTE filters not checked` | The run did not include `homelab`, so there were no column ids to compare against | Expected: run `--repo homelab` to check them |
| `cannot read the cards of Doing` | The column reports cards the read could not enumerate | Nothing was verified for that column; investigate before trusting a PASS |

## Rollback

There is none, and none is needed: the script only ever creates what is missing, renames empty columns,
and rewrites the same two filters. To undo a column rename, rename it back with `--repo` and the old
name in `BUCKET_RENAMES`; to drop the board entirely, delete the project in the UI, which destroys its
cards and leaves the labels and filters behind.
