(** The JSON edge. Everything that knows about a transport format is here.

    An {!Action.t} is typed and knows nothing about JSON. {!Make} declares one action in one functor
    application — its key, its hooks, its payload type, and from that type both the decoder and the
    advertised schema — and erases the payload into a closure. That is what lets actions with
    different payloads sit in one group, and is the whole of what a TUI user picking an action and
    an agent driving the CLI both need.

    The key-to-payload-type mapping stays here, next to the action. It cannot move out to a
    frontend: a caller holding the string ["editTitle"] and a blob has to pick the decoder by key,
    and doing that anywhere else would let a frontend's idea of an action's arguments drift from the
    action's own. *)

(** {1 Payloads} *)

(* [ppx_yojson_conv] decoders signal by exception; every action's arrives here. *)
let decode (of_yojson : Yojson.Safe.t -> 'p) (json : Yojson.Safe.t) : ('p, Error.t) result =
  match of_yojson json with
  | payload -> Ok payload
  | exception Ppx_yojson_conv_lib.Yojson_conv.Of_yojson_error (Failure message, _) ->
      Error (Error.Invalid message)
  | exception Ppx_yojson_conv_lib.Yojson_conv.Of_yojson_error (exn, _) ->
      Error (Error.Invalid (Printexc.to_string exn))

(** The arguments of an action that has none, for a spec to include.

    [type payload = unit [@@deriving yojson, jsonschema]] does not do it. The pair agrees with
    itself and on the wrong thing: the schema is [{"type":"null"}] and the decoder accepts [null]
    and refuses [{}], which is the opposite of what an action with no arguments is sent. OCaml has
    no empty record to derive from instead, so this is the hand-written pair — snacks'
    [EmptyActionData], which is one line there. *)
module No_payload = struct
  type payload = unit

  let payload_of_yojson = function
    | `Assoc [] -> ()
    | json -> Ppx_yojson_conv_lib.Yojson_conv.of_yojson_error "expected no arguments" json

  let payload_jsonschema : Yojson.Safe.t =
    `Assoc
      [
        ("type", `String "object");
        ("properties", `Assoc []);
        ("required", `List []);
        ("additionalProperties", `Bool false);
      ]
end

(** {1 Closed sets of values}

    A variant with wire names cannot be derived. [@@deriving yojson, jsonschema] on one yields
    ["Todo"] from both the decoder and the schema — the constructor name, not the wire name — and
    [~variant_as_string] fixes the schema while, per the library's own attribute documentation,
    breaking the decoder. So an enum states its names in a [to_string] and an [of_string] written as
    matches, and these two turn that into JSON. A match is exhaustive over the type, which is the
    thing a table of pairs could never be. *)

let enum_of_yojson ~name ~of_string ~names json =
  match json with
  | `String wire -> (
      match of_string wire with
      | Some value -> value
      | None ->
          Ppx_yojson_conv_lib.Yojson_conv.of_yojson_error
            (Printf.sprintf "%s must be one of %s" name (String.concat ", " names))
            json)
  | _ ->
      Ppx_yojson_conv_lib.Yojson_conv.of_yojson_error
        (Printf.sprintf "%s must be a string" name)
        json

let enum_jsonschema ~names : Yojson.Safe.t =
  `Assoc [ ("type", `String "string"); ("enum", `List (List.map (fun n -> `String n) names)) ]

(** {1 Declaring an action} *)

(** One action, stated once.

    Everything an action is, in one structure: the object it applies to, what it produces, its
    payload type, its key, its two hooks and its write. The two JSON values are what
    [[@@deriving yojson, jsonschema]] puts beside the payload type, so a spec names its payload once
    and cannot advertise a schema belonging to a different type than the one it decodes. *)
module type SPEC = sig
  type obj
  type out
  type payload

  val key : string
  val is_available : obj -> bool
  val is_disabled : obj -> string option
  val execute : obj -> payload -> (out, Error.t) result
  val payload_of_yojson : Yojson.Safe.t -> payload
  val payload_jsonschema : Yojson.Safe.t
end

type ('obj, 'out) entry = {
  key : string;
  schema : Yojson.Safe.t;  (** the arguments, as the JSON Schema an agent consumes *)
  is_available : 'obj -> bool;
  is_disabled : 'obj -> string option;
  run : 'obj -> Yojson.Safe.t -> ('out, Error.t) result;
}
(** One action with its payload type erased.

    The type is universally quantified in {!Make} and captured by the [run] closure, so it does not
    appear here. That is the erasure, and it is a closure rather than an existential type — there is
    nothing to open. *)

module type ACTION = sig
  type obj
  type out
  type payload

  val action : (obj, payload, out) Action.t
  (** The typed path, for code that names its action statically. *)

  val entry : (obj, out) entry
  (** The erased path, for a registration list. *)
end

module Make (S : SPEC) :
  ACTION with type obj = S.obj and type out = S.out and type payload = S.payload = struct
  type obj = S.obj
  type out = S.out
  type payload = S.payload

  let action =
    Action.make ~key:S.key ~is_available:S.is_available ~is_disabled:S.is_disabled
      ~execute:S.execute

  let entry =
    {
      key = S.key;
      schema = S.payload_jsonschema;
      is_available = S.is_available;
      is_disabled = S.is_disabled;
      run = (fun obj json -> Result.bind (decode S.payload_of_yojson json) (Action.run obj action));
    }
end

(** {1 Groups} *)

type ('obj, 'out, 'conn) group = {
  entries : ('obj, 'out) entry list;
  persist : 'conn -> 'out -> (string, Error.t) result;
}
(** The actions one kind of object offers, and how what they return is written.

    Registration is a value of this type. Carrying the write is what lets a creator be an ordinary
    action: an INSERT and an UPDATE are the same shape, and it is the group rather than the type of
    the result that says which. The pairing is stated once, where the actions are registered, rather
    than once per frontend per row.

    The connection is a type parameter because this file knows about JSON and nothing else; {!Store}
    is downstream of it. *)

(** What the object offers, in registration order.

    An action that does not apply is dropped and a refused one is kept with its reason, so a caller
    is told both what it can do and what it could do but for a reason. [None] is runnable. *)
let available (obj : 'obj) (group : ('obj, 'out, 'conn) group) :
    (('obj, 'out) entry * string option) list =
  List.filter_map
    (fun entry -> if entry.is_available obj then Some (entry, entry.is_disabled obj) else None)
    group.entries

let holds key (group : ('obj, 'out, 'conn) group) =
  List.exists (fun entry -> entry.key = key) group.entries

(** The one entry point for a caller holding a key and a blob. The hooks are not checked here —
    {!Action.run}, which {!Make} wired in, checks them against the live object, so what a frontend
    rendered stays a snapshot. *)
let dispatch (obj : 'obj) (group : ('obj, 'out, 'conn) group) ~key ~payload =
  match List.find_opt (fun entry -> entry.key = key) group.entries with
  | None -> Error (Error.Invalid (Printf.sprintf "no action %S" key))
  | Some entry -> entry.run obj payload

(** Dispatch, then write. Both frontends end here, so neither can reach an action the other cannot
    and neither states which store call follows. *)
let submit conn (group : ('obj, 'out, 'conn) group) obj ~key ~payload =
  Result.bind (dispatch obj group ~key ~payload) (group.persist conn)
