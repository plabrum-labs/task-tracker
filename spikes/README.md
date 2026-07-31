# spikes

Exploration only. Nothing here is wired into `backend/`, and neither spike is
covered by the root `justfile` or the git hooks.

Each spike has its own `justfile`. Run `just --list` in either directory; the
recipe names match across the two.

    cd spikes/ocaml && just verify     # or: just show / just cli … / just tui
    cd spikes/rust  && just verify     # or: just show / just cli … / just tui

The OCaml recipes source the opam switch themselves, so `dune` does not have to
be on the PATH first.

`ocaml/bin/show.ml` and `rust/examples/show.rs` print what the erased path looks
like from outside — what an object offers with each action's argument schema,
then a dispatch. `just show` in either.

Each spike is a small application rather than a harness: two persisted objects
over SQLite, soft deletes, list and detail reads, a CLI and a TUI, both built
only from what an action advertises. `just cli project ls`, `just tui`.

The TUIs are on [nottui](https://ocaml.org/p/nottui/latest) and
[ratatui](https://ratatui.rs) respectively: the menu is what `Wire.available`
returned, each form is what `Form.of_schema` derived from the advertised
schema, and submitting is a `Wire.dispatch`. They are there to find out what a
frontend built only from the schema can and cannot do (finding 10).

## What is being compared

An **action framework** over a domain object, in both languages, against the
same canonical design: snacks' `actions/base.py`. One declaration per action
carrying `action_key` and three hooks (`is_available`, `is_disabled -> reason |
None`, `execute`); defaults so an action states only what it overrides; an
action group that offers what applies to an object and dispatches into it; and
actions whose payload types differ sitting in one group.

Both spikes split that in two, and the split is load-bearing for most of what
follows:

- `action.ml` / `action.rs` — what an action *is*. No transport. A payload is
  whatever type the action says, availability is a pure function of the object,
  and `run` is where availability is enforced.
- `wire.ml` / `wire.rs` — the JSON edge. The payload type is erased here, and
  the key-to-decoder mapping lives next to the action so the CLI's idea of an
  action's arguments cannot drift from the action's own.

Two snacks behaviours are deliberately not reproduced. `trigger()` finds its
action by `_struct_to_action[type(data)]`, keying off the payload's runtime
type — the same defect as Go's `payload.(P)` cast. And `form_field_order` /
`form_field_labels` are ClassVars sitting apart from the Struct they describe,
so the advertised arguments and the decoder can drift.

### The slice

Two persisted objects, eleven actions and two creators, spread over eight
registration lists. Both spikes carry all of it, and the file sets correspond
one for one.

A **project** has a slug, a title, a body, a status and — from the read rather
than from a column — the counts of the issues under it. An **issue** belongs to
a project and has a title, a body, a status, a priority and a status note.

| object | action | payload | availability |
| --- | --- | --- | --- |
| issue | `editTitle` | `{ title }` | always; `execute` refuses a blank title |
| issue | `editBody` | `{ body }` | always; blank is how you clear it |
| issue | `editStatus` | `{ status, note? }` | always |
| issue | `editPriority` | `{ priority }` | always |
| issue | `delete` | `{}` | always |
| issue | `restore` | `{}` | always |
| project | `editTitle` | `{ title }` | always |
| project | `editBody` | `{ body }` | always |
| project | `editStatus` | `{ status }` | refused while anything is `doing` |
| project | `delete` | `{}` | refused while the project is active |
| project | `restore` | `{}` | refused once the slug is taken again |
| project | `addIssue` | `{ title, body?, priority? }` | creator; refused while archived |
| root | `createProject` | `{ slug, title?, body? }` | creator; `create` refuses a duplicate slug |

Three keys — `editTitle`, `editBody`, `editStatus` — are registered against
both objects with different payload types, which is what makes "resolve the
object before the key" a property worth asserting rather than an accident.

`delete` and `restore` are registered in their own groups because what
distinguishes them is not what they mean but what the store does with the
result: `group` is persisted by an `UPDATE` of the editable columns, `trash` by
one that stamps `deleted_at`, `deleted_group` by one that clears it. Soft
deletes are derived rather than cascaded — a live issue is one whose own
`deleted_at` and whose project's are both null — so deleting a project hides its
issues with one row written and restoring it brings back exactly the issues that
were not deleted in their own right.

Both frontends use the **erased** path: an agent driving `tt issue action <id>
<key> <json>` does exactly what a TUI user does — read the object, see what it
offers, pick one, supply arguments. A **typed** path sits alongside it for code
that names its action statically: `Action.run issue Issue_actions.Edit_title.action
payload`, `EditTitle::run(issue, payload)`.

## Findings

### 1. Deriving the schema from the payload type only works in Rust

The design rests on one type definition yielding both the decoder and the
argument schema, so the two cannot drift. That holds in Rust and does not hold
in OCaml.

`serde` and `schemars` share `serde_derive_internals`, so `schemars` reads the
`#[serde(…)]` attributes. `Option<T>` is left out of `required` and accepts
`null`; `deny_unknown_fields` becomes `additionalProperties: false`. One
attribute, both derivers.

`ppx_yojson_conv` and `ppx_deriving_jsonschema` are two independent ppxes that
do not read each other. An optional field needs `[@yojson.option]` **and**
`[@jsonschema.option]`; write only the first and the schema advertises the
field as required while the decoder is happy without it. Nothing reports the
disagreement.

Writing both still does not make them agree. The schema says
`"type": ["string", "null"]`; the decoder rejects an explicit `null`, because
`[@yojson.option]` means absent-or-a-string. `test_tt.ml` asserts both halves of
that so the divergence is recorded rather than discovered later.

This is the result that has survived every restructuring, and it is the one
that matters.

**It pays off again at application scale, and this is where it becomes a file.**
Two enums with wire names are all it takes. OCaml needs `lib/enum.ml`, a
hand-written functor over a table of `(constructor, wire name)` pairs, because
deriving a variant with `[@@deriving yojson, jsonschema]` yields `"Todo"` from
both — the constructor name, not the wire name — and `~variant_as_string` fixes
the schema while breaking the decoder. One table drives the encoder, the decoder
and the schema, and *nothing checks that the table covers the type*: a
constructor left out of `values` compiles clean and `to_string` raises on it.

Rust needs `#[serde(rename_all = "snake_case")]` and no file at all, and the
measurement is that `rust/src/` has no counterpart to `enum.ml`.

Adding a variant shows the difference. In OCaml it compiles clean and
`Status.to_string` raises `invalid_arg` the first time anything hits it. In Rust
the `DeriveActiveEnum` attribute and the `match` in `store::counts` are both
compile errors — but the `CHECK` constraint in the raw DDL is a string and is
not, so the new variant reaches the database and is rejected there. Neither
language catches it everywhere; Rust catches it in two places out of three and
OCaml in none.

### 1b. …and sea-orm relocates the drift rather than removing it

The residue is at the SQL edge. `DeriveActiveEnum` wants its own `string_value`
literals:

```rust
#[serde(rename_all = "snake_case")]
#[sea_orm(rs_type = "String", db_type = "Text")]
pub enum Status {
    #[sea_orm(string_value = "todo")]
    Todo,
    …
}
```

Two tables of strings on one type, related by nothing. Write
`string_value = "Todo"` and both derives are satisfied while the column and the
wire disagree; the `CHECK` constraint then rejects the write at run time with a
message about the column. This is the same class of drift as OCaml's ppx pair,
moved rather than removed, and only the round-trip assertion in
`tests/store.rs` catches it.

OCaml pays the same toll in the same place and calls it `Issue.Priority.to_int`
/ `of_int` — but it writes that pair as a `match`, which *is* exhaustive over
the type, where `string_value` is an attribute and is not. So on this one
column OCaml's second table is the safer of the two.

### 2. Both derive descriptions; only Rust derives the type name

Measured again for this round, and most of this finding did not survive it.

`schemars` carries doc comments into `description` and the type name into
`title`:

```json
{ "type": "object", "additionalProperties": false,
  "properties": { "title": { "type": "string",
                             "description": "What to call the issue." } },
  "required": ["title"], "title": "EditTitlePayload" }
```

`ppx_deriving_jsonschema` 0.0.7 does the first of those. A field takes
`[@jsonschema.description "What to call the issue."]`, and the deriver's
`~ocaml_doc` flag reads `(** *)` comments as the same thing, so the description
sits *on the field it describes*:

```ocaml
type t = { title : string  (** What to call the issue. *) }
[@@deriving yojson, jsonschema ~ocaml_doc]
```

Both forms emit `"description": "What to call the issue."`. There is more
beside them — `[@jsonschema.attrs { minimum; maximum }]`, `format`, `default`.
What is still absent is the type name: nothing puts `"title":
"EditTitlePayload"` in an OCaml schema.

So the claim this finding used to rest on — that descriptions on the OCaml side
mean a second structure beside the type, which is `form_field_labels` coming
back — was simply wrong, and it was the sharper half. An attribute on a field
is not a second structure, and a doc comment on a field is not one either.

The payload types in `ocaml/lib/` carry no doc comments, so the schemas this
spike emits are still bare and the OCaml `--help` and form below read that way.
That is now a decision this spike has not taken rather than a limit of the
library: one deriver flag and one comment per field.

Two further differences show up once the schemas are more than one string
field, and they cut in opposite directions:

- **Property order.** `ppx_deriving_jsonschema` emits properties in the
  *reverse* of the order the type declares them, and there is nothing in the
  schema to sort by instead — so an OCaml `--help` and an OCaml form read
  bottom-up through the type they came from. `schemars` emits declaration
  order, and Rust keeps it only because of finding 14.
- **Enums.** OCaml inlines the enum at the property:
  `{"type": "string", "enum": ["todo", "doing", "done"]}`. `schemars` emits
  `{"$ref": "#/$defs/Status"}` and puts the values in `$defs`, so
  `form::of_schema` has to resolve one level of `$ref` before it sees anything
  it can render. The OCaml shape is the friendlier one for a naive reader.

One hazard comes with the descriptions. `schemars` puts a *type's* doc comment
in the schema too, and cannot tell one written for a maintainer from one written
for a caller — so a paragraph of design rationale on `enum Status` is shipped to
every agent that reads `tt issue show`. The fix is to write that prose as `//`
rather than `///`, which the payload types here now do.

### 3. The `wire` split closed the gap that was supposed to be Rust's headline

Earlier rounds had OCaml writing `~schema` and `~decode` on every action while
Rust derived both from a `type Payload` bound. That gap is gone, and moving the
transport out is what removed it. Neither language's *action* mentions a
decoder or a schema now. Both pair the two exactly once, at registration:

```ocaml
Wire.entry Edit_title.action (module Edit_title.Payload)
```
```rust
Entry::of::<EditTitle>()
```

In both, the pairing is checked. `Wire.entry Edit_title.action (module
Edit_body.Payload)` is a type error, so the schema an action advertises cannot
belong to a different type than the one it decodes.

The remaining asymmetry is small and in Rust's favour: `Entry::of::<EditTitle>()`
names the action once, where OCaml names it and its payload module separately.

### 4. Optional arguments reproduce a base class; nesting the payload is what makes it read

`Action.make`'s defaults are optional arguments with a trailing `()`:

```ocaml
let make ?(is_available = fun _ -> true) ?(is_disabled = fun _ -> None) ~key ~execute () = …
```

`Edit_title` passes two arguments and says nothing about either availability
hook, which is what the Rust `impl` does by leaving the default methods alone.
The `()` is load-bearing: without a final positional argument the optional ones
cannot be erased.

One action is one module, holding its payload type and the action over it. The
payload module is **nested and not opened**, which is what keeps the object's
fields visible in `execute`. Opening it instead puts the payload's fields in the
same scope as the object's, and `Edit_title`'s payload has a `title` field, so:

```ocaml
~execute:(fun issue p -> … Ok { issue with title })
```

infers `issue` as the *payload* type. The definition typechecks; the error
surfaces later at the registration site, pointing at the wrong line. OCaml's
type-directed record disambiguation silently picked the nearer type, and only a
downstream annotation caught it.

`(p : Payload.t)` on `execute` is still required: nothing else pins `'p`, since
the payload module is no longer an argument to `make`. That is one annotation
per action, against Rust's `type Payload = EditTitlePayload`.

At application scale this hazard turned up twice more on the OCaml side and
never on the Rust one, because it is a hazard about *field names in scope*
rather than about actions: `Project.t` and `Project.draft` share `slug`,
`title` and `body`, and `Issue.t` and `Issue.draft` share `title`, `body` and
`priority`, so `store.ml` and `project.ml` both carry annotations that exist
only to stop disambiguation choosing the later declaration. Rust's struct
literals name their type.

### 5. Exhaustiveness: neither language names any site

Add a third action and see who is told. **Neither compiler says anything.**
Once actions are separate declarations rather than variants of one type, nothing
in either language is exhaustive over them, and registration is an explicit list
in both — `group` and `Group<Issue>` — checked in neither. An action that is
declared but never registered compiles clean in both and is absent from every
action group.

Earlier rounds had OCaml ahead here on the strength of a GADT whose constructors
made `Domain.name` and `Domain.handler` non-exhaustive. That design is gone, and
its advantage went with it. This is the cost of "one action = one self-contained
declaration", and both languages pay it in full.

Registration being explicit is still a win over snacks, where a decorator runs
on import and an unimported module is a missing action with no list to read.

### 6. The payload is erased, never cast

The part with no Go or Python equivalent, and it works in both.

OCaml's `Wire.entry` closes over the payload type in the `run` closure. The type
is universally quantified in `entry`'s signature and simply does not appear in
`'obj entry` — a closure, not an existential type, and nothing to open.

Rust's `Entry` holds two plain `fn` pointers. `A::availability` and the
monomorphised `decode_and_run::<A>` coerce to `fn` once `A` is known, so there is
no `Box<dyn …>`, no object safety to work around, no `PhantomData`, and nothing
to downcast.

Neither has Go's `payload.(P)` or snacks' `_struct_to_action[type(data)]`.

### 7. Both languages can make the enforcement point a guarantee, and neither gets it free

Both `action` files carry the same comment on `run`: *availability is enforced
here rather than at the edge, so a caller that already holds a payload cannot
skip it.* Written the obvious way, that comment is false in both languages —
`EditTitle::execute(issue, payload)` and
`Issue_actions.Edit_title.action.execute issue payload` each compile, and each
performs the write against an object that refused it.

Each language can close it, and they pay in different places.

OCaml closes it with a file. `action.mli` makes `('obj, 'p) t` abstract and
exports `make`, `key`, `availability` and `run`; `execute` is then a field
nobody outside `action.ml` can name, and the bypass is `Error: Unbound record
field execute`. The cost is the interface itself — every exported value's type
written a second time, by hand.

Rust cannot do it by sealing the trait, which would only shut out other crates.
It does it with a token whose constructor is private to `action.rs`:

```rust
pub struct Checked(());

fn execute(obj: Self::Obj, payload: Self::Payload, _: Checked) -> Result<Self::Obj, Error>;
```

The bypass is then `E0061: this function takes 3 arguments but 2 were
supplied`, and forging the token from outside is `E0423: cannot initialize a
tuple struct which contains private fields`. The cost is one parameter on every
`execute`, plus the import.

The two costs scale differently. Rust's is paid again by every action added;
OCaml's is paid once, and the same file buys the rest of the module's
encapsulation.

**A second module needed sealing, and that is where the comparison turns.** The
store has to guarantee that no read can forget its soft-delete predicate, which
means the tables must not be nameable from outside. OCaml writes `store.mli` —
another 88 lines, of which 23 are code and the rest are the doc comments that
are the only reason to keep it. Rust writes `mod entities;` without a `pub` and
is finished: the entity types, the raw DDL and the row structs are private,
`store.rs` exports the loads and the writes, and there is no second copy of any
signature to maintain.

So finding 7's cost is not symmetric after all. Rust pays per *action*; OCaml
pays per *module that needs sealing*, and an application has more of those than
a harness does.

### 8. Both can type "an action group never contains Absent", and Rust's is tidier

`available` drops absent actions, so what it returns can only be `Refused` or
`Runnable`. Both languages can say that in the type.

OCaml says it with polymorphic-variant subtyping:

```ocaml
type offered = [ `Refused of string | `Runnable ]
type availability = [ `Absent | offered ]
```

and `available` re-tags into the narrower type with `#Action.offered as
offered`.

Rust names the narrow type and reuses `Option` for the wide one:

```rust
pub enum Offered { Refused(Cow<'static, str>), Runnable }

/// `None` is "does not apply to this object at all".
pub type Availability = Option<Offered>;
```

which turns `available` into the shape `?` exists for, with no re-tagging at
all:

```rust
.filter_map(|entry| Some((entry, (entry.availability)(obj)?)))
```

The reason is a `Cow` rather than the `&'static str` earlier rounds used: a
refusal is allowed to quote the object it read — *"finish or drop 3 issues
first"* — and the 3 is per-project. Every refusal that does not need that stays
a borrowed literal, so the widening costs an `.into()` at four call sites and
nothing at run time. OCaml's `` `Refused of string `` always allocated.

Either way the caller loses a case it can no longer write, and under
`-warn-error +8` (finding 9) a two-case match that compiles is a proof that the
third cannot arrive. OCaml's version carries the usual polymorphic-variant
hazard, a mistyped tag quietly widening a type, contained here only because
`action.mli` annotates every producer. `Offered` is nominal, so Rust has nothing
equivalent to contain.

### 9. Warning 8 is still not fatal under `--profile release`

Carried forward, and still the reason every `dune` file that compiles OCaml
here sets `-warn-error +8`. Without it a release build compiles a non-exhaustive
match and raises `Match_failure` at runtime. Finding 5 was measured under
`--profile release`, so the flag is doing the work. Rust's `E0004` is a hard
error in every profile.

### 10. A frontend built from the schema follows the schema — in Rust

Neither TUI names an action key. The menu is `Wire.available`, each form is
`Form.of_schema` over the advertised schema, and submitting is `Wire.dispatch`
— so adding a fifth issue action changes no line of either. That much works in
both, and it is the payoff for erasing the payload type in one place.

The OCaml build turned findings 1 and 2 from things a test records into things
the frontend has to work around. **The Rust build is the control, and it clears
the idea.**

**The form cannot send what the schema advertises — in OCaml.** `editStatus`'s
schema says `note` is `["string", "null"]`. A form written from that would send
`null` for a field left blank, and the decoder rejects it: `[@yojson.option]`
means absent-or-a-string. The only encoding both halves accept is to omit the
field, which is what `Form.payload` does and why it carries a comment saying so.
In Rust, `serde` and `schemars` read one attribute, so `form::payload` sends the
`null` the schema offered and the decoder takes it. `tests/frontend.rs` asserts
exactly that, against the same field.

**The OCaml form labels its fields with property names, and did not have to.**
The prompt beside each input is the property name — the screen reads `title*`
and nothing else — because these payload types carry no descriptions, not
because the schema could not hold one. The Rust form reads
`title* []  What to call the issue.` and its CLI prints the same string in
`--help`. The difference on screen is real; per finding 2 the reason for it is a
decision rather than a limit, so this is the one place where the two spikes are
not measuring the same thing.

So what failed was the ppx pair, not "a frontend built only from the schema" —
and it failed on the encoding, which is silent, rather than on the labels, which
are visible and recoverable.

One thing does come from the libraries, and it is the same in both: the view is
a value, not a session. `View.root` returns a `ui` and `tui::render` draws into
a `TestBackend` buffer, so both `test_frontend.ml` and `tests/frontend.rs` drive
the whole frontend by keystroke and assert against the rendered frame, with no
pty, no sleeps and no mocks.

### 11. The two frontends could not have the same shape, and the seam is the finding

nottui is incremental — the state is a set of `Lwd.var`s and the view recomputes
— and ratatui is immediate-mode. That much is a library difference and not
interesting. What is interesting is where the database ended up.

OCaml's key handler calls `Wire.dispatch` and the store inline, because its state
holds the connection and Lwt is ambient; `view.ml` runs the promise to completion
with `Lwt_main.run` inside a helper and the rest of the file is synchronous.
Rust cannot: the store is `async`, so an inline key handler would have to be
`async` too, and an async key handler is not testable without a runtime driving
it.

So `tui.rs` is three functions instead of one —
`render(&State, &mut Frame)` and `on_key(&State, KeyCode) -> Intent` are pure,
`apply(&Db, State, Intent) -> State` is where the database is — and `Intent` is
the seam. It is more machinery than OCaml needs and it is also the more
assertable design: `tests/frontend.rs` asserts what a key *means* separately
from what it *does*, and the OCaml tests cannot separate the two.

The same split shows up in the rows. An OCaml row closes over a `persist`
function; a Rust row carries a `Persist` tag matched exhaustively in one place,
because a closure returning a future would have to be boxed. The tag is the
worse ergonomics and the better check.

### 12. sea-orm has petrol's schema gaps, and none of its migration ones

`Schema::create_table_from_entity` emits, for the projects table:

```sql
CREATE TABLE "projects" ( "id" integer NOT NULL PRIMARY KEY AUTOINCREMENT,
  "slug" varchar NOT NULL, … "deleted_at" varchar )
```

No `STRICT`, no `CHECK`, no index API at all, `varchar` for a `String`, and the
`ON DELETE CASCADE` on the issues foreign key silently dropped. That is petrol's
list almost exactly.

Two things make it a smaller problem than petrol's.

Dropping to raw DDL is ordinary rather than an escape hatch:
`db.execute_unprepared` takes a string, so `store.rs` declares the *whole*
schema as SQL and the entity structs declare only the columns, with a test
reading `sqlite_master` to assert the two describe one table. The OCaml side
cannot do that — petrol's `VersionedSchema` owns `CREATE TABLE`, so the schema
is necessarily half petrol's and half raw.

And there is no migration path that silently does not run. Petrol's
`VersionedSchema.initialise` runs migrations only when the stored version is
behind the declared one, so on a fresh database — every test, and the app's
first run — it creates the tables and runs nothing, which is why `extra_ddl`
sits outside the version check with `IF NOT EXISTS` on everything.

The measurement is what each database actually holds. `sqlite3 tt.db .schema`
against the Rust one shows `STRICT` on both tables, three `CHECK` constraints
and the partial unique index; against the OCaml one it shows what petrol could
emit. Two of the plan's assertions are Rust-only for that reason: that the
emitted schema is the designed one, and that a duplicate live slug is refused by
the index even when `createProject`'s hook is bypassed.

### 13. Two petrol gaps simply are not there

- **Multi-column `ORDER BY` with mixed directions composes.**
  `.order_by_desc(priority).order_by_asc(created_at)` builds
  `ORDER BY "priority" DESC, "created_at" ASC`. Petrol's `order_by` takes one
  direction for the whole clause and a second call overwrites the first, so the
  OCaml side moves the rest of the order out of SQL into a pure
  `Issue.pick_order` and applies it in the store so neither frontend has to
  remember to. There is no `pick_order` in `rust/src/`, and its absence is the
  measurement.
- **An insert returns the row it wrote.** `ActiveModel::insert(db).await`
  yields a `Model` carrying the assigned id. Petrol has neither `RETURNING` nor
  `last_insert_rowid`, so the OCaml side reaches for a raw Caqti request to ask
  what id it just used.

Rust still re-reads the row after an insert, but for a different reason: a
`Project` carries counts and an `Issue` carries its project's slug, and neither
is on the row that was written. That is a projection cost, not an id one.

### 14. A Rust-only hazard: `serde_json`'s `Map` is sorted by default

Without the `preserve_order` feature, `serde_json::Map` is a `BTreeMap`, so the
`properties` of a derived schema come back alphabetically and `form::of_schema`
renders fields in an order the payload type never declared. `addIssue` would ask
for body, priority, title.

Yojson's `Assoc` is an association *list* and preserves whatever order the
deriver emitted, so OCaml has no equivalent hazard — it has finding 2's
reverse-order problem instead, which is at least stable and visible. The Rust
one is silent, is fixed by one word in `Cargo.toml`, and is asserted in
`tests/frontend.rs` so that removing the word fails a test rather than reordering
a form.

### 15. Nominal traits keep `Action` and `Creator` apart for free

A creator produces a child that does not exist yet, so its `create` returns a
different type from what it was offered against. At `Parent = Child` it is
structurally identical to an action — and the store `INSERT`s the result of one
and `UPDATE`s the result of the other, with no field of either saying which. A
single type covering both would make the store's dispatch unsound.

Rust's traits are nominal, so `Action` and `Creator` are distinct because they
are written distinctly, and nothing at a use site has to remember it. OCaml's
records are nominal too, but `('obj, 'p) t` and `('parent, 'p, 'child) creator`
have to be kept apart by discipline in the interface and by prefixing every
field name (`creator_key`, `creator_is_available`) because a structure cannot
define one label twice.

Rust pays for it in duplicated default-method bodies: `availability` and `run`
are the same three lines on both traits. Factoring `decide` and `enforce` out as
private functions in `action.rs` reduces the duplication to two signatures,
which is the honest cost.

What neither language decides is the pairing of a group with a store call. A
soft delete is an update, so `delete_project` and `update_project` have the same
type, and the four registration lists exist only so that pairing is written once
per frontend rather than once per row.

### 16. `Deleted<T>` is one generic type where OCaml writes two records

Registering `restore` against the deleted type rather than the live one is what
makes "a deleted row cannot be edited" a fact about what compiles. OCaml writes
`Issue.deleted` and `Project.deleted` as two records with the same shape; Rust
writes `Deleted<T>` once, and `Group<Deleted<Issue>>` and `Group<Deleted<Project>>`
are distinct types for free.

The saving is five lines and it is real, but it is the smallest of the
structural differences here — worth recording mostly because it is the only
place in the whole comparison where Rust's parametric polymorphism buys anything
OCaml's does not. OCaml could write `'a deleted = { inner : 'a; deleted_at : string }`
just as easily; it does not, and the reason is that field-name disambiguation
(finding 4) makes a shared record type more awkward there, not that the type
system cannot express it.

### 17. A CRUD-shaped action set does not need the availability layer

The same result on both sides, and it is worth stating plainly because it cuts
against the design being compared.

All four issue edits are always `Runnable`. There is no WIP rule — many issues
may be `doing` at once — and nothing else about an issue constrains what may be
done to it, so `is_available` and `is_disabled` are the defaults in every case
and every refusal an issue makes comes from `execute`. Availability earns its
keep in exactly four places out of thirteen, and all four are on the project:
`editStatus`, `delete` and `restore` refuse on counts or on status, and
`addIssue` refuses on an archived parent.

The pattern is that availability pays where an action is a verb with a
precondition, and not where it is a field assignment. A tracker with `assign`,
`block`, `merge` or `close` would look different; this one mostly does not.

Two of those four refusals also show what a hook costs. A hook is a pure
function of *its object*, so anything it reads has to be on the object — which
is why `Project` carries its issue counts in every projection, and why `restore`
is offered against a `{ deleted, live }` pair rather than against the trash row
alone. Without the live list, restoring a project whose slug had been taken
again would surface as a `UNIQUE` constraint violation with no sentence in it.

## Size

Code lines, excluding blanks and comments; totals in brackets. Both trees are
formatted by their standard formatter, and rustfmt breaks expressions across
more lines than ocamlformat does — so Rust's column runs high by something like
a fifth for reasons that are not about either language.

    ocaml/lib/action.ml         44 [ 57]   rust/src/action.rs          69 [138]
    ocaml/lib/action.mli        24 [ 68]
    ocaml/lib/wire.ml           84 [147]   rust/src/wire.rs           111 [190]
    ─────────────────────────────────────  ─────────────────────────────────────
    framework                  152         framework                  180

    ocaml/lib/enum.ml           42 [ 80]   —                            0
    ocaml/lib/issue.ml          39 [ 86]   rust/src/issue.rs           70 [115]
    ocaml/lib/project.ml        26 [ 59]   rust/src/project.rs         56 [ 88]
    —                            0         rust/src/deleted.rs          5 [ 13]
    ocaml/lib/clock.ml           4 [ 12]   rust/src/clock.rs            4 [ 13]
    ocaml/lib/error.ml           8 [ 14]   rust/src/error.rs           23 [ 37]
    ─────────────────────────────────────  ─────────────────────────────────────
    domain                     119         domain                     158

    ocaml/lib/issue_actions.ml  63 [105]   rust/src/issue_actions.rs  113 [187]
    ocaml/lib/project_actions.ml
                               124 [174]   rust/src/project_actions.rs
                                                                      193 [277]
    ─────────────────────────────────────  ─────────────────────────────────────
    actions                    187         actions                    306

    ocaml/lib/store.ml         317 [505]   rust/src/store.rs          481 [649]
    ocaml/lib/store.mli         23 [ 88]
    ─────────────────────────────────────  ─────────────────────────────────────
    store                      340         store                      481

    ocaml/lib/form.ml           40 [ 93]   rust/src/form.rs            88 [170]
    ocaml/cli/command.ml       338 [476]   rust/src/cli.rs            504 [650]
    ocaml/bin/cli.ml            10 [ 22]   rust/src/bin/cli.rs         24 [ 39]
    ocaml/tui/view.ml          319 [432]   rust/src/tui.rs            526 [646]
    ocaml/bin/tui.ml            12 [ 19]   rust/src/bin/tui.rs         31 [ 46]
    ─────────────────────────────────────  ─────────────────────────────────────
    frontends                  719         frontends                 1173

    ocaml/test/test_tt.ml      348 [433]   rust/tests/actions.rs      582 [672]
    ocaml/test/test_store.ml   142 [184]   rust/tests/store.rs        284 [365]
    ocaml/test/test_frontend.ml
                               308 [407]   rust/tests/frontend.rs     528 [619]
    ─────────────────────────────────────  ─────────────────────────────────────
    tests                      798         tests                     1394

Two rows carry an argument rather than a measurement. `enum.ml` is 42 lines that
exist only because two ppxes disagree, against nothing at all in Rust — the
clearest single number in the table. And the two `.mli` files are 47 lines of
code and 156 lines total, of which the doc comments are the part worth keeping;
Rust buys the same encapsulation with `mod entities;` and one private tuple
field.

Dependencies moved too. `rust/Cargo.toml` went from three crates to nine —
sea-orm, tokio, clap, ratatui, crossterm and chrono are all new, and `sqlx`
bundles SQLite so a fresh checkout builds without `brew install sqlite`. The
OCaml side took petrol, caqti, cmdliner, nottui, notty and lwt for the same
reasons. Neither tree's domain core names any of it.

## Where this leaves the choice

The stated criterion is avoiding bugs through simplicity rather than testing.

Against OCaml, finding 1 is unchanged and remains decisive, and building an
application on top of it made it worse rather than better: it grew a file —
`enum.ml`, a functor whose own table nothing checks against the type it
describes — where Rust grew an attribute.

Finding 2 mostly went away when it was measured again.
`ppx_deriving_jsonschema` does carry descriptions, on the field, so the drift
this finding predicted does not exist; what is left is the missing type name and
the fact that this spike never took the flag up. It was one of the two results
the earlier rounds called decisive, and it is not one any more.

Finding 10 went Rust's way on the half that counts. The Rust frontend sends the
`null` its schema advertises and the OCaml one cannot — that is finding 1 with a
form attached, and it is silent. The labels are the smaller matter the earlier
round made too much of: OCaml can carry them and this spike does not.

Finding 7 also stopped being symmetric. Rust pays `Checked` once per action and
OCaml pays an `.mli` once per module that needs sealing — and the store needed
sealing, for the soft-delete predicate, at a cost of 88 more lines against
Rust's one missing `pub`.

What OCaml gained is smaller and real. Its schemas inline their enums where
Rust's hide them behind `$ref`. Its `Priority.to_int` is a `match` and therefore
exhaustive, where sea-orm's `string_value` is an attribute and is not. Its
frontend can call the store inline where Rust needs an `Intent` seam to keep the
key handler testable — though that seam is the better design, so the win is
brevity rather than correctness. And findings 3, 5 and 6 remain dead level.

So the two are still close on everything the type systems decide, and the gap
has narrowed to finding 1 alone — two ppxes reading one type definition and
agreeing with each other on the wrong thing. It is silent, it is the class of
bug this design exists to prevent, and it is OCaml's. Finding 2, which stood
beside it for every earlier round, does not survive re-measurement. The
application-scale evidence did not change the answer; it made the same answer
rest on one result instead of two.
