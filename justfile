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

# The tailnet host tt.db syncs with, same ~/.local/share/tt path on both ends.
tt_sync_host := "walter"

# Hand the database off to/from {{tt_sync_host}} over the tailnet. This is a
# handoff for a tracker used one machine at a time, not multi-writer
# replication: close tt on the source first so its files are quiescent.
#
# Both directions mirror the whole SQLite fileset — tt.db and its -wal/-shm
# sidecars — with `rsync --delete`. In WAL mode committed data lives in the -wal
# until a checkpoint, so copying the db alone would ship stale rows, and leaving
# a stale -wal on the destination would let it replay over fresh data; mirroring
# the exact set the source has avoids both. They refuse to overwrite a newer
# database on the far end unless you pass `force`.
#
#     just push          # send local -> {{tt_sync_host}}
#     just pull          # fetch {{tt_sync_host}} -> local
#     just push force    # override the newer-than-destination guard
push force="":
    @just _sync push "{{ force }}"

pull force="":
    @just _sync pull "{{ force }}"

[private]
_sync direction force="":
    #!/usr/bin/env bash
    set -euo pipefail
    direction="{{ direction }}"
    host="{{ tt_sync_host }}"
    local_dir="${XDG_DATA_HOME:-$HOME/.local/share}/tt"
    remote_dir=".local/share/tt"
    fileset=(--include=tt.db --include=tt.db-wal --include=tt.db-shm --exclude='*')

    # Recency is the newest mtime across the db and its WAL: a WAL-mode write
    # lands in the -wal and leaves tt.db untouched, so the db alone understates
    # it. Both stat spellings (BSD/macOS -f, GNU -c) are tried for portability.
    newest() {
      local m=0 t f
      for f in "$@"; do
        t=$(stat -f %m "$f" 2>/dev/null || stat -c %Y "$f" 2>/dev/null || echo 0)
        if [ "$t" -gt "$m" ]; then m="$t"; fi
      done
      echo "$m"
    }
    local_mtime=$(newest "$local_dir/tt.db" "$local_dir/tt.db-wal")
    remote_mtime=$(ssh "$host" "for f in $remote_dir/tt.db $remote_dir/tt.db-wal; do stat -f %m \"\$f\" 2>/dev/null || stat -c %Y \"\$f\" 2>/dev/null || true; done" | sort -n | tail -1)
    remote_mtime=${remote_mtime:-0}

    case "$direction" in
      push)
        if [ -z "{{ force }}" ] && [ "$remote_mtime" -gt "$local_mtime" ]; then
          echo "refusing: $host has a newer tt.db. Pull first, or 'just push force'." >&2
          exit 1
        fi
        ssh "$host" "mkdir -p $remote_dir"
        rsync -a --delete "${fileset[@]}" "$local_dir/" "$host:$remote_dir/"
        echo "pushed local -> $host"
        ;;
      pull)
        if [ -z "{{ force }}" ] && [ "$local_mtime" -gt "$remote_mtime" ]; then
          echo "refusing: local has a newer tt.db. Push first, or 'just pull force'." >&2
          exit 1
        fi
        mkdir -p "$local_dir"
        rsync -a --delete "${fileset[@]}" "$host:$remote_dir/" "$local_dir/"
        echo "pulled $host -> local"
        ;;
    esac
