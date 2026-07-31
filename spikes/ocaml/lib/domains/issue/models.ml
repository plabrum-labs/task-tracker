(** An issue, always read through the project it belongs to.

    [project_slug] comes from the read rather than from a column — it is never written, and it is
    what makes a list row printable without a second query per row. It is on the domain type because
    the join puts it there: {!Store.issues} selects it alongside the issue's own columns. *)

open Platform

(* An enum is three declarations and no functor. [to_string] is a match, so
   adding a constructor is a compile error here rather than a value the encoder
   raises on the first time anything reaches it — the one thing a table of
   (constructor, wire name) pairs could never check about itself. [of_string]
   and [names] can still drift from it, and only the round trip in test_tt.ml
   says they have not. *)

module Status = struct
  type t = Todo | Doing | Done

  let to_string = function Todo -> "todo" | Doing -> "doing" | Done -> "done"

  let of_string = function
    | "todo" -> Some Todo
    | "doing" -> Some Doing
    | "done" -> Some Done
    | _ -> None

  let names = [ "todo"; "doing"; "done" ]
  let yojson_of_t value = `String (to_string value)
  let t_of_yojson = Wire.enum_of_yojson ~name:"status" ~of_string ~names
  let t_jsonschema = Wire.enum_jsonschema ~names
end

module Priority = struct
  type t = Normal | High

  let to_string = function Normal -> "normal" | High -> "high"
  let of_string = function "normal" -> Some Normal | "high" -> Some High | _ -> None
  let names = [ "normal"; "high" ]
  let yojson_of_t value = `String (to_string value)
  let t_of_yojson = Wire.enum_of_yojson ~name:"priority" ~of_string ~names
  let t_jsonschema = Wire.enum_jsonschema ~names

  (** The column is an INTEGER, so this type has two representations: ["high"] on the wire, [1] in
      SQL. The integer is what [ORDER BY priority DESC] sorts by, so the column's type is not only a
      storage decision. Both directions are matches, so neither can miss a constructor. *)
  let to_int = function Normal -> 0 | High -> 1

  let of_int = function 0 -> Some Normal | 1 -> Some High | _ -> None
end

type t = {
  id : int;
  project_id : int;
  project_slug : string;  (** From the join. No action writes it. *)
  title : string;
  body : string;
  status : Status.t;
  priority : Priority.t;
  status_note : string option;
  created_at : string;
  updated_at : string;
}

type draft = { project_id : int; title : string; body : string; priority : Priority.t }
(** What [addIssue]'s [execute] builds and hands to {!Services.create}: an issue minus what the
    store assigns. No [id] and no stamps, because a type that could carry them would let a caller
    propose one. *)

let subject (t : t) = Printf.sprintf "issue %d" t.id
