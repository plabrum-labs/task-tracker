(** What an action is, with no transport in sight.

    Nothing in this file mentions Yojson. An action's payload is whatever type it says, its two
    hooks are pure functions of the object, and {!run} is the enforcement point. The JSON edge both
    frontends talk to is {!Wire}, built on top of this.

    There is no third state and no vocabulary for one. The spec being copied has [is_available] and
    [is_disabled] and nothing else; a caller that wants to know what an object offers asks
    {!Wire.available}, which drops what is not available and hands back the disabled reason beside
    what is. *)

type ('obj, 'p, 'out) t
(** One action over ['obj], taking a payload of type ['p] and producing an ['out].

    ['out] is ['obj] for an edit and something else for a creator: [addIssue] is offered against a
    project and returns an {!Issue.draft}. One type covers both because the group an action is
    registered in says how what it returned is written — see {!Wire.group} — where an earlier round
    needed two nominally distinct types to keep an INSERT out of reach of an edit.

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
  execute:('obj -> 'p -> ('out, Error.t) result) ->
  ('obj, 'p, 'out) t

val key : ('obj, 'p, 'out) t -> string
val is_available : ('obj, 'p, 'out) t -> 'obj -> bool
val is_disabled : ('obj, 'p, 'out) t -> 'obj -> string option

val run : 'obj -> ('obj, 'p, 'out) t -> 'p -> ('out, Error.t) result
(** Both hooks are enforced here rather than at the edge, so a caller that already holds a payload
    cannot skip them. {!Wire.dispatch} decodes and then comes through this. *)
