# Sync: a server copy without giving up local-first

Status: proposal · 2026-08-04

`tt` today is one per-user SQLite file that a CLI and a TUI drive directly. We
want the same tracker on more than one device, without giving up that it works
fully offline against a local database. This is a design for a **server copy the
local databases sync against** — not a move to a server the clients talk to over
the wire. Each device keeps its own SQLite and its own offline life; a sync
reconciles it with the server.

The design is deliberately built so that **collaboration between people is not
foreclosed**. We ship the behaviour of one person across their own devices, but
nothing we persist assumes a single actor — because the history you didn't
record is the history you can never merge later.

## Goals

- A device reads and writes its local SQLite with no network, exactly as now.
- A sync exchanges changes with the server and leaves both sides consistent.
- Concurrent offline edits **never silently lose data**. A collision is
  resolved by a defined rule or surfaced for attention — it is never a row that
  quietly overwrites another.
- The data model carries who made each change, so multi-user is a later feature,
  not a rewrite.

## Non-goals (for now)

Deferrable because retrofitting them corrupts nothing:

- Authentication / login.
- A permissions and sharing model (who may see or edit a project).
- Realtime push, presence, notifications.

These stay out of the first cut. The schema below leaves room for them; the UI
and the wire protocol for them are a separate design.

## 1. Identity: UUIDv7 primary keys

Autoincrement integer primary keys cannot be synced. Two devices offline both
hand out `id = 42` to different rows, and the databases can no longer be merged.
This is the one invasive change, and it is the prerequisite for everything else.

Every table maps on `BaseDBModel` (`tt/platform/db.py`), so the key lives in
exactly one place, plus the foreign keys that point at it. The replacement is
**UUIDv7** — globally unique so two devices never collide, and time-sortable so
it preserves the insertion-order sorting we get today from autoincrement (the
issue list, the `created_at` tiebreak).

### Human handles stay short

The CLI shows `issue 42` and the user types `42`; nobody wants to type a UUID at
a prompt. Identity therefore splits into two roles:

- **Sync identity** — the UUIDv7, the true primary key, never shown.
- **Human handle** — the short number (or a `PROJ-42` per-project number) shown
  and typed.

The human handle is **assigned by the server on sync**. A row created offline
carries its UUID and a *provisional* marker for its handle; when it syncs, the
server allocates the real number and hands it back. The UI shows the provisional
state until then, so two devices creating issues offline never fight over the
same number.

## 2. The unit of sync: operations, not row snapshots

The foundational choice, and the one that is expensive to reverse: **we sync an
operation log, not row snapshots.** Retrofitting this later means rewriting the
sync layer *and* discovering that the per-field history that would have made old
data mergeable was never recorded.

`tt` is already built for this. Every write goes through one named action with a
typed payload and a single `transaction()` chokepoint (`tt/platform/actions/`,
`tt/platform/db.py`). That is not a row update that happens to have a name — it
is an event. Recording it is recording what we already model.

### We do not become event-sourced

The current `issues` / `projects` tables stay exactly as they are — the
materialised current state that the read and query layers hit, fast and indexed,
untouched. Alongside them we add one append-only `changes` table that the same
write path writes to. This is CQRS-lite:

- A write updates the row **and** appends a change, in the one transaction.
- Sync ships `changes` rows between device and server.
- Applying a remote change appends it to the log **and** folds it into the
  materialised row by the merge rule for that field (below).

Reads never learn any of this happened. The log buys us deterministic merges,
multi-user attribution, and an audit trail as a side effect.

### Actor identity

Every change carries the **actor** that made it — a device/user UUID to start.
This is load-bearing and must exist from the first change written: without it,
multi-user history can never be reconstructed, because the attribution was never
there to recover.

## 3. Ordering: a hybrid logical clock, not the wall clock

Conflict resolution leans on "the later write wins" — but *later by whose clock?*
Two offline devices with skewed clocks break wall-clock ordering: device B writes
at its 10:01 while device A writes at its 10:00, but B's clock runs five minutes
fast and B's write is causally *earlier*. Ordering by wall-clock silently picks
the earlier edit.

Changes are therefore stamped with a **hybrid logical clock** (HLC): physical
time nudged forward to respect causal order, with the actor UUID as the final
tiebreak. It reads like a timestamp — so the UI and the "later wins" intuition
still hold — but it is monotonic and deterministic across devices. Both the
scalar rule and the body merge below order by the HLC, never the raw clock.

## 4. Conflict resolution is per field

There is no single global merge rule. The right rule depends on the field, and
forcing one rule across all of them is where data gets lost. A **row-level**
last-write-wins is off the table outright: you edit an issue's `status` on your
phone while another device edits its `body`, both offline, and whoever syncs
second overwrites the *whole row* — one edit vanishes with no error. That is a
data-corruption bug, not a tradeoff.

Instead, a merge strategy is registered per field:

| field kind                          | strategy                                   | change record carries                     |
| ----------------------------------- | ------------------------------------------ | ----------------------------------------- |
| scalar (`status`, `priority`, `title`, `slug`, …) | last-write-wins                    | value, hlc, actor                         |
| `body` (free text)                  | three-way text merge + conflict fallback   | snapshot, hlc, actor, **parent_version**  |

### Scalars: last-write-wins

For a scalar, the later HLC wins. Two offline edits to `status` resolve to the
later one, and edits to *different* fields of the same row both survive because
each field resolves on its own. This is exactly the right semantic for a status,
a priority, a title.

### `body`: a three-way merge

A `body` is free text two people can extend concurrently, and last-write-wins
would throw one contribution away. It gets a real merge — and a real merge is a
**three-way** merge: the algorithm needs the common ancestor both sides started
from, then diffs each side against it. That is why the body's change record is
richer than a scalar's:

- A scalar needs `(value, hlc, actor)`.
- A body needs `(snapshot, hlc, actor, parent_version)` — the version it was
  edited *from*. Those parent pointers make body history a small version DAG per
  issue, which is the structure a three-way merge walks.

**Store snapshots, not patches.** Bodies are small; keeping the full text per
edit is cheap and means the merge has base / ours / theirs as three literal
strings and `diff3` is trivial. Reconstructing an ancestor from a chain of
patches is the fiddly path — we skip it.

### The unresolvable case is defined, not discovered

A three-way merge can still conflict — both sides rewrite the same region — and
git, when that happens, writes conflict markers and stops to ask a human. **Our
sync path cannot stop and ask**: it runs during a phone sync with nobody
watching. So the fallback is decided here, not found in production:

- Run `diff3`. Clean merge → take it.
- Hard conflict → **keep both** (a conflict copy, or inline markers) and flag the
  issue as needing attention. Never silently pick one side — that is the same
  silent-loss bug, only rarer and harder to notice.

Body merges cleanly when it is multi-line; two people rewriting the same single
prose paragraph will conflict no matter the algorithm, and that is acceptable as
long as the fallback preserves both.

## The sync exchange

With the log and the HLC in place, the exchange itself is small:

- The server is authoritative and holds the canonical `changes` log (over its own
  SQLite, or Postgres).
- Each device remembers a **watermark** — the point in the log it last synced.
- **Pull**: fetch every change after the watermark; fold each into the local
  materialised rows by the field's strategy.
- **Push**: send the device's changes the server has not seen; the server folds
  them the same way.
- A change is idempotent by `(uuid, hlc, actor)`, so a re-sent change is a no-op —
  a sync interrupted midway is safe to retry.

## What is load-bearing now

Decide and build now, because they cannot be retrofitted without losing data or
history:

1. **UUIDv7 identity** — the primary-key migration, plus the server-assigned
   provisional handle.
2. **The operation log as the unit of sync**, written from the existing
   `transaction()` chokepoint.
3. **Actor identity** on every change.
4. **HLC ordering** and the **per-field merge strategy** (LWW for scalars,
   three-way merge for `body`).

Everything under Non-goals can be added later against this foundation.

## Sequencing

1. **Identity migration.** Move `BaseDBModel` to UUIDv7, migrate the foreign
   keys, add the provisional-handle column and the server-side allocator. A new
   forward Alembic migration (`just db-revision`) — the committed history is
   never edited. This is the only step that reshapes existing code; the rest is
   additive.
2. **The change log.** Add the `changes` table and write to it from the action
   path, with actor and HLC. No sync yet — just recording.
3. **The merge engine.** The per-field strategy registry, the three-way body
   merge, the conflict fallback. Tested directly against the in-memory engine,
   the same way the action layer is.
4. **The sync endpoint.** The server, the watermark, pull/push. Small, once the
   log and the merge rules exist.

## Open questions

- **How far does the body merge go?** `diff3` line-based is the proposal. Is that
  enough, or do we want a word-level merge for prose that is one long line?
- **Human handle scheme.** A global issue number, or per-project `PROJ-42`? The
  latter is nicer to read but is more allocator bookkeeping.
- **`path` is device-local.** A project's `path` (the working directory it owns)
  means something different on each machine. It should not sync, or should sync
  per-device — decide before the log records it like any other field.
- **Server storage.** SQLite on the server is the least new machinery; Postgres
  buys concurrency we do not yet need. Start with SQLite?
