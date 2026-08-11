# tt

A small task tracker: an action framework over SQLite, with a CLI and a TUI, in
Python. Projects and issues share one per-user database; every frontend drives
the same domain actions through the same availability checks. An agent drives it
through the CLI — see the `tt` skill under `.claude/skills/`.

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
the recipes below (`just cli`, `just tui`).

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
just tui                         # the terminal UI (bare `tt`, no subcommand)
```

A refusal (an action the object won't allow) exits `123` — it is the object's
answer, not a usage error.

If you ran `just install`, the same commands work directly: `tt project ls`,
`tt issue show 1`, and so on. Bare `tt` opens the terminal UI.

## Driving it from an agent

An agent drives `tt` through the CLI, not a separate server. The read/act loop is
`ls --json` and `show` (which returns the object plus the actions it offers and
their argument schemas), then `action KEY '<json>'` to write. The `tt` skill under
`.claude/skills/tt/` teaches Claude Code that loop and the tracker's rules; install
it globally with:

```
just install-skill
```

That symlinks the in-repo skill into `~/.claude/skills/` so it is available in
every project, tracking your working copy the way `just install` does for the
binary.

## Sync across devices

`tt` keeps each device's database fresh by mirroring the whole SQLite fileset
(`tt.db` and its `-wal`/`-shm` sidecars) to and from ssh hosts over the tailnet.
This is a handoff for a tracker used **one machine at a time**, not multi-writer
replication: the cron path is pull-only and refuses to overwrite newer local
work, and a pull skips the cycle if another `tt`/TUI is mid-write.

Configure the mirrors and cadence in `~/.config/tt/config.toml` (hand-edited —
`tt` reads this table, it never writes it):

```toml
[sync]
interval = "15m"          # cadence; "30s"/"15m"/"1h" or bare seconds

[[sync.mirror]]
host = "walter"           # ssh host (tailnet)
path = ".local/share/tt"  # remote data dir; this is the default
```

Then:

```
tt sync status      # mirrors, cadence, schedule state, freshness vs local
tt sync run         # one pull cycle — what the scheduler calls
tt sync pull        # pull the newest mirror in, if one is ahead
tt sync push        # mirror local out to every mirror (--force overrides the guard)
tt sync install     # install the schedule (launchd on macOS, cron elsewhere)
tt sync uninstall   # remove it
```

`tt sync install` schedules `tt sync run` at the configured cadence, so each
device pulls itself fresh unattended. `just push` / `just pull` delegate to
`tt sync push` / `tt sync pull` for muscle memory.

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
