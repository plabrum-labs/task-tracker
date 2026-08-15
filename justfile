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

# The domain, the wire, the frontends, and the SQL edge against real Postgres.
# Needs the local database up (`just db-up`).
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

# Bring the local development/test Postgres up (the compose.yaml container on an
# esoteric port) and wait for it to accept connections.
db-up:
    docker compose up -d --wait

# Stop the local Postgres. Its named volume survives, so the data is kept.
db-down:
    docker compose down

# Stand up (or update) the real Postgres on the always-on host over SSH. Reads the
# password and bind address from `.env.prod` (gitignored; see `.env.prod.example`),
# ships them beside `compose.prod.yaml`, and brings the container up. The named
# volume there outlives the container, so a re-run never loses data.
db-prod-up host="walter":
    ssh {{ host }} 'mkdir -p ~/tt-postgres'
    scp compose.prod.yaml {{ host }}:~/tt-postgres/compose.yaml
    scp .env.prod {{ host }}:~/tt-postgres/.env
    ssh {{ host }} 'cd ~/tt-postgres && docker compose up -d --wait'

# Author a migration from a change to the models (autogenerate against the
# local database, so upgrade it to head first).
db-revision message:
    uv run alembic revision --autogenerate -m "{{ message }}"

# Bring the local database up to the latest schema.
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
