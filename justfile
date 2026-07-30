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

# Regenerate ent. Run after editing ent/schema/, and commit the result.
generate:
    go generate ./ent

# Generate a new versioned migration from the current ent schema.
migrate name:
    go run ./ent/migrate/gen -name {{ name }}

# Full verification. A change is not complete until this passes.
verify: lint build test

# Install the git hooks. Run once per clone.
hooks:
    pre-commit install --hook-type pre-commit --hook-type pre-push
