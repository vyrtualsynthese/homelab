# Seeding the board

Date: 2026-11-15

**Changes the cluster:** no. This writes to the task board — projects, views, columns and one saved
filter — and touches no Kubernetes object. The script lives in `hack/vikunja-seed.sh`; this file is
how to run it, what its output means, and what it deliberately does not do.

The board (Vikunja) holds one project per GitHub repository, each with a kanban view titled `Board`
whose columns are `Ready`, `Doing`, `Waiting for validation` and `Done`. `Done` is the view's done
bucket: a card dragged there is marked done, and a card marked done is moved there. That is what
keeps the board and every filter honest, and it is the one setting `--verify` exists to check.

The script is idempotent. It matches every object by title, creates only what is missing, and prints
`existing` for the rest, so running it twice creates nothing the second time. It never deletes or
renames anything.

Run it after [`vikunja-first-start.md`](vikunja-first-start.md), which creates the owner account the
token below belongs to.

## Names

| Thing | Value |
|---|---|
| Address | `https://vikunja.kub.local` |
| Script | `hack/vikunja-seed.sh` |
| Projects | `homelab`, `config`, `scripts`, `ashuvidz-website`, `ashudev-website`, `selfhosted-helmcharts` |
| View | `Board`, kanban, manual bucket order |
| Columns | `Ready`, `Doing`, `Waiting for validation`, `Done` |
| Saved filter | `Awaiting my review` |

## Preconditions

- The first start is done: the owner account exists, and `https://vikunja.kub.local` answers.
- `curl` and `jq` are installed on the workstation.
- The workstation resolves `vikunja.kub.local` to the cluster. Everything here runs from the LAN;
  the board's route carries the `lan-only` gate, so a request from outside the house is refused
  before it reaches the API.
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
check the chain against the cluster's own authority instead. The chain itself is proved once, in
[`vikunja-first-start.md`](vikunja-first-start.md) Step 5 — this procedure is about the board's
contents, not about who signed its certificate.

## Step 2 — the dry run

Always first. It reads the board and prints every call it would make, sending no write.

```bash
./hack/vikunja-seed.sh --dry-run
```

```text
Board https://vikunja.kub.local, 6 repositories: homelab config scripts ashuvidz-website ashudev-website selfhosted-helmcharts
Dry run: reading the board, sending no write.

         -> GET /api/v1/projects
         -> PUT /api/v1/projects {"title":"homelab"}
project  homelab                                      would-create
         -> PUT /api/v1/projects/<new project homelab>/views {"title":"Board","view_kind":"kanban","bucket_configuration_mode":"manual"}
view     homelab / Board                              would-create
         -> PUT /api/v1/projects/<new project homelab>/views/<new view Board>/buckets {"title":"Ready"}
bucket   homelab / Board / Ready                      would-create
...
         -> PUT /api/v1/filters {"title":"Awaiting my review","filters":{"sort_by":["created"],"order_by":["asc"],"filter":"done = false"}}
filter   Awaiting my review                           would-create

dry run: 43 write(s) planned, none sent; 0 object(s) already exist
```

The shape above is what the script printed against a stand-in for the API before the board existed,
so the ids and the URL differ on the real run; the lines, the order and the `43` are what to expect
from an empty board. A placeholder in angle brackets (`<new project homelab>`) is an id the script
does not know yet because it has not written anything.

`bucket_configuration_mode: "manual"` is in the view body on purpose: its default, `none`, fixes each
card to the column it was created in, and `manual` is what allows a person to drag a card from
`Doing` to `Waiting for validation`.

On a board where the view does not exist yet, the dry run cannot know which default columns that view
will bring, so it plans all four. The real run reads them back and matches by title — see Step 3.

## Step 3 — the real run

```bash
./hack/vikunja-seed.sh
```

Expected: six blocks, one per repository — the project, the view, the four columns, the done bucket,
and the bucket mode — then the saved filter.

```text
project  homelab                                      created       id=1001
view     homelab / Board                              created       id=1002
bucket   homelab / Board / Ready                      created       id=1006
bucket   homelab / Board / Doing                      existing      id=1004
bucket   homelab / Board / Waiting for validation     created       id=1007
bucket   homelab / Board / Done                       existing      id=1005
bucket   homelab / Board / To-Do                      kept          id=1003
done     homelab / Board                              created       bucket 1005 (Done)
mode     homelab / Board                              existing      manual
...
filter   Awaiting my review                           created       id=1037

seeded: 31 created, 18 already present, 0 updated, 6 kept (not created here)
```

The block above is from a stand-in for the API whose new kanban views arrive with `To-Do`, `Doing`
and `Done`. That is the case worth reading carefully: the two columns the script manages are matched
by title and reported `existing`, `To-Do` is reported `kept` and left alone, and only `Ready` and
`Waiting for validation` are created. A second `Done` would break the done bucket, which is why the
view's columns are read back on the same run that creates it.

A board whose new views arrive empty instead shows all four columns `created`, and the totals are
`43 created, 0 already present, 0 updated, 0 kept` — 6 × 7 plus the filter. Whatever the totals, a
`kept` line is a column the script did not create and will not touch; nothing is deleted, because an
empty column and a column holding work look the same from the API.

Run it a second time and every line says `existing`: `seeded: 0 created, 49 already present, 0
updated, 6 kept (not created here)` on the board above, or `seeded: 0 created, 43 already present, 0
updated, 0 kept (not created here)` on an empty-view board. That second run is the proof of
idempotency, not a repeat of the first.

`updated` is the fourth counter and it should be `0` on every run after the first: it counts a view
whose bucket mode was repaired. A view made before this script set the mode, or made by hand, carries
`none` and prints `mode … updated none -> manual`; that one write is the repair.

## Step 4 — verify

`--verify` reads every board back and checks its shape. It never writes, and it exits 1 if anything
is wrong.

```bash
./hack/vikunja-seed.sh --verify
```

```text
Verifying 6 boards against https://vikunja.kub.local

PASS homelab                view 1002, 5 buckets, done bucket 1005, mode manual
PASS config                 view 1009, 5 buckets, done bucket 1012, mode manual
PASS scripts                view 1016, 5 buckets, done bucket 1019, mode manual
PASS ashuvidz-website       view 1023, 5 buckets, done bucket 1026, mode manual
PASS ashudev-website        view 1030, 5 buckets, done bucket 1033, mode manual
PASS selfhosted-helmcharts  view 1037, 5 buckets, done bucket 1040, mode manual
PASS saved filter "Awaiting my review"

verify: all 6 boards hold the shape the protocol expects
```

Each `PASS` says the view is kanban, the four columns exist, `done_bucket_id` names the column titled
`Done`, and the bucket mode is `manual`. The bucket count is what the API returned, so `5` means one
column was kept. The done bucket is the check that matters most: without it a card sits in `Done`
while the board still reports it as open, and every filter lists it as work outstanding. A `FAIL`
names the repository and the problem, in the same words as the troubleshooting table below.

The mode check depends on the API returning the field. If a `PASS` line has no `mode manual` next to
it and a `NOTE` follows it, the instance did not return `bucket_configuration_mode`, so the mode could
not be checked — the line would otherwise read as "the cards can be dragged" when nothing proved it.
The verification still succeeds, and the summary says how many boards went unchecked. In that case a
re-run cannot detect a view stuck on `none` either; moving a card by hand is the only test left.

## Step 5 — a seventh repository

Add one line to `REPOS` at the top of `hack/vikunja-seed.sh`:

```bash
REPOS=(
  homelab
  config
  scripts
  ashuvidz-website
  ashudev-website
  selfhosted-helmcharts
  the-new-one
)
```

Commit it, run `--dry-run`, then the real run. Every line for the six existing repositories says
`existing` and only the new project is created — the matching is per object, not per run.

## What the saved filter covers, and what it does not

`Awaiting my review` selects every open task in every project, oldest first: `done = false`, sorted
by creation date, ascending. It is not restricted by column and not restricted by assignee, despite
its title.

Column membership cannot be expressed in a saved filter. So the operator's actual queue is the
`Waiting for validation` column of each board — that is where a task lands when an agent says it is
ready — and the filter is the backstop that answers "did anything fall out of a board and get
forgotten?". A task that appears in the filter and in no column's first screen has been dropped
somewhere; a task in `Waiting for validation` is work to look at now.

## Column order is not enforced

The script creates the four columns in the order `Ready`, `Doing`, `Waiting for validation`, `Done`,
but it does not set their position. A new kanban view can therefore come up with its own default
columns first, leaving `Ready` and `Waiting for validation` to the right of them. Nothing is wrong
with the board when that happens: drag the columns into place once, in the browser, and the order is
saved with the view. The script will not move a column on a later run, and `--verify` does not check
order.

## Titles are the key

Every object is found again by its title. Renaming something in the browser — the project, the
`Board` view, a column, the filter — makes the next run create a second object with the original
title, and the board then holds both. Rename deliberately, and if a rename is intended, change the
name in the script in the same move.

The one rename with teeth is a column: a renamed `Done` means the view's done bucket points at a
column that no longer exists, which `--verify` reports as `done_bucket_id is <id>, expected <id>`.

## Troubleshooting

Every failure prints the method, the path, the HTTP status and the API's response body, and exits 1.
Read the body before the status: Vikunja says which field it objected to.

| Status | Likely cause | What to do |
|---|---|---|
| 401 | The token is wrong, expired, or was not exported into this shell | Make a new token, `export` it, retry. Check `echo ${VIKUNJA_API_TOKEN:+set}` before blaming the board. |
| 403 | The token is valid but lacks the permission for that call | Recreate it with write access; if it still fails, the owner account is not the one that made the token. |
| 404 | Wrong address, or the API group is not the one the script expects | `curl -sk "$VIKUNJA_URL/api/v1/info"` — if that is 404 the instance answers under another prefix, and `API_PATH` is what to change. |
| Any non-2xx on the very first call | TLS or DNS, not the board | The script prints that curl could not complete; check the name resolves and the certificate story in Step 1. |

`done_bucket_id is 0, expected <id>`: the view exists and its `Done` column exists, but the view was
never told that the column means done. Running the script again sets it. If it comes back after
that, the column titled `Done` was renamed or duplicated, so match the title.

`bucket_configuration_mode is "none", expected "manual"`: the view exists but its cards are pinned to
the column they were created in. Running the script again repairs it and prints that line as
`updated`; a dry run first shows `would-update none -> manual`. If the API does not return the field
at all, `--verify` prints `NOTE … the mode was not checked` instead and the summary counts the
boards it could not check — then the only proof is dragging a card by hand.

A `kept` line naming a column you did not expect is not a fault: it is a column the view already had.
Rename or delete it in the browser if it is noise, and the next run will report it gone.

## Rollback

The script never deletes anything, so there is nothing to undo in the other direction: delete what
you no longer want in the browser — a project, a view, a column, the filter — or drop the board's
database if the whole board is being abandoned
([`vikunja-database.md`](vikunja-database.md)). Both are hand operations; neither is scripted here,
because deleting a project takes its tasks with it.
