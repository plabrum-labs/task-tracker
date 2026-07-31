(** What an action is, with no transport in sight.

    Nothing in this file mentions Yojson, and nothing names a database. An action's payload is
    whatever type it says, its two hooks are pure functions of the object, and {!run} is the
    enforcement point. The connection {!run} hands to [execute] is an opaque ['conn]: this file
    threads it and never queries through it, so what an action writes is the action's business and
    the JSON edge below ({!Wire}) is the only thing that fixes ['conn] to a real {!Db.conn}.

    There is no third state and no vocabulary for one. The spec being copied has [is_available] and
    [is_disabled] and nothing else; a caller that wants to know what an object offers asks
    {!Wire.available}, which drops what is not available and hands back the disabled reason beside
    what is. *)

type ('obj, 'p, 'conn) t
(** One action over ['obj], taking a payload of type ['p] and, given a ['conn], writing whatever it
    wants and returning a message.

    [execute] holds the transaction, so an edit, a soft delete and an insert are all just what the
    body does with the connection — the type says nothing about which, because nothing outside
    [execute] needs to know. An earlier round handed a new object back for a store to persist, which
    needed a second type to keep an INSERT out of reach of an edit; with the connection in hand
    there is nothing to hand back and nothing to keep apart.

    Abstract, so [execute] is unreachable and {!run} is the only way to perform the write. That is
    what makes the enforcement below a guarantee rather than a convention. *)

module Defaults : sig
  (** The two hooks an action states only when it overrides them. Both are polymorphic, so
      [include Action.Defaults] sits at the top of a spec whatever object that spec goes on to name.
  *)

  val is_available : 'obj -> bool
  val is_disabled : 'obj -> string option
end

val make :
  key:string ->
  is_available:('obj -> bool) ->
  is_disabled:('obj -> string option) ->
  execute:('obj -> 'p -> 'conn -> (string, Error.t) result) ->
  ('obj, 'p, 'conn) t

val key : ('obj, 'p, 'conn) t -> string
val is_available : ('obj, 'p, 'conn) t -> 'obj -> bool
val is_disabled : ('obj, 'p, 'conn) t -> 'obj -> string option

val run : 'obj -> ('obj, 'p, 'conn) t -> 'p -> 'conn -> (string, Error.t) result
(** Both hooks are enforced here rather than at the edge, so a caller that already holds a payload
    cannot skip them and reach the write. Only once they pass is [execute] given the connection.
    {!Wire.dispatch} decodes and then comes through this, and the frontends run the whole thing
    inside one {!Db.transaction} so a refusal here rolls back anything the connection already saw.
*)
