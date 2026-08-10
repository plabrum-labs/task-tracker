# tt

Personal task tracker. Python CLI + TUI over SQLite, backed by SQLAlchemy.

## Commands

Use the `justfile` recipes (`just --list`) rather than rediscovering the
underlying commands.

If a recipe is missing or broken, report that. Do not silently bypass or
substitute it. Never claim verification succeeded unless the command was
actually run.

Enabling or disabling a linter is a stop-and-ask, and every suppression is
justified in a comment (see the Stop and ask list).

The environment is managed by `uv` from `uv.lock`. Run `just sync` to install
it. Everything the build needs is a dev dependency in `pyproject.toml` and
resolves from the lockfile — Alembic is the only tool that touches a real
on-disk database, and only the `db-*` recipes invoke it.

## Verify

`just verify` is the gate: it runs `just lint` (ruff check + basedpyright) then
`just test` (pytest). Every commit must pass it.

`just format` runs Ruff's formatter, rewrites files, and is therefore not part
of `verify` — run it before committing.

## Commits

Standing authorization: don't leave completed work uncommitted. Once a logical
unit is done and the tree is green, commit it.

- Commit as you go, not all at once at the end.
- **Every commit must pass `just verify`.** No WIP commits.
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

Docstrings state what a module or function is and the constraint that shaped it,
in the voice the existing modules use. Do not expand them into rationale.

A comment marking a genuine landmine — a constraint where the obvious tidy-up
breaks something — is exactly what this section is for. Say what breaks.

## Types

Fully typed Python. Every function parameter, return type, and non-obvious
variable is annotated; no inferred/implicit signatures. Prefer precise types
over `Any` — reach for `typing`/`collections.abc` generics, `Protocol`,
`TypedDict`, and `| None` unions. Code passes `ruff` and `basedpyright`
cleanly. Fix type errors by adding correct annotations, not by suppressing them
(`# type: ignore`, `Any`, casts) unless there is a genuine reason noted in a
comment.

## Architecture

Dependency flow: `frontend` (`cli`/`tui`) / `tt.api` → domain `api` → `platform` → SQLAlchemy → SQLite.

- `tt/domains/{issue,project}/` — each domain is `models` (the mapped
  table), `enums`, `schemas` (the wire shapes), `queries` (the reads that name
  the tables), `actions` (the writes and their contracts), and `api` (the one
  dispatch a frontend calls).
- `tt/platform/` — the shared spine: `db` (engine, read scope, write
  transaction, the base every table maps on), `enums`, and `actions/`
  (`base`, `deps`, `form`, `group`, `registry`) — the action framework the
  domains register into.
- `tt/frontend/` — `cli` (Typer) and `tui` (Textual). No frontend names an
  action key; the subcommands and forms are derived from the registered action
  schemas. An agent drives the tracker through the `cli` (see the `tt` skill).
- `tt/api/` — the public, versioned client library (`TtClient` + frozen
  read shapes in `types`, `enums`, `exceptions`). A second consumer of the
  domain `api` layer, parallel to the frontends: it holds the `Engine` and
  `_adapt`s the internal wire schemas into its own contract, so an internal
  shape change is absorbed there rather than reaching a consumer. Its data
  module is `types`, not `models`, because `schema.py` discovers every
  `models.py` under `tt` as a mapped-table module.
- `tt/schema.py` — `create_all` and the metadata that binds the domains'
  tables to one engine.
- `alembic/` — the on-disk migration history. `alembic.ini` prepends the repo
  root (`.`) to the path and autogenerates against the default database.

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
extracting a function or splitting a module, that refactor is part of the task.

## Testing

The domain and action layers are what's worth testing, against a real in-memory
SQLite engine from `tests/conftest.py` (`connect("sqlite://")` held open by a
`StaticPool`, schema built with `create_all`). No mocks, and no interfaces
invented for test seams — add an abstraction when a second implementation
appears, not before. Fixtures seed rows through the create actions — the same
path a frontend takes — so what a test reads back is persisted, not hand-built.

- Table-driven where it fits, one case per invariant. Deterministic and
  order-independent.
- No sleeps for synchronization.
- Pure functions (an object's offered actions, a payload's derived form,
  selection resolution) are tested directly, without a database.
- Every action key gets two cases: the menu withholds it, and the write refuses
  it against live rows.
- A refusal is the object's answer, not a usage error — the CLI exits 123 on
  one, and that contract is tested.
- **Never delete, skip, or loosen an assertion to get a change passing.**
  If a test looks wrong, that's a stop-and-ask.

## Stop and ask

- Adding a dependency.
- Changing an Alembic migration that is already committed — never do this;
  schema changes are a new forward migration (`just db-revision`).
- Editing the mapped `models` in a way that changes the schema without a
  matching migration.
- Silencing a linter — a `# type: ignore`, a `# noqa`, or a `ruff`/
  `basedpyright` config change. Fix the finding instead; if it is a false
  positive, say so and let me decide.
- Bypassing a git hook.
- Expanding the task beyond its stated scope.
