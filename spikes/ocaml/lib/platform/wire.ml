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

    Everything an action is, in one structure: the object it applies to, its payload type, its key,
    its two hooks and its write. [execute] is given the connection and does whatever it likes with
    it — any rows, any tables — returning the message a frontend reports. The two JSON values are
    what [[@@deriving yojson, jsonschema]] puts beside the payload type, so a spec names its payload
    once and cannot advertise a schema belonging to a different type than the one it decodes.

    This is the one place ['conn] is fixed to a real {!Db.conn}: {!Action} threads it opaquely, and
    a spec's [execute] writes through the store with it. *)
module type SPEC = sig
  type obj
  type payload

  val key : string
  val is_available : obj -> bool
  val is_disabled : obj -> string option
  val execute : obj -> payload -> Db.conn -> (string, Error.t) result
  val payload_of_yojson : Yojson.Safe.t -> payload
  val payload_jsonschema : Yojson.Safe.t
end

type 'obj entry = {
  key : string;
  schema : Yojson.Safe.t;  (** the arguments, as the JSON Schema an agent consumes *)
  is_available : 'obj -> bool;
  is_disabled : 'obj -> string option;
  run : 'obj -> Yojson.Safe.t -> Db.conn -> (string, Error.t) result;
}
(** One action with its payload type erased.

    The type is universally quantified in {!Make} and captured by the [run] closure, so it does not
    appear here. That is the erasure, and it is a closure rather than an existential type — there is
    nothing to open. What survives is the message every action returns, the one thing a frontend
    reports without knowing which action ran. *)

module type ACTION = sig
  type obj
  type payload

  val action : (obj, payload, Db.conn) Action.t
  (** The typed path, for code that names its action statically. *)

  val entry : obj entry
  (** The erased path, for a registration list. *)
end

module Make (S : SPEC) : ACTION with type obj = S.obj and type payload = S.payload = struct
  type obj = S.obj
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
      run =
        (fun obj json conn ->
          Result.bind (decode S.payload_of_yojson json) (fun payload ->
              Action.run obj action payload conn));
    }
end

(** {1 Groups} *)

type 'obj group = 'obj entry list
(** The actions one kind of object offers.

    Registration is a list of erased entries. There is no store call beside it any more: each
    [execute] holds the transaction and writes for itself, so an INSERT, an UPDATE and a
    [deleted_at] stamp are told apart by what their bodies do rather than by which group they sit
    in. The split that remains is the object a group is offered against — a live row, a deleted one,
    or the list a creator is checked against. *)

(** What the object offers, in registration order.

    An action that does not apply is dropped and a refused one is kept with its reason, so a caller
    is told both what it can do and what it could do but for a reason. [None] is runnable. *)
let available (obj : 'obj) (group : 'obj group) : ('obj entry * string option) list =
  List.filter_map
    (fun entry -> if entry.is_available obj then Some (entry, entry.is_disabled obj) else None)
    group

(** The one entry point for a caller holding a key and a blob. Decode, check the hooks against the
    live object, then write — all of it {!Action.run}'s doing, so what a frontend rendered stays a
    snapshot and the write is checked against the row as it is now. The connection is the frontend's
    open transaction, so a refusal rolls back anything already written. *)
let dispatch (obj : 'obj) (group : 'obj group) ~key ~payload conn =
  match List.find_opt (fun entry -> entry.key = key) group with
  | None -> Error (Error.Invalid (Printf.sprintf "no action %S" key))
  | Some entry -> entry.run obj payload conn
