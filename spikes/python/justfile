default:
    @just --list

# Install the environment from the lockfile.
sync:
    uv sync

# The domain, the wire, the frontends, and the SQL edge against real SQLite.
test:
    uv run pytest

# The erased path from outside: the offers, their schemas, then one dispatch.
show:
    uv run python -m tt.frontend.show

# A refusal exits 123 — it is the object's answer, not a usage error.
#
#     just cli project ls
#     just cli issue show 1
#     just cli issue editStatus 1 --status doing --note started
#     just cli issue action 1 editStatus '{"status":"doing"}'

# The command line, over the same groups. Takes a subcommand.
cli *args:
    uv run tt-cli {{ args }}

# The terminal UI. Up/down to move, enter to pick, esc to go back, q to quit.
tui:
    uv run tt-tui

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
