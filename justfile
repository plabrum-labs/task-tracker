default:
    @just --list

# Install the environment from the lockfile.
sync:
    uv sync

# Put `tt` on your PATH (~/.local/bin) as an editable uv tool, so the working
# copy stays live. Mutates the global tool env, unlike the rest here.
install:
    uv tool install --editable .

# Link the in-repo `tt` agent skill into ~/.claude/skills so Claude Code can use
# it from any project, tracking your working copy. Mutates your global Claude
# config, the way `install` mutates the global uv tool env.
install-skill:
    mkdir -p ~/.claude/skills
    ln -sfn "{{ justfile_directory() }}/.claude/skills/tt" ~/.claude/skills/tt

# The domain, the wire, the frontends, and the SQL edge against real SQLite.
test:
    uv run pytest

# A refusal exits 123 — it is the object's answer, not a usage error.
#
#     just cli project ls
#     just cli issue show 1
#     just cli issue edit 1 --title ship --body "" --status doing --priority high
#     just cli issue action 1 edit '{"title":"ship","body":"","status":"doing","priority":"high"}'

# The command line, over the same groups. Takes a subcommand.
cli *args:
    uv run tt {{ args }}

# The terminal UI. Up/down to move, enter to pick, esc to go back, q to quit.
# Bare `tt` with no subcommand is the TUI.
tui:
    uv run tt

# Author a migration from a change to the models (autogenerate against the
# default database, so upgrade it to head first).
db-revision message:
    uv run alembic revision --autogenerate -m "{{ message }}"

# Bring the default database up to the latest schema.
db-upgrade:
    uv run alembic upgrade head

# Ruff's formatter over the tree. Rewrites files, so it is not part of verify.
format:
    uv run ruff format

# Ruff's linter and basedpyright.
lint:
    uv run ruff check
    uv run basedpyright

# Lints, and tests. Mirrors the root justfile's verify.
verify: lint test

# Hand the database off to/from the mirrors in ~/.config/tt/config.toml over the
# tailnet, one implementation in `tt sync` so the host lives in config, not here.
# A handoff for a tracker used one machine at a time: `tt sync` guards against a
# live writer and refuses to overwrite newer work. See `tt sync status`, and
# `tt sync install` to run `pull` on a schedule.
#
#     just push          # send local -> the mirrors
#     just pull          # fetch the newest mirror -> local
#     just push force    # override the newer-on-a-mirror guard
push force="":
    uv run tt sync push {{ if force != "" { "--force" } else { "" } }}

pull:
    uv run tt sync pull
