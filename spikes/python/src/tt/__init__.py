"""A small task tracker: an action framework over SQLite, a CLI and a TUI.

The tree mirrors the OCaml and Rust spikes beside it — ``platform`` / ``domains``
/ ``frontend``, each layer the only one that knows about the one below it:

- ``platform`` — the framework, with no persisted object in it. ``action`` is
  what an action is, typed and free of any transport; ``wire`` is the JSON edge
  both frontends talk to; ``form`` is the form built from a schema; ``models`` is
  the declarative base every table maps on; ``db`` opens a session and names no
  table; ``error``, ``types`` and ``deleted`` are the small shared pieces.
- ``domains`` — one package per persisted object. Each ``issue`` and ``project``
  package holds its ``models`` (the mapped table and its types), its ``queries``,
  its ``services`` and its ``actions``. ``tt.schema`` brings a database up to the
  current schema, by Alembic or by ``create_all``.
- ``frontend`` — ``cli`` and ``tui``, both built from what an action advertises
  rather than from any action's name.
"""
