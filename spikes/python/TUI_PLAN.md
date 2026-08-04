# TUI build plan — the lazygit of Linear

Turn the current three-screen drill-down TUI into the one that looks like the
[design study](https://claude.ai/code/artifact/acc1b8b4-29ff-4508-8405-b74be626e525):
one keyboard-driven, issue-centric screen that **opens on the project matching the
working directory**, with a Linear-style command menu powered by the action
system. Fidelity to that look is already proven in Textual (`tui_spike.py`).

This is a rewrite of `frontend/tui.py`, not the domain. Everything below the
frontend stays: `issue_api` / `project_api` are the only calls we make.

## What we keep

- **The api surface is the whole contract.** Reads: `issue_api.list_issues`,
  `issue_api.show` → `(IssueDetail, list[Offer])`, `project_api.list_projects`,
  `project_api.show`, `project_api.root_offers`. Writes: `issue_api.run`,
  `project_api.run`, `project_api.run_root`. Refusals: catch `REFUSALS`.
- **`Offer` is the menu.** `Offer(key, state: Runnable | Refused(reason), fields:
  list[Field])`. A menu row *is* an offer: runnable ones are selectable, refused
  ones greyed with `reason`. Nothing in the frontend names an action key to build
  the menu — it enumerates offers. Adding a sixth action changes no menu code.
- **`Field` drives every form.** `Field(name, required, kind: Text | OptionalText
  | Enum(values), description)`. The form controls (`Editing`, `Choosing`) and
  `action.payload(values)` carry over unchanged.
- **The pure-core discipline.** State + reducer stay plain Python, driven from
  tests with an in-memory DB and no terminal. See "Rendering seam" for the one
  change to how drawing is tested.

## What changes

| Now | Target |
|---|---|
| Three screens: `Projects` → `Issues` → `Detail` | One screen scoped to a project; project is a **filter**, not a level |
| Actions are rows mixed into the navigable list | Actions live in a **command menu** (`space`) + single-key accelerators; the list holds only issues |
| `> ` caret, monochrome `list[str]` | Indigo selection bar, glyph statuses, styled Textual widgets |
| One layout | `list` / `split` / `board`, `tab` cycles the ones that fit |
| No entry point from cwd | Bare `tt` resolves the project from `$PWD` |

## Architecture

### State

```
Scope      = Project(slug) | AllProjects
Layout     = "list" | "split" | "board"
Overlay    = None | CommandMenu | Switcher | Form | Capture | Cheatsheet | Filter

State:
    scope: Scope
    layout: Layout
    issues: list[IssueListItem]     # loaded for the scope
    selected_id: int | None         # an issue id, NOT a cursor index
    overlay: Overlay
    status: str                     # the message / hint line
    quit: bool
```

**Selection is an issue id.** Filtering, reload, and layout switches all reshuffle
rows; the cursor index is derived from `selected_id` each render and degrades to
the nearest surviving row when the selected issue disappears (Go design
invariant 1). This is a pure function — `resolve_index(issues, selected_id)`.

### Pure functions (unit-tested directly, no terminal — per CLAUDE.md)

- `columns(issues, layout) -> list[Column]` — one column for list/split, three
  (todo/doing/done) for board. Backs the board render and the header counts.
- `fits(layout, w, h) -> bool` — `split` needs ≥100 cols, `board` ≥90; `list`
  always fits. The whole responsive story in one tested function.
- `resolve_index(issues, selected_id) -> int` — selection survival.
- `menu_items(offers, accelerators) -> list[MenuItem]` — an `Offer` becomes a
  runnable or greyed row, with its accelerator key attached. Grouped ISSUE /
  PROJECT.
- `filter_issues(issues, query)` and `filter_projects(...)` — live substring.
- `glyph(status)`, `marker(priority)`, `issue_id_label(slug, id)` — the visual
  vocabulary, one place.
- `accelerators()` — the `{keystroke: action_key}` map (see keymap). This is the
  **one** place the frontend names action keys; it's a UX affordance, and the menu
  stays fully registry-driven so the two can't diverge on *availability*.

### Reducer

`on_key(state, key) -> Intent` and `apply(engine, state, intent) -> State` stay as
now: `on_key` pure, `apply` the only half that touches the DB (loads via `*_api`
reads, writes via `*_api.run*`, catches `REFUSALS`). New intents for the overlays
(open menu, open switcher, run accelerator, start capture, submit capture, filter
keystroke, cycle layout).

### Rendering seam

`render_lines(state) -> list[str]` is replaced. The design (selection bar, board
columns, floating menu) is **structural**, not just color, so `list[str]` can't
express it. Two honest options — pick one in Phase 0:

- **(A) Widget tree from State.** `TrackerApp` builds Textual widgets directly
  from `State`; the hard *decisions* are the pure functions above (tested), and a
  handful of Pilot (`run_test`) integration tests assert behavior + an SVG
  snapshot. Matches the Go design ("TUI tests cover the pure functions and
  nothing else").
- **(B) Pure view-model.** `view(state) -> ViewModel` returns typed regions
  (header, rows with semantic cells, menu, footer); tests assert on the
  ViewModel, `TrackerApp` maps it to widgets.

**Recommendation: (A).** It's less machinery, and CLAUDE.md already names
`columns()` / `fits()` / selection resolution as the things worth testing
directly. The current `test_tui.py` render assertions get rewritten to target the
pure functions + Pilot — a deliberate contract change, not a loosened assertion
(flagging per the "stop and ask" rule; call it out in the Phase 0 commit).

### Design tokens (from the study — single source for the TCSS)

| token | hex | use |
|---|---|---|
| bg / panel / menu | `#0B0C0F` / `#14161B` / `#1B1E25` | ground, bars, overlay |
| line | `#23262E` | hairline borders |
| text / muted / faint | `#E7E9EE` / `#8A8F99` / `#565B66` | title / id / disabled |
| accent | `#8B8CF0` | selection bar, menu border, active layout |
| accent-dim | `#8B8CF0` @ 14% | selected-row fill, hot menu row |
| todo / doing / done | `#8A8F99` / `#E3B341` / `#3FB950` | status glyphs |
| high | `#F0883E` | priority marker |

Glyphs: `○` todo, `◐` doing, `●` done; `▲` high; selected row = `▌` accent + dim
fill, no caret. Ids render `TT-4` (`slug` upper + `-` + id).

## Keymap

| Key | Action | Route |
|---|---|---|
| `j` `k` / arrows | Move within column | reducer |
| `h` `l` | Move between columns (board) | reducer |
| `tab` | Cycle layout (skip ones that don't `fit`) | reducer |
| `enter` | Open selected issue (split pane / full swap in list) | reducer |
| `space` | **Command menu** for the selected issue | `issue_api.show` offers |
| `s` `p` | Change status / priority | accelerator → `editStatus` / `editPriority` form |
| `n` | New issue — inline title prompt | `addIssue` (Capture) |
| `e` | Edit title/body | accelerator → `editTitle` form |
| `/` | Filter, live substring | reducer |
| `P` | **Project switcher** | `project_api.list_projects` |
| `A` | Toggle all-projects scope | reducer |
| `?` | Cheatsheet overlay (reads the accelerator table) | reducer |
| `esc` | Close overlay / clear filter | reducer |
| `q` | Quit | reducer |

The command menu is the discoverable superset; accelerators are the fast path.
Both end at `*_api.run`, so neither can reach an action the object doesn't offer.

## cwd → project resolution — OPEN DECISION

Bare `tt` should land in the project for `$PWD` 90% of the time. Isolate this in
one function `project_for_cwd(cwd) -> slug | None`; the mechanism behind it is the
decision:

1. **Stored `path → slug` table** (recommended) in `$XDG_STATE_HOME/tt/dirs.json`
   (or a small table). Explicit, survives renames, and `tt` in an unmapped dir can
   offer to bind the current dir to a project.
2. **`.tt` marker file** in the repo root naming the slug — git-like, travels with
   the checkout, but litters repos.
3. **Directory basename == slug** — zero config, fragile.

First cut stubs option 3 behind `project_for_cwd` so the screen works; the real
mechanism lands in Phase 5 without touching anything else. **Needs your pick.**

## Phases

Each phase builds and passes `just verify`; commit as you go, refactor before
behavior (per CLAUDE.md). No phase leaves the tree red.

- **Phase 0 — seam.** Introduce the pure functions (`columns`, `fits`,
  `resolve_index`, `menu_items`, `glyph`/`marker`) with tests; choose rendering
  option (A). Rewrite `test_tui.py` render assertions onto the new seam. No
  visible change yet. *Done when:* pure functions tested, old tests migrated,
  `tui.py` still runs the old view through the seam.
- **Phase 1 — the styled list.** Scope = one project (stub `project_for_cwd`).
  Issue rows with glyph/priority/id, indigo selection bar, top bar, footer keybar,
  `j/k`/arrows over `selected_id`. *Done when:* bare `tt` shows the study's list
  frame against real data.
- **Phase 2 — command menu + forms.** `space` opens the menu from `show` offers
  (runnable + greyed refusals with reason); pick → run (Empty payload) or open the
  `Field` form; single-key accelerators (`s`/`p`/`e`). *Done when:* the study's
  menu frame works end to end, including a real greyed refusal.
- **Phase 3 — capture + edits.** `n` inline new-issue prompt (`addIssue`), status
  message on write, `/` live filter. *Done when:* thought → stored in two
  keystrokes; filter narrows live.
- **Phase 4 — layouts.** `board` (three status columns) and `split`; `tab` cycles
  via `fits`; drop to the widest that fits on resize with a status marker; persist
  layout to `$XDG_STATE_HOME/tt/ui.json`. *Done when:* all three render and `tab`
  skips ones that don't fit.
- **Phase 5 — projects.** `P` switcher, `A` all-projects, project-level actions in
  the menu (archive/delete with their refusals), and the real `project_for_cwd`
  (per the decision above). *Done when:* `tt` opens on the cwd project and you can
  switch.
- **Phase 6 — polish.** `?` cheatsheet, trash/restore as a toggle or menu, reduced
  edge cases (tiny viewport message, empty-project invitation). *Done when:* it
  reads like the study, not a prototype.

## Testing

- **Pure functions** (`columns`, `fits`, `resolve_index`, `menu_items`,
  `filter_*`, `glyph`/`marker`, `accelerators`) — table-driven, one case per
  invariant, no DB, no terminal.
- **Reducer** (`on_key` + `apply`) — the existing `test_tui.py` style: real
  migrated in-memory SQLite from `dbtest`, drive keystrokes, assert on `State` and
  the messages `*_api.run` returns. Every accelerator gets two cases: the menu
  withholds a refused action, and the write refuses it against live rows.
- **Integration** — a few Textual `run_test` (Pilot) tests: `space` opens the
  menu, `enter` on a refused row shows the reason, `tab` skips an unfitting layout.
- **Visual regression** (optional, dev-only) — the `save_screenshot` → SVG →
  `rsvg-convert` pipeline from the spike. `rsvg` is an external tool, **not** in
  `go.mod`/`uv.lock`, so it stays a manual convenience and never a `verify`
  dependency.

## Stop-and-ask / risks

- **No new dependency expected** — `textual` is already pinned. If a widget need
  pulls one in (e.g. `textual`'s own extras), that's a stop-and-ask.
- **No `--json` / flag / exit-code changes** — this is the TUI only; the CLI
  contract is untouched.
- **No schema or migration changes.** cwd-mapping state (option 1) is a JSON file
  in XDG state, not a DB table, unless you'd rather it be a table — a schema change
  would be its own stop-and-ask.
- **The render-test rewrite in Phase 0** is the one place assertions change shape;
  it's a contract change from `list[str]` to the pure-function seam, done in the
  open, not a loosened test.

## Open decisions

1. cwd → project mechanism (stored table / `.tt` marker / basename). *Recommend
   stored table.*
2. Rendering seam (A) widget-tree vs (B) view-model. *Recommend (A).*
3. Does `s` open a status **picker** (todo/doing/done) or cycle? `editStatus`
   carries a status enum + optional note, so a small picker reusing the `Enum`
   control is the honest reuse. *Recommend picker.*
