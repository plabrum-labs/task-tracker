# spikes — the goal

Two implementations of one design, OCaml and Rust, over the same slice
(`README.md` has the action table).

**The criterion is how well an action declaration reads.** The target is snacks'
Python: a class header carrying the object and the payload type, a decorator
registering it in place, `execute`, and nothing else. Where the two languages
differ, what counts is which one gets closest to that without giving up the
guarantees below.

Framework machinery is in scope — a proc macro, a ppx, a functor, whatever the
language's answer to a generic base class and a decorator is. snacks'
readability comes from exactly that, so hand-writing everything measures the
languages without the thing that makes the Python read well.

## Must be true

- **One declaration per action.** Its key, its hooks, its payload type, and from
  that type both the schema and the decoder. Nothing anywhere states any part of
  it a second time, and nothing in it is ceremony a reader has to skip.
- **Adding an action changes no frontend line.** The menu is what the object
  offered; the form, the subcommand and its `--help` are what the schema
  advertised. No frontend names an action key.
- **The mistakes don't compile.** Pairing an action with the wrong payload,
  reaching `execute` around availability, choosing an action by the payload's
  runtime type, resolving a key before its object.
- **A frontend never invents a payload.** A schema it cannot render fails and
  says so, rather than dropping the field and submitting something the decoder
  refuses for a reason nothing on screen mentions.
- **One public call is one transaction.** The CLI and the TUI open it at the
  edge, exactly as an endpoint does, and the framework hands it to `execute`. A
  refusal rolls back, including one that comes after rows are written. What
  `execute` does with the transaction is its own business — any rows, any
  tables. The framework has no opinion, and an action stays dumb.

The third bullet outranks readability, and enforcement is where that bites.
snacks checks availability in `trigger()` at the edge, and `execute` stays a
public classmethod that nothing stops a caller reaching directly — so the
guarantee there is a convention. Rust's `Checked` token and OCaml's `action.mli`
are what buy it, one costing a parameter on every action and the other a file.
That is the one place the spikes are ahead of the target, and the ceremony is
the price of being ahead.

## To remove

- **`Creator`, and every trace of insert-against-update.** There is one kind of
  action. Whether it inserts, updates, stamps `deleted_at` or writes five tables
  is whatever `execute` does with its transaction, and nothing outside `execute`
  needs to know. `Creator` exists in the spikes only because a pure `execute`
  handed back an object and the store had to be told what to do with it; with the
  transaction in hand there is nothing to tell. The type goes, the eight groups
  collapse to one per object, and the frontends' group-by-group pairing goes with
  them — Rust's `Persist` tag and OCaml's `persist` closure both stop existing.

  The one split that stays is **whether an object is loaded before the action
  runs** — snacks' top-level actions against its object ones, the spikes' `root`
  group against the rest. That is about what availability is checked against, not
  about what gets written.

## Not true yet

- The TUI has no trash screen, so `restore` is CLI-only and nothing reports it.
- The form renders strings and enums only. The first integer or boolean fails.

Neither language is exhaustive over actions, so no type holds the second bullet
above. One test does: every registered key is reachable from every frontend.

## Done

`just verify` passes in both, and `just cli` and `just tui` drive the whole slice
through the erased path. Findings go in `README.md`.
