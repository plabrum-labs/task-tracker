# tt — build phases

Companion to `DESIGN.md` (model, product decisions) and `IMPLEMENTATION.md` (stack,
layout, surfaces). This file owns sequencing: what blocks what, and what can run at the
same time.

## How to read this

- **Blocking phases** are serial. Everything downstream depends on them, so they get done
  before lanes open.
- **Lanes** inside a phase are independent: they touch disjoint files and don't import each
  other's unfinished work. Safe to run concurrently — by you across sessions, or by separate
  agents in worktrees.
- **Done when** is a checkable condition, not a vibe. If it isn't true, the phase isn't done
  and the next one isn't safe to start.

Two rules make the parallelism real rather than theoretical:

1. **The full schema lands in Phase 0**, including entities no feature uses yet. Atlas
   migrations are ordered and hash-verified in `atlas.sum`; two lanes generating migrations
   concurrently produces a conflict that's tedious to untangle. Declaring all five entities
   plus `Ref` up front means no lane after Phase 0 touches `ent/schema/` at all.
2. **One lane owns a file.** Where that's impossible, it's flagged below as a coordination
   point.

---

## Phase 0 — Foundation · blocking

Nothing else can start.

- `go.mod`, module `github.com/Plabrum/tt`.
- `ent/schema/`: **all five entities and the `Ref` edge schema**, complete — `Issue`,
  `Project`, `Milestone`, `Label`, `Comment`, `Ref` with its `kind` enum and the unique index
  on `(blocked, blocker)`. Including fields no verb reads yet.
- `TimeMixin`; `status` and `priority` as `field.Enum`.
- `atlas.hcl`, first migration generated into `migrations/`.
- Connection helper: `modernc.org/sqlite` → `entsql.OpenDB`, DSN pragmas
  (`foreign_keys`, `journal_mode(WAL)`, `busy_timeout`).
- Test harness: one helper returning a fresh in-memory client, schema via `Schema.Create`.

**Done when** `go test ./...` passes with a test that opens an in-memory DB, creates the
schema, and round-trips one issue with a label, a milestone, a comment, and a ref.

**Verify here, not later:** predicate support on Ent's update builder, needed for the
`start` compare-and-swap. Version-sensitive, and cheaper to discover now than in Phase 2.

**Applying migrations is not part of opening.** An earlier revision of this phase had
`internal/db` apply pending migrations on open, so that a fresh database was always
usable. That is reversed: applying is `just db-upgrade`, run by the Atlas CLI as its own
step, and `Open` only connects.

The cost is a real failure mode that did not exist before — a fresh database now gives
`no such table: projects` on the first verb. **Phase 2 owns the fix**, and it is not
optional: the CLI needs a first-run path and an upgrade path of its own. It also needs to
settle the store location, which nothing has yet — the `db-*` recipes default to `tt.db`
in the working directory, overridable via `TT_DB`, purely as a placeholder.

---

## Phase 1 — Domain core · blocking

The types and reads everything else is written against.

- `internal/db/open.go`, `internal/db/tx.go` — connection, pragmas, `WithTx`.
- `internal/errs/` — the four sentinels every layer returns.
- `internal/app/` — `api.go`, `capture.go`, `read.go`. One transaction per write method.
- `internal/issue/model.go` — `Issue`, `Detail`, `AddParams`. The ent-free view types.
- `internal/project/` — cwd → slug inference, upsert.
- `internal/issue/` — `repository.go`, `enum.go`, `service.go` for `Create` and `Get`.
- `internal/query/` — `Row` plus `List`, resolving labels, milestone name, and subtask rollup
  in one pass.

**Done when** `app.Add` + `app.List` are tested against in-memory SQLite, including project
auto-create, and no exported struct in `issue` or `query` exposes an ent type — the enum
conversions being the deliberate, documented exception.

Phase 1 is deliberately small: its only job is to freeze the view types and the `API` that
every lane below compiles against. `query.Row` and `issue.Detail` in particular are the
contract for lane 2C, which can't start until their fields are settled.

---

## Phase 2 — three lanes in parallel

Feature packages are the ownership boundary, so the lanes touch disjoint directories.

| Lane | Scope | Owns |
|---|---|---|
| **2A · CLI capture** | dispatch, `add`, `ls`, `show`, output rules, `--json`, exit codes | `main.go`, `cli/`, `cli/exit.go` |
| **2B · Work features** | lifecycle, refs, comments | `internal/issue/`, `internal/ref/`, `internal/comment/`, `internal/app/lifecycle.go`, `internal/app/refs.go`, `internal/app/capture.go` |
| **2C · Taxonomy features** | labels, milestones, and their `query/` aggregates | `internal/label/`, `internal/milestone/`, `internal/query/`, `internal/app/taxonomy.go` |
| **2D · TUI pure funcs** | `columns()`, selection resolution, `fits()` | `ui/columns.go`, `ui/select.go`, `ui/layout.go` |

2B and 2C split cleanly *because* of the acyclic rules in `IMPLEMENTATION.md`: taxonomy never
imports `issue`, so 2C never waits on 2B. The one crossing is `app.Add` needing
`label`/`milestone` resolve funcs — so 2C lands those two signatures first, and 2B, which
owns `internal/app/capture.go`, codes against them.

### 2A — CLI capture surface

Verb dispatch on `os.Args[1]`, per-verb `FlagSet`, shared global-flag helper. `add`, `ls`,
`show`. TTY detection, `NO_COLOR`, `tabwriter` tables, column drop order, `--json` encoding,
the exit-code table. Bare `tt` aliases `ls` for now.

**Done when** you can capture and list real work from a real terminal, `tt ls | grep` is
clean, and `tt ls --json | jq` parses.

### 2B — Work features

- `internal/issue`: `Start` (compare-and-swap + bounded retry), `Done` (`--force` bypass),
  `Drop`, surfaced as `app.Start`/`app.Done`/`app.Drop` in `internal/app/lifecycle.go`.
  `app.Done` is where `ref.AssertClosable` is called — it is an issue rule needing ref data,
  so it composes rather than importing across.
- `internal/comment`: `Note`.
- `internal/ref`: `Link`/`Unlink` with upsert-not-duplicate, cycle DFS carrying the path in
  the error, `AssertClosable`.
- `internal/frontmatter/` — parse/render the `$EDITOR` form. **Shared by 3A (`edit`) and 3B
  (`e`)**, so it gets built once, here, early.

**Done when** the Ref and lifecycle cases from `IMPLEMENTATION.md`'s testing section pass:
double-link updates one row, `subtask` and `dep` block eligibility identically, `done` refuses
on an open parent and succeeds with `--force`, two concurrent `Start` calls yield one winner.

### 2C — Taxonomy features

- `internal/label`, `internal/milestone`: upsert-on-first-use, resolve-by-name, set
  `due`/desc, delete — package-level funcs taking a client, like every other feature.
- `query/`: `Eligible` and `Next` (the one-level blocker predicate), subtask rollup, milestone
  progress, label open-counts. These live here, not in the taxonomy services, because they
  join against issues.

**Done when** rollup counts only `kind = subtask`, a subtask under two parents counts toward
both, milestone progress and label counts are correct with zero issues, and label/milestone
auto-create is idempotent.

### 2D — TUI pure functions

`columns(issues, GroupBy) []Column`, ID-based selection resolution with nearest-row
degradation, `fits(layout, w, h)`. No bubbletea import in this lane at all.

**Done when** all three are table-driven tested, including empty input, a status with zero
issues, and the `fits` boundary sizes (40×12, 80×24, 90×24, 100×30, 200×60).

---

## Phase 3 — two lanes in parallel

Both need 2B and 2C. The TUI lane also needs 2D.

| Lane | Scope | Owns |
|---|---|---|
| **3A · CLI lifecycle + organize** | `start`, `done`, `drop`, `note`, `dep`, `label`, `milestone`, `edit`, `rm`, `add --sub-of` | `cli/` |
| **3B · TUI** | bubbletea shell, `list` layout, then `split`/`board` | `ui/` |

### 3A — Remaining verbs

Lifecycle verbs, then the organize verbs, then `edit` (frontmatter via 2B's parser), `rm`
with TTY confirmation, bulk ids on `edit`, `add --sub-of`.

**Done when** all twelve verbs in `DESIGN.md` work, exit code 4 fires on an empty backlog,
and `tt start --json` prints a body an agent could act on.

### 3B — TUI

1. Shell: `Model`, `Update` with `tea.Cmd` for every DB call, `View`, `list` layout, `j`/`k`,
   `s`, `d`, `n`, `/`. **Flip bare `tt` from the `ls` alias to launching this** — the one
   line in `main.go` that 3A also touches (see coordination points).
2. `split` and `board`, `tab` cycling over fitting layouts, `tea.WindowSizeMsg` handling,
   degradation marker in the status bar, `ui.json` persistence.
3. Remaining bindings: `S`, `e` via `tea.ExecProcess`, `c`, `D`, `L`, `M`, `a`, `A`, `?`
   overlay driven by the same table that dispatches keys.

**Done when** bare `tt` opens the TUI, all three layouts render, `tab` skips ones that don't
fit, resizing degrades and restores cleanly, and `tt | cat` still prints a plain list.

---

## Phase 4 — polish · mostly parallel

Independent, small, any order:

- `export` — markdown snapshot, one-way, marked generated.
- `README` with a real terminal capture.
- `golangci-lint` config, CI running `go test ./...` and lint.
- `go install` verification on a clean machine.

---

## Coordination points

Three places where lanes touch the same thing:

1. **`main.go`** — 2A writes dispatch; 3B flips bare `tt` from `ls` to the TUI. Two lines,
   trivially mergeable, but land 2A's dispatch first.
2. **`internal/frontmatter/`** — 2B builds it; both 3A (`edit`) and 3B (`e`) consume it. If it
   slips, both consumers stall, so build it early in 2B rather than last.
3. **`ent/schema/`** — after Phase 0, nobody touches it. If a lane genuinely needs a schema
   change, it stops, the change lands on its own with its own migration, and lanes rebase.
   Concurrent `atlas migrate diff` runs corrupt `atlas.sum` ordering.
4. **`internal/app/`** — one file per lane, so lanes do not collide:

   | File | Owner |
   |---|---|
   | `api.go`, `read.go` | Phase 1; nobody after |
   | `capture.go` | 2B |
   | `lifecycle.go`, `refs.go` | 2B |
   | `taxonomy.go` | 2C |
   | `edit.go` | 3A |

5. **`app.Add` in `capture.go`** — 2B owns the file and writes the issue and ref calls, but
   2C's `label.Ensure`/`milestone.Ensure` calls land in the same method. 2C lands those two
   function signatures before anything else in the lane; 2B codes against them.

## Solo path

Running this alone, the lanes collapse to a linear order that keeps the tool usable as early
as possible:

**0 → 1 → 2A → 2C → 2B → 2D → 3B.1 → 3A → 3B.2 → 3B.3 → 4**

2A first is deliberate: it makes capture work end to end at the earliest possible moment,
which is the milestone that decides whether this tool survives. 2C before 2B because
`app.Add` calls into label/milestone resolution, so building taxonomy first avoids writing
against signatures that don't exist yet.
