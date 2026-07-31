(** The argument form a frontend builds from an action's advertised schema.

    This is the second half of the erased path. {!Wire.available} says which actions apply; this
    says what each one wants typed in. Both come from the schema the payload type derived, so a
    frontend needs no knowledge of any particular action — {!Command} turns what {!of_schema}
    returns into options and {!Tui} turns it into controls.

    Pure, and separate from the rendering for that reason: the shape of a form is decided here and
    asserted in the tests, and the two frontends are left with drawing and key handling. *)

type kind =
  | Text  (** [{"type": "string"}] *)
  | Optional_text  (** [{"type": ["string", "null"]}] *)
  | Enum of string list  (** [{"type": "string", "enum": […]}] *)

type field = { name : string; required : bool; kind : kind; description : string option }
(** One field to render.

    [description] is what the [~ocaml_doc] flag buys: a doc comment on the payload's field reaches
    the schema, so the CLI has real [--help] text and the TUI has a real label. It is the field's
    own comment, not a second structure beside the type. *)

let strings names = List.filter_map (function `String n -> Some n | _ -> None) names

(** An optional field of a primitive type widens to [["string", "null"]]; an optional field of any
    other type becomes [anyOf: […, {"type": "null"}]] instead. Two encodings of the same idea, from
    one deriver, decided by something the schema reader cannot see — so the null arm is stripped and
    the rest read as if it had been required. What makes that safe is [required], which the schema
    states separately and {!of_schema} reads from there. *)
let rec kind_of_spec (spec : Yojson.Safe.t) : (kind, string) result =
  let member = Yojson.Safe.Util.member in
  match member "anyOf" spec with
  | `List [ inner; null ] when member "type" null = `String "null" -> kind_of_spec inner
  | _ -> (
      match (member "type" spec, member "enum" spec) with
      | `String "string", `List names -> Ok (Enum (strings names))
      | `String "string", _ -> Ok Text
      | `List [ `String "string"; `String "null" ], _ -> Ok Optional_text
      | json, _ -> Error (Yojson.Safe.to_string json))

let description spec =
  match Yojson.Safe.Util.member "description" spec with `String d -> Some d | _ -> None

(** The fields to render, in the order the schema lists them — which is the reverse of the order the
    payload type declares them, because that is the order [ppx_deriving_jsonschema] emits
    [properties] in. A form or a [--help] reads bottom-up through the type it came from, and there
    is nothing in the schema to sort by instead.

    A property whose type has no form field fails the whole form rather than being dropped. Dropping
    it would render a form that cannot express the action, and submitting it would produce a payload
    the decoder refuses for a reason nothing on screen mentions. *)
let of_schema (schema : Yojson.Safe.t) : (field list, string) result =
  let required =
    match Yojson.Safe.Util.member "required" schema with `List names -> strings names | _ -> []
  in
  let rec collect acc = function
    | [] -> Ok (List.rev acc)
    | (name, spec) :: rest -> (
        match kind_of_spec spec with
        | Error what -> Error (Printf.sprintf "%s: no form field for type %s" name what)
        | Ok kind ->
            collect
              ({ name; required = List.mem name required; kind; description = description spec }
              :: acc)
              rest)
  in
  match Yojson.Safe.Util.member "properties" schema with
  | `Assoc properties -> collect [] properties
  | _ -> Error "schema advertises no properties"

(** What the typed-in values make, ready for {!Wire.dispatch}.

    An empty [Optional_text] is omitted rather than sent as [null], even though the schema
    advertises [null] as acceptable. The two derivers disagree here: [@jsonschema.option] widens the
    type to ["string" | "null"] while [@yojson.option] means absent-or-a-string, so the decoder
    refuses the very null its own schema offers. Omission is the only encoding both halves accept.

    An empty [Text] is sent as [""] rather than withheld. The action's [execute] is the enforcement
    point — [editTitle] is what decides a blank title is refused, and a form that second-guessed it
    would be a second place that has to agree.

    An [Enum] is always sent, whether or not the schema marks it required. A selector over a closed
    list has no blank state to mean "absent" without inventing one, and the schema does not say
    which value the column defaults to — so an optional enum left alone submits the first value it
    advertises. That is the right answer only while the first value {e is} the default, which is
    true of [priority] and is not something this function can check. *)
let payload (values : (field * string) list) : Yojson.Safe.t =
  `Assoc
    (List.filter_map
       (fun (field, value) ->
         match field.kind with
         | Text | Enum _ -> Some (field.name, `String value)
         | Optional_text -> if value = "" then None else Some (field.name, `String value))
       values)
