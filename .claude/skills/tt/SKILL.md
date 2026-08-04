---
name: tt
description: >-
  Manage personal tasks with the `tt` tracker — projects and issues in a local
  SQLite database. Use whenever the user wants to add, list, show, update,
  prioritize, move, start, or close a task / issue / project in tt, or asks what
  they are working on. Drives the `tt` command-line tool.
---

# Driving the `tt` task tracker

`tt` is a personal task tracker: **projects** contain **issues**. It runs as a
CLI over a shared per-user SQLite database — the same file the user's TUI opens —
so your writes and their TUI see the same data. You drive it by running `tt` in
the shell.

## Invocation

- If `tt` is on `PATH` (the user ran `just install`), call it directly:
  `tt issue ls`.
- Otherwise, from inside the repo, use `uv run tt …`.

Examples below use `tt`.

## The loop: read, then act

Every write is addressed to one object and checked against that object's **live**
state, so the reliable pattern is to read the current state and then act on it:

1. **List** to find the object:
   - `tt project ls --json` — every live project as a JSON array.
   - `tt issue ls --json` — every live issue; add `--project SLUG` to scope to one.
   - Pass `--json` whenever you parse the output. Without it you get a
     human-readable text table, not JSON.
2. **Show** the object you will act on:
   - `tt project show SLUG` / `tt issue show ID`
   - Returns JSON with the object **and an `actions` array**: every action the
     object offers, each with its `key`, `label`, `state`, and the `arguments`
     its payload takes (`name`, `required`, `type`, and enum `values`). This is
     your source of truth for what you can do and exactly what to send — read it
     before every write.
3. **Act** with the key and a JSON payload:
   - `tt issue action ID KEY '<json>'`
   - `tt project action KEY '<json>' --slug SLUG`

## Actions

`show` lists the exact actions and their arguments for the object in front of
you. The set is small:

- **Issues** — `edit`, `delete`.
- **Projects** — `edit`, `delete`, `setPath` (associate a filesystem path),
  `addIssue`.

Creating:

- **New project** (top-level — no `--slug`):
  `tt project action createProject '{"slug":"web","title":"Website"}'`
- **New issue** (a project action, addressed by `--slug`):
  `tt project action addIssue '{"title":"Fix login","priority":"high"}' --slug web`

Spelled-out subcommands exist for a person at a prompt
(`tt issue edit 1 --title … --status …`), but prefer the JSON `action` path from
an agent: it takes the same payload `show` describes, verbatim.

## `edit` is a whole-object write — read first

`edit` carries **every** editable field and sets each one. It is not a patch:
send only the field you mean to change and the rest are overwritten, or the
required ones are rejected. So:

**Always `show` the object first, take its current fields, apply your one change,
and send the complete set back.**

Moving issue 1 to `doing` while keeping everything else:

```
tt issue show 1     # read title / body / priority as they are now
tt issue action 1 edit '{"title":"first issue","body":"","status":"doing","priority":"high"}'
```

`status_note` is part of that whole-object edit: a status move that carries no
`status_note` **clears** any existing note — the note describes the status the
issue arrived at, so it does not outlive the move. Pass `status_note` when you
want to record why.

## Refusals are answers, not errors

An object can refuse an action — a project will not archive or delete while it
has unfinished issues, for instance. A refusal exits **123** and prints the
reason. That is the object's answer: **do not retry it unchanged.** Read the
reason and either satisfy it (finish or drop the blocking issues first) or tell
the user why it cannot be done.

That is different from exit **2**, a usage error (a missing required option, a
value outside an enum). Exit 2 *is* a mistake in how you called `tt` — fix the
call and retry.

`show` reports refusals ahead of time: an action whose `state` is `"refused"`
carries a `"reason"`. Check it before a write so you do not attempt something the
object will reject.
