# tt — design

A slim, global, local-first task tracker. Primary interface is an interactive CLI.
Two consumers: a human at a terminal, and headless agent sessions picking work off
the backlog.

## Principles

- Capture friction is what kills a personal tracker. `tt add "thing"` must be the
  whole interaction. Containers auto-create on first use; nothing needs registering.
- Global store, auto-scoped to the current project. No flags in the common case.
- Derive rather than store. Anything stored twice will desync.
- Child entities over special-case columns. Prose belongs in a row, not a `log` field.
- No recursive SQL. Graph walking happens in Go.
- Every field and verb earns its place against a felt pain, not a predicted one.

## Stack

| Concern | Choice |
|---|---|
| Language | Go |
| Schema | Ent — declarative, in Go (`ent/schema/*.go`) |
| Migrations | Atlas, versioned, diffed from the Ent schema |
| Driver | `modernc.org/sqlite` (pure Go, no cgo) via `entsql.OpenDB(dialect.SQLite, db)` |
| CLI | stdlib `flag` + subcommand dispatch |
| Output | `text/tabwriter` |
| TUI | `bubbletea` + `lipgloss` |

SQLite specifics that bite:

- Foreign keys are **off by default**. The DSN must enable them:
  `file:tt.db?_pragma=foreign_keys(1)&_pragma=journal_mode(WAL)`.
- `ALTER TABLE` is limited, so column drops and renames require a table rebuild.
- Versioned migrations rather than Ent's runtime auto-migrate, because a backfill and a
  rename are statements someone has to write. Auto-migrate cannot express either at any
  flag setting: it diffs a schema against a database and emits DDL, so a rename reads as
  a drop plus an add, and there is no point at which data can be moved between them.
  Auto-migrate is still what the tests use — they have no data to preserve.
- Migrations are generated against a throwaway dev database, wired up in `atlas.hcl`:
  `just db-migrate <name>` → `atlas migrate diff --env local <name>`

## Data model

Seven logical tables. Ent owns the two join tables, so we declare five entities and
let edges generate the rest.

### Entities

**`Project`** — `slug` (unique), `title`, `description`, `created_at`
Auto-created on first use. See scoping below.

**`Milestone`** — `name`, `due` (nullable), `description`
Belongs to one project, `UNIQUE(project, name)`. Auto-created on first use.

**`Issue`** — `title`, `body` (markdown), `status`, `priority` (enum: `normal`/`hi`),
`created_at`, `updated_at`, `closed_at` (nullable)
`body` is the instruction set an agent reads, and nothing else.

**`Label`** — `name` (unique), `description`
Global, not project-scoped. Auto-created on first use.

**`Comment`** — `at`, `author`, `body` (markdown)
The issue log. `author` is free text: `phil` | `agent:<session>`.

### Edges

| Edge | Kind | Notes |
|---|---|---|
| `Project` → `Issue` | O2M | |
| `Project` → `Milestone` | O2M | |
| `Milestone` → `Issue` | O2M | An issue has at most one milestone |
| `Issue` ↔ `Label` | M2M | Ent generates the join table |
| `Issue` → `Comment` | O2M | Cascade delete |
| `Issue` → `Issue` | M2M, self-referential, edge schema | `Blocks` / `BlockedBy` through `Ref` |

The self-referential `Blocks`/`BlockedBy` pair replaces the hand-written `dep` table.
This is the part of the design Ent handles better than raw SQL — the join table and
both traversal directions come for free and typed.

`Ref` carries one field, `kind` — `field.Enum("kind").Values("dep", "subtask").Default("dep")`.
An edge schema (`edge.To(...).Through("refs", Ref.Type)`) rather than a plain M2M, because
a plain join table has no room for it.

### Ent-specific notes

- **`status` is `field.Enum("status").Values("todo","doing","done").Default("todo")`**,
  which generates typed constants. Better than the stringly-typed column a `.sql`
  schema would have given us.
- **Timestamps are schema-level defaults**: `Default(time.Now).Immutable()` on
  `created_at`, `UpdateDefault(time.Now)` on `updated_at`. No manual stamping in any
  handler, so `updated_at` cannot be forgotten.
- **Use a `TimeMixin`** for `created_at`/`updated_at` across entities rather than
  repeating the fields.
- **`Project` keeps Ent's default int `id` plus a unique `slug` string field**, rather
  than making `slug` the primary key. Ent supports custom ID types, but going against
  the default int id makes every edge and every generated builder more awkward. The
  slug stays the thing you type; the int stays the thing edges point at.

## Project scoping

A real entity with real edges, but **rows are created implicitly**. `tt add "thing"`
inside `~/repos/foo` upserts `Project{slug: "foo"}` and moves on. There is no
`tt project init`, because ceremony in the capture path is what kills this tool.

Resolution order:

1. `--project X` (per-call override)
2. `$TT_PROJECT`, or a `.tt` file in the tree
3. Nearest ancestor `.git` directory's basename
4. cwd basename

Every read command scopes to the inferred project. `-A/--all` escapes it. So `tt`
inside `~/repos/foo` lists foo's work with no flags; `tt -A` lists everything.

## Milestone vs label

Separate entities, because they differ in both ways that matter:

| | `Milestone` | `Label` |
|---|---|---|
| Cardinality | One per issue (O2M) | Many per issue (M2M) |
| Deadline | Has `due`; overdue is a real query | Never. `backend` cannot be late. |
| Scope | Per-project (`v1` of foo ≠ `v1` of bar) | Global (`bug` means `bug` everywhere) |
| Answers | "What's left before we ship?" | "What kind of work is this?" |

An earlier draft had these as one table with a nullable `due`. That looked economical
but meant every deadline query branched on which subset it was looking at — the same
polymorphism problem as a `Container` table with a `kind` column, just smaller.

## Blocking is derived, and needs no recursion

An issue is eligible iff it is `todo` and has no unfinished direct blocker:

```go
client.Issue.Query().
    Where(
        issue.StatusEQ(issue.StatusTodo),
        issue.HasProjectWith(project.SlugEQ(slug)),
        issue.Not(issue.HasBlockedByWith(issue.StatusNEQ(issue.StatusDone))),
    ).
    Order(ent.Desc(issue.FieldPriority), ent.Asc(issue.FieldCreatedAt))
```

**The one-level check is complete, not an approximation.** Transitive blocking falls
out for free: a blocker that is itself blocked cannot be `done`, so it still fails the
predicate. There is no depth to walk, and no recursive CTE anywhere in the read path.

Close a blocker and its dependents become eligible on the next query — nothing to
update, nothing to desync. A stored `blocked` state goes stale the first time you close
a dependency, which is why it isn't in the model.

## Subtask is a kind of dependency, not a second entity

Every subtask is a dependency — the parent can't be `done` while it's open — but not
every dependency is a subtask (`redesign` blocked by `legal-signoff` isn't a
decomposition of `redesign`). Dependency is the general relation; subtask is a labeled
subset of it, so it rides on the same `Ref` edge rather than a parent/child pointer or
a second table.

`dep story on subtask1` links them as a plain dependency. `dep story on subtask1
--subtask` sets `kind = subtask` on the same edge. Nothing else changes: eligibility,
cycle rejection, and the one-level-is-complete argument all still operate on `Blocks`/
`BlockedBy` as a whole, `kind` only changes how `show` groups and labels what it lists.
A DAG also does something a tree can't: a `subtask` issue can sit under two parents at
once, which a strict outline has to duplicate to express.

This is deliberately *not* a hierarchy entity or a parent-pointer column — that would
reopen the sub-issue-hierarchy question this design otherwise avoids. `kind` only
distinguishes how an existing edge reads; it adds no new traversal, no new query shape.

## Cycle rejection happens in Go

`dep` edges must stay acyclic, or two issues can block each other into permanent
invisibility with no explanation. Detection is a DFS in application code, not SQL:
load the edge set, walk from the proposed blocker looking for the blocked id, reject
with the cycle path in the error message.

At a few hundred issues with sparse edges this is instant, and it produces a better
error than SQL could — `cannot link: 12 → 19 → 7 → 12`.

## States

Three.

| State | Meaning |
|---|---|
| `todo` | Candidate. Eligible iff no unfinished blocker. |
| `doing` | In flight, or stalled. Not eligible. |
| `done` | Terminal. |

`doing` is the catch-all for "not eligible by someone's choice" — actively worked and
stalled-with-a-reason look identical to the pick query. An agent that can't do a ticket
leaves it in `doing` and writes a `note`; the loop skips it and you decide.

No `blocked` (derived), no `shelved`, no `review`, no `snoozed`.

## Verbs

| Verb | Notes |
|---|---|
| `add <title>` | `-b <body>`, `-e` ($EDITOR), `-l <label>`, `-M <milestone>`, `-!` |
| `ls [query]` | Default: this project, `todo`+`doing`. `-a` all statuses, `-A` all projects, `-l`, `-M`, `--blocked`, `--json`. Positional arg is substring match — no `search` verb. |
| `show <id>` | Body, metadata, labels, blockers, dependents, comments. Subtasks list separately from plain blockers. |
| `edit <id>...` | `$EDITOR` form, or flags: `--title`, `--body`, `-l`/`-L` add/remove label, `-M`, `--project`, `-!`. Multiple ids — the bulk-retriage path. |
| `done <id>` | `-m <text>` writes a closing comment. Legal from any state. |
| `start [id]` | With an id: take it. Without: take the top eligible issue (agent entry point). `--json` prints the body. |
| `drop <id>` | `doing` → `todo`. Also the cleanup path for work a dead session left behind. |
| `note <id> <text>` | Add a comment. `-` reads stdin. |
| `dep <id> on <id>` | `--off` to unlink. `--subtask` marks the edge as decomposition rather than a plain prerequisite. Rejects cycles. |
| `label <name>` | `--desc`, `--rm`. Bare `tt label` lists labels with open counts. |
| `milestone <name>` | `--due`, `--desc`, `--rm`. Bare `tt milestone` lists milestones with progress and due dates. |
| `rm <id>` | Hard delete, for typos. Cascades edges, labels, comments. |

Twelve. `-m` is message (git convention), `-M` is milestone.

No `next` — the top row of `ls` is the preview, `start` with no id is the mutating
version. No `project` verb; rows auto-create and there's nothing to configure yet.

## Pick order

`priority DESC, created_at ASC` over the eligible set. Not a scoring function, not manual
rank:

- Scoring functions become unexplainable ("why did it pick #37?").
- Manual rank needs curation you won't sustain, and a stale rank is worse than age.
- Recurring filters like "bugs first" belong in a saved query, not the ordering.

Dependencies do the ordering work a rank column would have: if #12 must come first,
say so with `dep`, and the pick query can't get it wrong.

## Starting work

SQLite has no `SELECT … FOR UPDATE`, and Ent can't express
`UPDATE … WHERE id = (SELECT …)`. So `start` with no id is a **compare-and-swap inside
a transaction**: query the top eligible id, then issue a predicate-guarded update.

```go
n, err := tx.Issue.Update().
    Where(issue.IDEQ(id), issue.StatusEQ(issue.StatusTodo)).
    SetStatus(issue.StatusDoing).
    Save(ctx)
// n == 0 means someone else took it; re-query and retry.
```

The guard is what makes it safe — two concurrent sessions cannot both see `n == 1`.
Retry a bounded number of times, then report an empty backlog. (Confirm the exact
predicate form against the pinned Ent version.)

**Crash recovery is a human running `drop`.** A dead session's ticket sits in `doing`;
you see it in `ls` and `drop` or `done` it. No claim ownership, no PID tracking, no
liveness checks, no timeouts, no `reap`.

## Agent contract

`--json` on every read command, plus stable exit codes. `tt start --json` printing the
body *is* the agent interface.

## Deliberately absent

Assignees and claim ownership, a transition/event table, `undo`, a separate hierarchy
entity or parent-pointer column (subtasks are a labeled `dep`, not a tree — see
above), epics, `resolution` and `log` columns, recursive CTEs, cycles/sprints, estimates,
velocity, custom workflow config, `review`/`submit`/`merge`,
`blocked`/`shelved`/`snoozed` states, retry ceilings, manual ranking, and any form of
crash recovery.

## Deferred, with the trigger that would justify it

| Thing | Add it when |
|---|---|
| Structured transition events (and `undo` on top) | You want to *query* history — attempt counts, throughput, what got dropped repeatedly. Comments are prose and can't answer that. |
| A `commit`/`ref` field on issue | "Which commit closed #12" needs to be a query, not a comment you read. |
| Comment edit/delete | You typo a comment and care. Append-only until then. |
| `snoozed_until` (nullable, no new state) | Deferred-not-started tickets clutter `ls`. Known cost of cutting blocking states. |
| Stall detection ("doing since" in `ls`) | You've lost a ticket to a dead session and didn't notice. |
| FTS5 across `title`/`body`/`comment` | Substring `ls` search gets slow or imprecise. |
| Project-scoped labels | Label names start colliding across projects. |
| Milestone ordering | Enough milestones per project that due-date sort isn't enough. |
| A third priority level | Two genuinely isn't enough. |
| Linear / `ISSUES.md` import | You need to move a live backlog in. |
