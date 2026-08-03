"""A small task tracker: an action framework over SQLite, a CLI and a TUI.

The tree mirrors the OCaml and Rust spikes beside it — ``platform`` / ``domains``
/ ``frontend``, each layer the only one that knows about the one below it:

- ``platform`` — the framework, with no persisted object in it. ``action`` is
  what an action is, typed and free of any transport; ``wire`` is the JSON edge
  both frontends talk to; ``form`` is the form built from a schema; ``db`` runs a
  query and names no table; ``error``, ``clock`` and ``deleted`` are the small
  shared types.
- ``domains`` — one package per persisted object. ``schema`` names the base
  tables; each ``issue`` and ``project`` package holds its object, its queries
  (``services``) and its actions.
- ``frontend`` — ``cli`` and ``tui``, both built from what an action advertises
  rather than from any action's name.
"""
