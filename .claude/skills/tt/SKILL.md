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

- **Project** — pinned to a repo, permanent, never completes. Holds issues and epics.
- **Epic** — a completable deliverable inside a project. Carries its **own status**
  (`active` / `done`) and an optional due date.
- **Milestone** — a dated checkpoint inside an **epic**: just a name and an
  optional due date, no status of its own (its progress is derived from its issues).
- **Issue** — the unit of work. Lives in a project and may *optionally* belong to
  one epic and one milestone (the milestone must be in the issue's epic), carry a
  due date, and wear any number of tags.
- **Tag** — a **global** label, many-to-many to issues only, for slicing across
  projects ("all bugs everywhere"). Not owned by any project.

It runs as a CLI over a shared per-user SQLite database — the same file the user's
TUI opens — so your writes and their TUI see the same data. You drive it by
running `tt` in the shell.

## Invocation

- If `tt` is on `PATH` (the user ran `just install`), call it directly: `tt issue ls`.
- Otherwise, from inside the repo, use `uv run tt …`.

Examples below use `tt`.

## The loop: read, then act

Every write is addressed to one object and checked against that object's **live**
state, so the reliable pattern is to read the current state and then act on it:

1. **List** to find the object (pass `--json` whenever you parse the output;
   without it you get a human-readable text table):
   - `tt project ls --json` — live projects, each with its issue rollup
     (`backlog`, `planning`, `todo`, `doing`, `done`).
   - `tt issue ls --json` — live issues; add `--project SLUG` to scope to one.
   - `tt epic ls --json` — live epics; add `--project SLUG` to scope to one.
   - `tt milestone ls --json` — live milestones; add `--epic ID` to scope to one.
   - `tt tag ls --json` — the global tag vocabulary.
2. **Show** the object you will act on:
   - `tt project show SLUG` / `tt issue show ID` / `tt epic show ID` /
     `tt milestone show ID`
   - Returns JSON with the object **and an `actions` array**: every action the
     object offers, each with its `key`, `label`, `state`, and the `arguments`
     its payload takes (`name`, `required`, `type`, and enum `values`). This is
     your source of truth for what you can do and exactly what to send — read it
     before every write.
3. **Act** with the key and a JSON payload:
   - `tt issue action ID KEY '<json>'`
   - `tt epic action ID KEY '<json>'`
   - `tt milestone action ID KEY '<json>'`
   - `tt project action KEY '<json>' --slug SLUG`  (omit `--slug` for a top-level create)
   - `tt tag action KEY '<json>' --id ID`  (omit `--id` for a top-level create)

## Actions

`show` lists the exact actions and their arguments for the object in front of
you. The full set:

- **Issues** — `edit`, `setStatus`, `setDueDate`, `tagIssue`, `untagIssue`, `delete`.
- **Projects** — `edit`, `delete`, `setPath` (associate a filesystem path),
  `addIssue`, `addEpic`. Plus the top-level `createProject`.
- **Epics** — `edit`, `setStatus`, `setDueDate`, `addMilestone`, `delete`.
- **Milestones** — `edit`, `setDueDate`, `delete`.
- **Tags** — `rename`, `delete`. Plus the top-level `createTag`.

Creating (top-level creates take no address):

- **Project:** `tt project action createProject '{"slug":"web","title":"Website"}'`
- **Issue** (a project action): `tt project action addIssue '{"title":"Fix login","priority":"high"}' --slug web`
- **Epic** (a project action): `tt project action addEpic '{"title":"v1","due_date":"2026-09-01"}' --slug web`
- **Milestone** (an epic action): `tt epic action ID addMilestone '{"title":"alpha","due_date":"2026-09-01"}'`
- **Tag** (top-level): `tt tag action createTag '{"name":"bug"}'`

Spelled-out subcommands exist for a person at a prompt
(`tt issue edit 1 --title … --status …`, `tt issue setDueDate 1 --due_date 2026-09-01`),
but prefer the JSON `action` path from an agent: it takes the same payload `show`
describes, verbatim.

## Issue statuses

An issue's status is one of, in order:
`backlog` → `requires_planning` → `todo` → `doing` → `done`. There is no status
machine — any status may follow any other. Use `setStatus` for a quick move
(`tt issue action ID setStatus '{"status":"doing"}'`), or set `status` as part of
the whole-object `edit`.

## `edit` is a whole-object write — read first

`edit` carries **every** editable field and sets each one. It is not a patch: send
only the field you mean to change and the rest are overwritten, or the required
ones are rejected. So:

**Always `show` the object first, take its current fields, apply your one change,
and send the complete set back.**

An issue's `edit` payload carries `title`, `body`, `status`, `priority`,
`due_date`, `epic`, and `milestone`. `due_date` is `YYYY-MM-DD` or `null`; `epic`
and `milestone` are ids or `null`. Blank/`null` clears the field.

```
tt issue show 1     # read every field as it is now
tt issue action 1 edit '{"title":"first issue","body":"","status":"doing","priority":"high","due_date":null,"epic":null,"milestone":null}'
```

Grouping an issue under an epic and milestone (the milestone must belong to that
epic, else the write is refused):

```
tt issue action 1 edit '{"title":"first issue","body":"","status":"doing","priority":"high","due_date":null,"epic":3,"milestone":7}'
```

Changing the epic to one the current milestone is not in **clears** the now-stale
milestone rather than blocking the move.

## Tags are their own verbs, not part of `edit`

Tags are a many-to-many, not a scalar column, so they are attached and detached by
**name** through focused verbs, not through the whole-object `edit`:

```
tt tag action createTag '{"name":"bug"}'   # the tag must exist first
tt issue action 1 tagIssue '{"name":"bug"}'
tt issue action 1 untagIssue '{"name":"bug"}'
```

`tagIssue` is idempotent; an unknown tag name is refused, and so is untagging a tag
the issue does not wear. An issue's current tags show up as `tags` in `issue show`.

## Refusals are answers, not errors

An object can refuse an action — a project will not archive or delete while it has
unfinished issues; an epic or milestone will not delete while live issues still
point at it; a duplicate tag name is refused. A refusal exits **123** and prints
the reason. That is the object's answer: **do not retry it unchanged.** Read the
reason and either satisfy it (reassign or close the blocking issues first) or tell
the user why it cannot be done.

That is different from exit **2**, a usage error (a missing required option, a
value outside an enum). Exit 2 *is* a mistake in how you called `tt` — fix the call
and retry.

`show` reports refusals ahead of time: an action whose `state` is `"refused"`
carries a `"reason"`. Check it before a write so you do not attempt something the
object will reject.
