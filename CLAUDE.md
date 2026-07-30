# tt

Personal task tracker. Go CLI + TUI over SQLite, backed by Ent.

## Commands

Use the `justfile` recipes (`just --list`) rather than rediscovering the
underlying commands.

- `just format` — `gofmt -w .`. Run before every commit.
- `just lint` — checks formatting and runs `go vet`.
- `just test` / `just test-race` — unit tests, in-memory SQLite.
- `just generate` — regenerate ent. Run after editing `ent/schema/`, and commit
  the result.
- `just migrate <name>` — generate a new versioned migration.
- `just verify` — the full gate. A change is not complete until it passes.

If a recipe is missing or broken, report that. Do not silently bypass or
substitute it. Never claim verification succeeded unless the command was
actually run.

Linting is `gofmt` + `go vet` only; `golangci-lint` and `gofumpt` are not
installed. Adding them is a stop-and-ask.

## Surface decisions; decide them together

When a decision surfaces while you're implementing — a design choice, a
tradeoff, a scope cut, a "this turned out harder than expected" — don't quietly
make the call and keep going, even with a clear recommendation, even when the
call seems small. Lay out the options and let me weigh in. I want to make these
calls with you, not find them afterwards in the diff.

This applies with equal force to *discoveries*. If you find a latent bug, a
wrong assumption, or a case the plan didn't handle, raise it before designing a
fix — even when the fix seems obvious and even when it's "just correctness."
Finding the problem is itself the fork.

Not a request to check in on every trivial detail: obvious mechanical choices
with one sensible answer don't need a checkpoint. It's about genuine forks,
where a reasonable person might pick differently. When in doubt, surface it.

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

Dependency flow: `cli`/`ui` → feature service → repository → Ent → SQLite.

These are load-bearing. Breaking one is a design change, not a refactor.

1. **`cli` and `ui` never import `ent`.** Ent entities and query builders stop
   at the repository layer; frontends see only feature view types. This also
   keeps lazily-loaded edges out of render code, where `Edges.Labels == nil`
   can't be distinguished from "has no labels".
2. **Taxonomy never depends on work.** `project`, `milestone`, and `label` must
   not import `issue`.
3. **`issue` may depend on `ref`; `ref` must not depend on `issue`.**
4. **Cross-feature reads of substance live in `query/`.** A repository may
   reach outside its own tables, but only for narrow existence and status
   checks.
5. **Write methods come in pairs.** The plain one is frontend-facing, has an
   ent-free signature, and owns the transaction via `db.WithTx`; the `…In` one
   takes an `*ent.Client` and composes into a caller's transaction. The plain
   one wraps the `…In` one so the two cannot drift.

Any rule a user can hit must be reachable identically from the CLI and the TUI.
Logic in `cli/` or `ui/` is in the wrong place.

## Contracts

`--json` keys and exit codes are the agent-facing API, not implementation
details. Changing one is a stop-and-ask.

- `--json` output is covered by golden files, so a key change shows up as a
  diff rather than a surprise.
- Exit codes: 0 success, 1 error, 2 usage, 3 not found, 4 nothing eligible.
- **A flag either works or returns an error.** Never parse a flag and silently
  ignore it.

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

Feature services are the layer worth testing, against a real migrated
in-memory SQLite client from `internal/dbtest`. No mocks, and no interfaces
invented for test seams — add an interface when a second implementation
appears, not before.

- Table-driven, one case per invariant. Deterministic and order-independent.
- No sleeps for synchronization.
- Pure functions (`columns()`, `fits()`, selection resolution) are tested
  directly.
- **Never delete, `t.Skip`, or loosen an assertion to get a change passing.**
  If a test looks wrong, that's a stop-and-ask.

## Stop and ask

- Adding a dependency.
- Touching `ent/schema/`, or generating a migration.
- Changing a `--json` key, an exit code, or a flag name.
- Altering a committed migration — never do this; schema changes are a new
  forward migration.
- Expanding the task beyond its stated scope.
