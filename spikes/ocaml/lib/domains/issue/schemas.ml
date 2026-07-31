(** The payload shapes an issue's actions accept — the wire contract, in one place.

    One type per action that takes arguments, each deriving both its decoder and its advertised JSON
    Schema from the same definition. The field doc comments are the schema descriptions a frontend
    shows, so they live here beside the shapes rather than beside the writes: an action in
    [actions.ml] names [type payload = Schemas.<name>] and the [%action] ppx wires the two decoders
    that name derived. An argumentless action ([delete], [restore]) names no payload here and
    [include]s {!Wire.No_payload} instead.

    Referring to {!Models} for the enum types is the only reason this is not pure data. *)

open Ppx_yojson_conv_lib.Yojson_conv.Primitives

type edit_title = { title : string  (** What to call the issue. *) }
[@@deriving yojson, jsonschema ~ocaml_doc]

type edit_body = { body : string  (** What the issue is about. Blank clears it. *) }
[@@deriving yojson, jsonschema ~ocaml_doc]

type edit_status = {
  status : Models.Status.t;  (** Where the issue is up to. *)
  note : string option; [@yojson.option] [@jsonschema.option]
      (** Why it moved. Left out, whatever described the old status goes with it. *)
}
[@@deriving yojson, jsonschema ~ocaml_doc]

type edit_priority = { priority : Models.Priority.t  (** How far up the list it sorts. *) }
[@@deriving yojson, jsonschema ~ocaml_doc]
