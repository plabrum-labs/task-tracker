(** [%action ...] — the base class and the decorator, as one extension.

    An action's declaration is a struct: its object type, its key, its two hooks and its [execute],
    plus a [payload] the wire decodes. [Wire.Make] turns that into the typed and erased forms, but
    applying it by hand costs a named [Spec] wrapper and an [include] on every action. This rewrites

    {[
     module Edit_title = [%action
         include Action.Defaults
         type obj = Models.t
         type payload = Schemas.edit_title
         let key = "editTitle"
         let execute (issue : obj) (p : payload) conn = ...
       ]
    ]}

    into the [module Spec = struct ... end] / [include Wire.Make (Spec)] pair the reader no longer
    writes. [Wire] is expected to be in scope at the use site.

    When the payload is a type from another module — [type payload = Schemas.edit_title] — the two
    decoders that type derived are named after {e it} ([Schemas.edit_title_of_yojson] and
    [Schemas.edit_title_jsonschema]), not after [payload], which is what [Wire.Make]'s signature
    wants. So the rewrite also aliases them, and that is what lets the payload shapes live in one
    [schemas.ml] while the action states only [type payload = Schemas.<name>]. A payload defined
    inline still derives its own [payload_of_yojson]/[payload_jsonschema], and an argumentless
    action still [include]s [Wire.No_payload]; in both cases there is no external name to alias and
    this generates nothing. *)

open Ppxlib

(* [type payload = M.foo] gives the payload's decoder and schema the names M.foo derived —
   M.foo_of_yojson and M.foo_jsonschema — so alias them under the names Wire.Make wants. Only a
   payload that is a manifest reference to another module needs this; an inline record or a
   Wire.No_payload include already binds them. *)
let codec_aliases ~loc (items : structure) : structure =
  let external_payload =
    List.find_map
      (fun item ->
        match item.pstr_desc with
        | Pstr_type (_, tds) ->
            List.find_map
              (fun td ->
                match (td.ptype_name.txt, td.ptype_manifest) with
                | "payload", Some { ptyp_desc = Ptyp_constr ({ txt = Ldot _ as lid; _ }, []); _ } ->
                    Some lid
                | _ -> None)
              tds
        | _ -> None)
      items
  in
  match external_payload with
  | Some (Ldot (prefix, name)) ->
      let open Ast_builder.Default in
      let alias bound derived =
        pstr_value ~loc Nonrecursive
          [
            value_binding ~loc
              ~pat:(ppat_var ~loc { txt = bound; loc })
              ~expr:(pexp_ident ~loc { txt = Ldot (prefix, name ^ derived); loc });
          ]
      in
      [ alias "payload_of_yojson" "_of_yojson"; alias "payload_jsonschema" "_jsonschema" ]
  | Some _ | None -> []

let expand ~ctxt (items : structure) : module_expr =
  let loc = Expansion_context.Extension.extension_point_loc ctxt in
  let open Ast_builder.Default in
  let spec = pmod_structure ~loc (items @ codec_aliases ~loc items) in
  pmod_structure ~loc
    [
      pstr_module ~loc (module_binding ~loc ~name:{ txt = Some "Spec"; loc } ~expr:spec);
      pstr_include ~loc
        (include_infos ~loc
           (pmod_apply ~loc
              (pmod_ident ~loc { txt = Ldot (Lident "Wire", "Make"); loc })
              (pmod_ident ~loc { txt = Lident "Spec"; loc })));
    ]

let ext = Extension.V3.declare "action" Extension.Context.module_expr Ast_pattern.(pstr __) expand
let () = Driver.register_transformation "ppx_action" ~extensions:[ ext ]
