# The operator↔agent protocol

> **In one line.** The board is run by one contract, written once in the workstation's own
> `AGENTS.md`; its long form and the API calls behind it live outside this repository, and this page
> is the pointer that keeps them findable from the docs site.
>
> **Use it when** you are filing, moving or refusing a card and want the page that governs it ·
> **Verified** 2026-11-22 · **Kind:** explanation
>
> **Sources** the task-board contract in `~/.config/opencode/AGENTS.md`, `docs/agent-access.md`

The board rules are written once, in the contract every session reads: §Task board of
`~/.config/opencode/AGENTS.md`. The long form — a card's shape, a handover, a refusal, a blocked
task — is [The board and worktrees](https://docs.kub.internal/config/develop/agent/board-and-worktrees/)
in the `config` repository, and the API calls that work against the running instance are the
`vikunja-board` skill of the workstation's OpenCode config.

This page used to restate all of it, a fourth copy to keep in step. It now points at the one copy,
and a rule that disagrees with that contract is a defect here.

The other half of the same protocol — how an agent reaches an app instead of handing the operator a
click — is [Reaching the apps without a click](agent-access.md).
