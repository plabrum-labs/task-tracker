# tt

Personal task tracker. Go CLI + TUI over SQLite, backed by Ent.

## Commands

Use the `justfile` recipes (`just --list`) rather than rediscovering the
underlying commands.

If a recipe is missing or broken, report that. Do not silently bypass or
substitute it. Never claim verification succeeded unless the command was
actually run.

Enabling or disabling a linter is a stop-and-ask, and every suppression in
`.golangci.yml` says why.

Every tool the build needs is pinned in `go.mod`. The `db-*` recipes are the
exception: they shell out to the Atlas CLI, which has to be installed
separately.

    brew install ariga/tap/atlas      # or: curl -sSf https://atlasgo.sh | sh

It cannot be pinned as a `tool` directive — `ariga.io/atlas/cmd/atlas` stopped
publishing to the module proxy at v0.13.1, years behind the library this repo
builds against. `verify` therefore does not depend on it: migrations are
generated and applied as their own step, and only the `db-*` recipes need the
binary.

## Hooks

`pre-commit` runs the same justfile recipes, so a hook can never check
something different from what you get at the terminal.

- pre-commit: `just format`, `just lint`, `just build`.
- pre-push: `just verify`, which adds the tests.

Never commit with `--no-verify`. If a hook is in the way, that is a
stop-and-ask.

`pre-commit` stashes unstaged changes before running. A change to `go.mod` that
is not yet staged is therefore invisible to the hooks, and a tool the commit
itself introduces will fail to resolve — stage `go.mod` and `go.sum` in the
same commit as whatever needs them.


## Commits

Standing authorization: don't leave completed work uncommitted. Once a logical
unit is done and the tree is green, commit it.

- Commit as you go, not all at once at the end.
- **Every commit must build and pass `just verify`.** No WIP commits.
- **Messages explain _why_, not _what_.** The diff shows what changed. No
  `feat:`/`fix:`/`chore:` prefixes — match the existing plain imperative style.
- **Separate preparatory refactors from behavior changes**, refactor first.
  This applies even when the need for the refactor only becomes apparent while
  writing the behavior change.

## Comments

Comments explain *why this code is shaped the way it is*. They are not a record
of development history — what was tried first, what didn't work, what's
"cleaner" than some alternative. The rejected alternative is nowhere in the
file, so the comparison means nothing to a later reader.

Avoid:

- "cleaner than the previous approach"
- "we used to … but …"
- "after trying X, we found Y"
- product or design justification — that belongs in the tracker, as an issue
- notes scoped to work in flight — "not wired yet", "this guard is deleted when
  X lands". They are false as soon as the next change lands.

Exported identifiers get the one-line godoc convention requires. Do not expand
it into rationale.

A comment marking a genuine landmine — a constraint where the obvious tidy-up
breaks the build — is exactly what this section is for. Say what breaks.

## Architecture

Dependency flow: `cli`/`tui` → `backend` → domain package → Ent → SQLite.

## Bugs

Don't offer "accept it / document it and move on" alongside real fixes. A known
race, correctness violation, or data-corruption bug needs a fix, not a
tradeoff. If a real fix is genuinely out of reach, say so plainly rather than
dressing "no fix" up as an option.

A bug fix includes a regression test that fails before the fix.

## Prefer the cleaner design over the smaller diff

When a task could be done either by tacking onto existing code or by first
restructuring it, choose the restructuring. "Minimal change" is not a goal; a
readable final state is — that's what the prep-refactor-first rule above is for.

This is not license for speculative abstraction. Don't invent structure for
imagined future needs. But if the *current* change would be clearer after
extracting a method or splitting a function, that refactor is part of the task.

## Testing

Domain services are the layer worth testing, against a real migrated in-memory
SQLite client from `backend/internal/dbtest`. No mocks, and no interfaces
invented for test seams — add an interface when a second implementation
appears, not before.

- Table-driven, one case per invariant. Deterministic and order-independent.
- No sleeps for synchronization.
- Pure functions (`Actions()`, `columns()`, `fits()`, selection resolution) are
  tested directly. `Actions()` needs no database — a table of literal contract
  values is the whole test.
- Every action key gets two cases: the menu withholds it, and the write refuses
  it against live rows.
- **Never delete, `t.Skip`, or loosen an assertion to get a change passing.**
  If a test looks wrong, that's a stop-and-ask.

## Stop and ask

- Adding a dependency.
- Touching `backend/internal/ent/schema/`, or generating a migration.
- Changing a `--json` key, an exit code, or a flag name.
- Altering a committed migration — never do this; schema changes are a new
  forward migration.
- Silencing a linter, whether by a `//nolint` directive or by disabling a check
  in `.golangci.yml`. Fix the finding instead; if it is a false positive, say
  so and let me decide. A `//nolint` without a reason fails the lint run
  anyway.
- Bypassing a git hook with `--no-verify`.
- Expanding the task beyond its stated scope.
