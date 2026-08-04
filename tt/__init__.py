"""A small task tracker: an action framework over SQLite, a CLI and a TUI.

The tree mirrors the OCaml and Rust spikes beside it — ``platform`` / ``domains``
/ ``frontend``, each layer the only one that knows about the one below it:

- ``platform`` — the framework, with no persisted object in it. ``action`` is
  what an action is — typed, free of any transport — together with the small
  vocabulary a frontend drives it through: the refusals, the payload decoder, the
  form a payload derives and the offer an object makes. ``db`` opens a session,
  names no table, and carries the declarative base every table maps on; ``enums``
  is the two column adapters a domain stores an enum through.
- ``domains`` — one package per persisted object. Each ``issue`` and ``project``
  package holds its ``enums``, its ``models`` (the mapped table), its ``queries``,
  its ``schemas`` (the wire contract), its ``actions`` and one ``api`` facade.
  ``tt.schema`` brings a database up to the current schema, by Alembic or by
  ``create_all``.
- ``frontend`` — ``cli`` and ``tui``, both built from what an action advertises
  rather than from any action's name.
"""
