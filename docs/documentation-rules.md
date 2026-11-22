# The documentation rules

> **In one line.** A page here is one of four kinds, each kind names who may edit it, every fact has
> exactly one home, and the parts a script can check are checked on every pull request.
>
> **Use it when** you are writing a page, moving one, or wondering why a check refuses your change ·
> **Verified** 2026-11-22 · **Kind:** explanation
>
> **Sources** `mkdocs.yml`, `hack/check-docs.sh`, `hack/check_docs.py`, `hack/gen_docs.py`,
> [`superpowers/specs/2026-11-22-documentation-rules-design.md`](superpowers/specs/2026-11-22-documentation-rules-design.md)

There is one reader: the operator. This is a working manual for one homelab, not a course, and every
rule below exists because a page drifted or a fact ended up in two places. A second audience gets a
second site, not a second voice in this one.

## The four kinds

A page declares its kind in its opening blockquote, and the kind decides who may touch it. That is
the part which stops an agent "improving" a snapshot.

| Kind | Answers | Written by | Edit rule |
|---|---|---|---|
| Explanation | why the place is shaped this way | an agent, for the operator | rewritten when the shape changes, re-dated |
| How-to | what to do, in order | an agent, then the operator after it has been run | the body follows reality; a change to reality changes it in the same pull request |
| Reference | a table of current fact | a script, from the repository, where it can be; by hand where the set is not derivable (the application catalogue, under R16) | never hand-edited once generated; regenerate and commit |
| Record | what was true, or what was decided, on a date | the session that did it | never edited; a correction is a new record |

The old labels map onto these, and a page adopts its true kind the next time it is edited: `living
page` becomes an explanation or a how-to depending on its tab, `living procedure` and `living
runbook` become how-tos, `snapshot` and `completed record` become records.

## Which page owns which question

A question has one home. Anything else states it by link.

| The question | The page | Kind |
|---|---|---|
| Why is the cluster shaped this way? | [the-shape-and-why.md](how-it-works/the-shape-and-why.md) | explanation |
| Which machine runs this application? | [applications.md](how-it-works/applications.md) | reference |
| Is this hostname reachable from outside, and what gates it? | [routes.md](reference/routes.md) | reference, generated |
| Who signs in to what, and how? | [sso-applications.md](reference/sso-applications.md) | reference |
| How does a request reach an application? | [network.md](how-it-works/network.md) | explanation |
| Where does a secret come from? | [secrets.md](secrets.md) | reference |
| What is backed up, and when? | [backups.md](how-it-works/backups.md) | explanation |
| What is broken right now? | [every-day.md](running-it/every-day.md) | how-to |
| Why was this decided? | [the decision records](decisions/README.md) | record |

[`superpowers/`](superpowers/specs/2026-11-22-documentation-rules-design.md) sits outside the nav on
purpose: it is the design record of one change, read from the card that links it, never a manual.

## The rules

**Check** means a script refuses the pull request. **Review** means a person or an agent verifies it,
and the pull request says what the artifact is.

| # | Rule | Enforced by |
|---|---|---|
| R1 | One fact, one home. A second page states it by link, never by copy. | Review |
| R2 | Every page declares one of four kinds, and the kind fixes who may edit it. | Check: banner |
| R3 | The opening blockquote carries In one line / Use it when / Verified / Kind / Sources. | Check: banner |
| R4 | The nav is the reading order. A page cited from outside `docs/` keeps its path; it is listed in the nav where the reader needs it. | Check: references, nav |
| R5 | A generated page is never hand-edited. It carries a banner, its sources and its regenerate command. | Check: `gen_docs.py --check` |
| R6 | A change that alters behaviour updates the page that owns it, in the same pull request. | Review |
| R7 | Shape: answer first, then why, then procedure, then links, with one Mermaid diagram where a flow is described. A page whose prose passes about 1,200 words — tables are not prose — is a candidate for splitting, and the review names it. | Review |
| R8 | Plain language, one gloss per domain term, the first time it appears. | Review |
| R9 | No secret value ever, in any page or any example. Keys and locations only. | Check: secrets |
| R10 | A record and a snapshot are dated and never rewritten. A correction is a new record. | Review |
| R11 | A procedure states preconditions, commands, the output that means success, and the rollback; a destructive step says so first. | Review |
| R12 | A runbook exists only after it has been run; its status is `proved <date>`, `pending` or `completed record <date>`. | Review |
| R13 | Links are relative inside `docs/`; bodies use Markdown that renders on GitHub as well as MkDocs. | Check: links |
| R14 | A new page and its nav entry land in the same commit. A page the nav does not list is a defect. | Check: nav |
| R15 | A page older than the last change to its Sources is stale: the hardening pass files a card and does not edit the page. | Check: staleness report |
| R16 | A hand-written catalogue names exactly the set its source declares. A missing row and a row for something that no longer exists are both failures. | Check: catalogue |

`hack/check-docs.sh` runs all of them in one go, and `AGENTS.md` §6 is where the command lives.

## A new page

Put it in the tab that matches the question, not in a new folder. Then, in the same commit: add it to
`mkdocs.yml`, and open it with the banner — one blockquote holding **In one line**, **Use it when**
(with the date you verified it and the kind), and **Sources** naming the files the page was written
from. A page whose Sources are all in the repository can be checked for staleness; a page with none
is invisible to that report, which is the failure this rule is for.

**Freshness**: `Sources` is the list of files that, if they changed, would make the page wrong. When
one of them is newer than the page's `Verified` date, the page is stale. A hardening pass runs
`python3 hack/check_docs_staleness.py`, reads what it prints, and files one card per stale page. It
never edits the page in the same pass: a refresh is its own card, its own branch and its own review.

The card says which page, which source moved and on what date, and it carries `priority/low` — a
newer source file is a question, not a verdict, and a change to `AGENTS.md` rarely makes a page
wrong. The exception is a page a person runs under pressure: a restore, a rollback, a rotation. That
one is `priority/high`, because a wrong procedure at 3 a.m. costs more than a wrong explanation.

## What is not a rule

The order of tabs and the order of pages inside a tab is taste. It is not a fact about the cluster,
so it can change in a pull request that changes nothing else. The same holds for a page's title.
