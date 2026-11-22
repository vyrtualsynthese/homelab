# The operator↔agent protocol

The board rules are written once, in the contract every session reads: §Task board of
`~/.config/opencode/AGENTS.md`. The long form — a card's shape, a handover, a refusal, a blocked
task — is [The board and worktrees](https://docs.kub.local/config/develop/agent/board-and-worktrees/)
in the `config` repository, and the API calls that work against the running instance are the
`vikunja-board` skill of the workstation's OpenCode config.

This page used to restate all of it, a fourth copy to keep in step. It now points at the one copy,
and a rule that disagrees with that contract is a defect here.

The other half of the same protocol — how an agent reaches an app instead of handing the operator a
click — is [Reaching the apps without a click](agent-access.md).
