---
name: tt
description: >-
  Manage personal tasks with the `tt` tracker — projects, epics, milestones,
  issues, and tags in a local SQLite database. Use whenever the user wants to
  add, list, show, update, prioritize, move, start, close, group, or tag a task /
  issue / project / epic / milestone in tt, or asks what they are working on.
  Drives the `tt` command-line tool.
---

# Driving the `tt` task tracker

`tt` is a personal task tracker. Its containers, from outermost in:

- **Project** — pinned to a repo, permanent, never completes. Holds issues, epics
  and milestones.
- **Epic** — a completable deliverable inside a project. Carries its **own status**
  (`active` / `done`) and an optional due date.
- **Milestone** — a dated checkpoint inside a **project**: a title and an optional
  due date, no status of its own (its progress is derived from its issues). It may
  carry one **epic** as an optional label, but it is not owned by an epic — a
  checkpoint can span several epics or none.
- **Issue** — the unit of work. Lives in a project and may *optionally* belong to
  one epic and one milestone (two independent links — the milestone need **not**
  be in the issue's epic), depend on other issues in the same project, carry a
  due date, and wear any number of tags.
- **Comment** — a dated note under an **issue**: just a body. A thread of them
  builds up over time; each is editable and deletable. Addressed by its own
  global `id` (not a ref).
- **Tag** — a **global** label, many-to-many to issues only, for slicing across
  projects ("all bugs everywhere"). Not owned by any project.

It runs as a CLI over a shared per-user SQLite database — the same file the user's
TUI opens — so your writes and their TUI see the same data. You drive it by
running `tt` in the shell.

The `tt sync` group (mirroring the database across the user's devices over ssh) is
a **device-maintenance** operation, not part of the task loop — do not run it while
managing tasks unless the user explicitly asks you to sync.

## How each object is addressed

- **Issue** — by its **ref**: the project's slug, a hyphen, and a per-project
  number, `TT-12`. Numbering is scoped to the project, so each project counts from
  1 and `TT-1` and `WEB-1` are different issues; the number never resets or gets
  reused. A `show`/`ls` reports the ref as `ref`. Only issues carry a ref.
- **Epic / Milestone** — by its **title within a project**: pass the title as the
  argument and the project as `--project SLUG`
  (`tt epic show "Payments" --project TT`). A live project cannot hold two epics —
  or two milestones — with the same title, so the pair (project, title) is unique.
  Epics and milestones have **no ref**: nothing like `TT-3` addresses them.
- **Project** — by its **slug**. Slugs are stored **uppercase** and resolved
  case-insensitively, so every ref reads `TT-12` regardless of how the slug was
  typed. You may pass a slug in any case.
- **Tag** — by its `--id` (tags are global, not per-project).

## Invocation

- If `tt` is on `PATH` (the user ran `just install`), call it directly: `tt issue ls`.
- Otherwise, from inside the repo, use `uv run tt …`.

Examples below use `tt`.

## Writing: one verb per field, no JSON, no read-first

Each write is a **subcommand named after what it sets**, taking that field as an
option. Run it straight against the object's address — you do **not** `show` the
object first, and you do **not** hand-build JSON:

```
tt issue setStatus TT-1 --status doing
tt issue setDueDate TT-1 --due_date 2026-09-01
tt issue tagIssue TT-1 --name bug
tt issue addDependency TT-1 --dependency TT-4
tt issue addComment TT-1 --body "shipped the fix"
tt issue delete TT-1
```

The write is addressed to the **live** object and checked against it. If the
object won't allow it you get a refusal (exit **123**) carrying the reason — that
*is* the answer (see Refusals). So act, and let the object refuse; don't read to
pre-check.

Every object's verbs (run `tt <group> <verb> --help` for a verb's exact options):

- **Issue** — `setStatus`, `setDueDate`, `tagIssue`, `untagIssue`,
  `addDependency`, `removeDependency`, `addComment`, `edit`, `delete`.
- **Project** — `edit`, `setPath` (bind a filesystem path), `addIssue`,
  `addEpic`, `addMilestone`, `delete`. Plus `init` (create).
- **Epic** — `setStatus`, `setDueDate`, `edit`, `delete`.
- **Milestone** — `setDueDate`, `edit`, `delete`.
- **Comment** — `edit`, `delete`.
- **Tag** — `rename`, `delete`.

`edit` is the exception: it is a **whole-object** write, not a focused verb — see
its section below. There is no focused verb for an issue's `title`, `body`,
`priority`, `epic`, or `milestone`, so those five go through `edit`.

## Reading

- **`tt <group> ls`** — the live list. Bare it is a text table; add `--json`
  whenever you parse it.
  - `tt project ls --json` — projects, each with a `counts` rollup (a mapping of
    every issue status to its tally: `backlog`, `blocked`, `todo`, `doing`,
    `done`, `canceled`).
  - `tt issue ls --json` — issues; `--project SLUG` scopes to one. Filter with
    `--tag NAME`, `--epic TITLE`, `--milestone TITLE` (the last two need
    `--project`); order with `--sort <field>[:asc|desc]`, field one of
    `priority`, `created`, `updated`, `due`, `status`.
  - `tt epic ls --json` / `tt milestone ls --json` — add `--project SLUG` to
    scope; milestones also take `--epic TITLE` (needs `--project`).
  - `tt tag ls --json` — the global tag vocabulary.
- **`tt <group> show <address>`** — one object, every field, as JSON (an issue by
  `REF`; a project by `SLUG`; an epic/milestone by `TITLE --project SLUG`; a
  comment by `id`). It carries only the object — no action schema.
- **`tt <group> actions <address>`** — what the object offers *now*: each action's
  `key`, `label`, and `state` (`runnable`, or `refused` with a `reason`). A
  planning aid, not a required pre-check — you don't have to call it before a
  write, and it does not repeat each verb's option schema (that's `--help`).

## Creating

Creates are their own subcommands too, addressed by the parent:

```
tt project init web --title "Website"                              # a new project
tt project addIssue web --title "Fix login" --priority high        # an issue in web
tt project addEpic web --title v1 --due_date 2026-09-01            # an epic in web
tt project addMilestone web --title alpha --due_date 2026-09-01 --epic v1
tt issue addComment TT-1 --body "first note"                       # a comment on TT-1
tt tag action createTag '{"name":"bug"}'                          # tags: see below
```

A milestone's `--epic` is the **title** of an epic to file it under, and is
optional — omit it for a milestone that stands on its own.

## The JSON `action` path is the fallback

Every verb above is the spelled-out form of one underlying action. The raw form
takes the action key and a JSON payload:

```
tt issue action TT-1 setStatus '{"status":"doing"}'
```

Prefer the named verbs — the JSON path means shell-quoting a payload, which is
what to avoid. Reach for `action` only where there is no verb: the top-level
`createTag` and `createProject` (`tt tag action createTag '{"name":"bug"}'`,
`tt project action createProject '{"slug":"web","title":"Website"}'`).

## Issue statuses

An issue's status is one of, in order:
`backlog` → `blocked` → `todo` → `doing` → `done` → `canceled`. `done` and
`canceled` are the two **terminal** statuses — work that needs no more doing,
whether it finished (`done`) or was abandoned (`canceled`); a dependency at
either no longer holds its dependents back. There is no status machine — any
status may follow any other. Use the focused `setStatus` verb for a move
(`tt issue setStatus TT-1 --status doing`); `edit` can also set it, but only as
part of the whole object.

## `edit` is a whole-object write — read first, then use flags

`edit` carries **every** editable field and sets each one at once. It is not a
patch: each field is required, so any you leave out is either rejected or
overwritten. So for an `edit`:

**`show` the object first, take its current fields, apply your one change, and
pass the complete set as flags** (flags, not JSON — no quoting to get wrong):

An issue's `edit` fields are `--title`, `--body`, `--status`, `--priority`,
`--due_date`, `--epic`, and `--milestone`. `--due_date` is `YYYY-MM-DD`; `--epic`
and `--milestone` are **titles** (e.g. `"Payments"`) and must name an epic /
milestone in the issue's own project. A blank value (`--body ""`) clears that
field.

```
tt issue show TT-1     # read every field as it is now
tt issue edit TT-1 --title "first issue" --body "" --status doing \
  --priority high --due_date "" --epic "" --milestone ""
```

Grouping an issue under an epic and a milestone is done through this same `edit`,
and the two are **independent** links: the milestone need not belong to the named
epic, and either may be set or cleared without regard to the other
(`--epic "Payments" --milestone "Beta launch"`).

Because only `edit` reaches `title`, `body`, `priority`, `epic`, and `milestone`,
those are the one case that still needs a `show` first; everything else has a
focused verb that does not.

## Tags are their own verbs, not part of `edit`

Tags are a many-to-many, not a scalar column, so they are attached and detached by
**name** through focused verbs, not through the whole-object `edit`:

```
tt tag action createTag '{"name":"bug"}'   # the tag must exist first (no create verb)
tt issue tagIssue TT-1 --name bug
tt issue untagIssue TT-1 --name bug
```

`tagIssue` is idempotent; an unknown tag name is refused, and so is untagging a tag
the issue does not wear. An issue's current tags show up as `tags` in `issue show`.

## Dependencies are their own verbs too, and go by ref

An issue can depend on other issues **in the same project**. Like tags, the graph
is edited through focused verbs, not `edit`, and the other issue is named by its
**ref**:

```
tt issue addDependency TT-1 --dependency TT-4      # TT-1 now waits on TT-4
tt issue removeDependency TT-1 --dependency TT-4
```

`addDependency` is idempotent (an edge already there is a no-op that still
succeeds). It is refused for a ref in another project, a self-edge, or an edge
that would close a cycle. `removeDependency` is refused for an edge the issue does
not carry.

`issue show` reports the graph as refs: `depends_on` (what must finish before this
issue can start) and `dependents` (what waits on it). The derived `waiting` is
`true` when a live dependency is not yet at a terminal status — surfaced in
`issue ls` too, so a blocked-on-work issue reads as waiting without opening it.

## Comments are added on the issue, then edited on their own

A comment is a dated note under an issue. Add one through the issue's `addComment`;
the issue's live comments (id, body, timestamps) show up as `comments` in
`issue show`, oldest first. Each comment is then addressed by its own `id` — not a
ref — for its own `edit` (a whole-object write of the body) and `delete`:

```
tt issue addComment TT-1 --body "first note"   # creates comment id N
tt issue show TT-1                              # comments[] lists it
tt comment show N                              # the comment on its own
tt comment edit N --body "revised"
tt comment delete N
```

A blank body is refused on both `addComment` and `edit`.

## Refusals are answers, not errors

An object can refuse an action — a project will not archive or delete while it has
unfinished issues; an epic or milestone will not delete while live issues still
point at it; creating or renaming an epic or milestone to a title a live sibling
in the same project already holds is refused, as is a duplicate project slug or
tag name; a dependency edge that crosses projects, points at itself, or would
close a cycle is refused. A refusal exits **123** and prints the reason. That is the object's answer: **do not retry it unchanged.** Read the
reason and either satisfy it (reassign or close the blocking issues first) or tell
the user why it cannot be done.

That is different from exit **2**, a usage error (a missing required option, a
value outside an enum). Exit 2 *is* a mistake in how you called `tt` — fix the call
and retry.

`tt <group> actions <address>` reports refusals ahead of time: an action whose
`state` is `"refused"` carries a `"reason"`. It's there to consult when planning,
but you needn't pre-check — attempting the write and reading the 123 is
equivalent.
