# tt

A small task tracker: an action framework over SQLite, with a CLI, a TUI, and
an MCP server, in Python. Projects and issues share one per-user database; every
frontend drives the same domain actions through the same availability checks.

## Requirements

- Python ≥ 3.12
- [`uv`](https://docs.astral.sh/uv/) — environment and dependency manager
- [`just`](https://github.com/casey/just) — command runner (all workflows go
  through the `justfile`; run `just --list` to see them)

## Install

Set up the project environment from the lockfile:

```
just sync
```

That builds the `tt` executable inside the project virtualenv, reachable through
the recipes below (`just cli`, `just tui`, `just mcp`).

To put `tt` on your PATH for use from any directory, install it as an editable
uv tool:

```
just install
```

This drops a `tt` shim in `~/.local/bin` that tracks your working copy. It
mutates the global uv tool environment, unlike the other recipes. After removing
or renaming an entry point, refresh the shim with
`uv tool install --editable . --reinstall`.

## Usage

```
just cli project ls              # list live projects
just cli issue show 1            # print an issue and what it offers
just cli issue editStatus 1 --status doing --note started
just tui                         # the terminal UI
```

A refusal (an action the object won't allow) exits `123` — it is the object's
answer, not a usage error.

If you ran `just install`, the same commands work directly: `tt project ls`,
`tt tui`, and so on.

## MCP server

`tt mcp` runs the MCP server over stdio, exposing the same projects, issues, and
actions to an agent. It opens the shared per-user database, so an agent can
drive it while the TUI holds the same file open.

Register it with Claude Code once. With `tt` on your PATH (via `just install`),
user scope makes it available in every project:

```
claude mcp add tt --scope user -- tt mcp
```

Without the global install, register the in-repo entry point instead (run from
this directory):

```
claude mcp add tt -- uv run tt mcp
```

Then `claude mcp get tt` confirms it, and `/mcp` in Claude Code reconnects.

## Develop

`just verify` is the gate every commit must pass — it runs `just lint` (Ruff +
basedpyright) then `just test` (pytest against real in-memory SQLite):

```
just verify
```

`just format` rewrites files with Ruff's formatter; run it before committing (it
is not part of `verify`).

Schema changes are new forward migrations — never edit a committed one. Author
one with `just db-revision "message"` (autogenerates against the default
database, so `just db-upgrade` to head first).
