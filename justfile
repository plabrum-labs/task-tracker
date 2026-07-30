# Path to the SQLite store the db-* recipes operate on. Absolute, because the
# db-* recipes run from backend/ and a relative TT_DB would resolve there.
db := absolute_path(env("TT_DB", "tt.db"))

default:
    @just --list

# Format all Go source.
format:
    go tool golangci-lint fmt ./...

# Formatting, go vet and the enabled linters. See .golangci.yml.
lint:
    go tool golangci-lint run ./...

# Build all packages.
build:
    go build ./...

# Unit tests.
test:
    go test ./...

# Unit tests under the race detector.
test-race:
    go test -race ./...

# Regenerate ent. Run after editing backend/internal/ent/schema/, and commit the result.
generate:
    go generate ./backend/internal/ent

# The db-* recipes run from backend/, where atlas.hcl lives. Atlas's ent loader
# writes a temp package into .entc/ in the working directory, and from the
# repository root that package cannot import backend/internal/ent/schema.
# migrations/ stays at the root; atlas.hcl reaches it as ../migrations.

# Generate a new versioned migration from the current ent schema.
db-migrate name:
    cd backend && atlas migrate diff --env local {{ name }}

# Apply every pending migration to the store.
db-upgrade:
    cd backend && atlas migrate apply --env local --url "sqlite://{{ db }}"

# Which migrations the store has applied, and what is pending.
db-status:
    cd backend && atlas migrate status --env local --url "sqlite://{{ db }}"

# Tests build their schema with Schema.Create rather than by replaying
# migrations/, so a forgotten db-migrate breaks nothing until someone opens a
# real database. This is what catches it.
#
# `migrate diff` exits 0 whether or not it found a difference — the difference
# is a file it wrote — so the file is the signal. Writing one also rewrites
# atlas.sum, hence the hash on the way out.

# Fail if the ent schema has drifted from migrations/.
db-check:
    #!/usr/bin/env bash
    set -euo pipefail
    cd backend
    atlas migrate diff --env local drift_check
    shopt -s nullglob
    drift=(../migrations/*_drift_check.sql)
    if (( ${#drift[@]} )); then
        rm -f "${drift[@]}"
        atlas migrate hash --env local
        echo "the ent schema has drifted from migrations/ — run 'just db-migrate <name>'" >&2
        exit 1
    fi

# Full verification. A change is not complete until this passes.
verify: lint build test

# Install the git hooks. Run once per clone.
hooks:
    pre-commit install --hook-type pre-commit --hook-type pre-push
