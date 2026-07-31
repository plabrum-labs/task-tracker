(** The payload shapes a project's actions accept — the wire contract, in one place.

    See [../issue/schemas.ml]: one type per action that takes arguments, each deriving its decoder
    and its schema together, with the field descriptions a frontend shows sitting beside the shapes.
    [addIssue] carries an issue's priority, so this refers across to {!Issue.Priority} — the same
    direction [services.ml] already depends in. *)

open Ppx_yojson_conv_lib.Yojson_conv.Primitives

type edit_title = { title : string  (** What to call the project. *) }
[@@deriving yojson, jsonschema ~ocaml_doc]

type edit_body = { body : string  (** What the project is for. Blank clears it. *) }
[@@deriving yojson, jsonschema ~ocaml_doc]

type edit_status = {
  status : Models.Status.t;  (** Whether the project is still being worked on. *)
}
[@@deriving yojson, jsonschema ~ocaml_doc]

type add_issue = {
  title : string;  (** What to call the issue. *)
  body : string option; [@yojson.option] [@jsonschema.option]  (** What it is about. *)
  priority : Issue.Priority.t option; [@yojson.option] [@jsonschema.option]
      (** How far up the list it sorts. Normal unless said otherwise. *)
}
[@@deriving yojson, jsonschema ~ocaml_doc]

type create_project = {
  slug : string;  (** The short name the project is addressed by. *)
  title : string option; [@yojson.option] [@jsonschema.option]  (** What to call it. *)
  body : string option; [@yojson.option] [@jsonschema.option]  (** What it is for. *)
}
[@@deriving yojson, jsonschema ~ocaml_doc]
