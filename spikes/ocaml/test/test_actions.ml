(** The domain and the wire.

    The first half is typed payloads and no JSON. The second half is the wire, where JSON is the
    point. Both are pure — no database, no clock — because every [execute] and both hooks are
    functions of the object and the payload and nothing else. The store's half is [test_store.ml]
    and the frontends' is [test_frontend.ml]. *)

open Tt

let stamp = "2026-01-01T00:00:00Z"

let issue ?(status = Issue.Status.Todo) ?(priority = Issue.Priority.Normal) ?status_note () :
    Issue.t =
  {
    id = 1;
    project_id = 1;
    project_slug = "tt";
    title = "a title";
    body = "a body";
    status;
    priority;
    status_note;
    created_at = stamp;
    updated_at = stamp;
  }

let project ?(status = Project.Status.Active) ?(todo = 0) ?(doing = 0) ?(done_ = 0) ?(slug = "tt")
    () : Project.t =
  {
    id = 1;
    slug;
    title = "task tracker";
    body = "";
    status;
    todo;
    doing;
    done_;
    created_at = stamp;
    updated_at = stamp;
  }

let restorable ?(slug_taken = false) p : Project.restorable =
  { deleted = { inner = p; deleted_at = stamp }; slug_taken }

let deleted_issue i : Issue.t Deleted.t = { inner = i; deleted_at = stamp }

(* --- what alcotest compares ---------------------------------------------- *)

let error = Alcotest.testable (fun ppf e -> Format.pp_print_string ppf (Error.to_string e)) ( = )

let issue_t =
  Alcotest.testable
    (fun ppf (i : Issue.t) ->
      Format.fprintf ppf "%s %S %S %s %s %s" (Issue.subject i) i.title i.body
        (Issue.Status.to_string i.status)
        (Issue.Priority.to_string i.priority)
        (Option.value i.status_note ~default:"-"))
    ( = )

let project_t =
  Alcotest.testable
    (fun ppf (p : Project.t) ->
      Format.fprintf ppf "%s %S %s %d/%d/%d" (Project.subject p) p.title
        (Project.Status.to_string p.status)
        p.todo p.doing p.done_)
    ( = )

let issue_draft =
  Alcotest.testable
    (fun ppf (d : Issue.draft) ->
      Format.fprintf ppf "%d %S %S %s" d.project_id d.title d.body
        (Issue.Priority.to_string d.priority))
    ( = )

let project_draft =
  Alcotest.testable
    (fun ppf (d : Project.draft) -> Format.fprintf ppf "%S %S %S" d.slug d.title d.body)
    ( = )

(** [None] is "not offered at all", [Some None] runnable, [Some (Some reason)] refused with its
    reason. Three cases, and only two of them are states an action can be in — which is the whole of
    what {!Wire.available} says. *)
let listed = Alcotest.(option (option string))

let deleted_issue_t =
  Alcotest.testable
    (fun ppf (d : Issue.t Deleted.t) ->
      Format.fprintf ppf "%s @ %s" (Issue.subject d.inner) d.deleted_at)
    ( = )

let deleted_project_t =
  Alcotest.testable
    (fun ppf (d : Project.t Deleted.t) ->
      Format.fprintf ppf "%s @ %s" (Project.subject d.inner) d.deleted_at)
    ( = )

(* --- reaching the wire --------------------------------------------------- *)

let offered key group obj =
  List.assoc_opt key
    (List.map (fun ((e : _ Wire.entry), disabled) -> (e.key, disabled)) (Wire.available obj group))

let keys_of group obj = List.map (fun ((e : _ Wire.entry), _) -> e.key) (Wire.available obj group)

let dispatch obj group key payload =
  Wire.dispatch obj group ~key ~payload:(Yojson.Safe.from_string payload)

let schema key (group : _ Wire.group) =
  let e = List.find (fun (e : _ Wire.entry) -> e.key = key) group.entries in
  Yojson.Safe.to_string e.schema

let is_conflict = function
  | Error (Error.Conflict _) -> true
  | Error (Error.Invalid _ | Error.Broken _) | Ok _ -> false

let is_invalid = function
  | Error (Error.Invalid _) -> true
  | Error (Error.Conflict _ | Error.Broken _) | Ok _ -> false

let case name f = Alcotest.test_case name `Quick f
let check_bool name value = Alcotest.(check bool) name true value

(* --- the issue: every action is always offered --------------------------- *)

(* There is no WIP rule and nothing else about an issue constrains what may be
   done to it, so no issue action has a refusal at all. The honest pair for each
   key is therefore "always offered" plus whatever [execute] refuses — not an
   invented rule to fill the shape. *)
let issue_offers =
  [
    case "editTitle is offered on a done issue" (fun () ->
        Alcotest.check listed "" (Some None)
          (offered "editTitle" Issue_actions.group (issue ~status:Issue.Status.Done ())));
    case "editBody is offered on a done issue" (fun () ->
        Alcotest.check listed "" (Some None)
          (offered "editBody" Issue_actions.group (issue ~status:Issue.Status.Done ())));
    case "editStatus is offered on a done issue" (fun () ->
        Alcotest.check listed "" (Some None)
          (offered "editStatus" Issue_actions.group (issue ~status:Issue.Status.Done ())));
    case "editPriority is offered on a high-priority issue" (fun () ->
        Alcotest.check listed "" (Some None)
          (offered "editPriority" Issue_actions.group (issue ~priority:Issue.Priority.High ())));
    case "delete is offered on any issue" (fun () ->
        Alcotest.check listed "" (Some None)
          (offered "delete" Issue_actions.trash (issue ~status:Issue.Status.Doing ())));
    case "restore is offered on any deleted issue" (fun () ->
        Alcotest.check listed "" (Some None)
          (offered "restore" Issue_actions.deleted_group (deleted_issue (issue ()))));
  ]

(* --- the issue: what execute refuses -------------------------------------- *)

let issue_writes =
  [
    case "editTitle trims its input" (fun () ->
        Alcotest.check
          Alcotest.(result issue_t error)
          ""
          (Ok { (issue ()) with title = "new" })
          (Action.run (issue ()) Issue_actions.Edit_title.action { title = " new " }));
    case "editTitle refuses a blank title" (fun () ->
        check_bool ""
          (is_invalid (Action.run (issue ()) Issue_actions.Edit_title.action { title = "   " })));
    case "editBody accepts a blank body, because that is how you clear one" (fun () ->
        Alcotest.check
          Alcotest.(result issue_t error)
          ""
          (Ok { (issue ()) with body = "" })
          (Action.run (issue ()) Issue_actions.Edit_body.action { body = "" }));
    case "editStatus records its optional note" (fun () ->
        Alcotest.check
          Alcotest.(result issue_t error)
          ""
          (Ok { (issue ()) with status = Issue.Status.Doing; status_note = Some "started" })
          (Action.run (issue ()) Issue_actions.Edit_status.action
             { status = Issue.Status.Doing; note = Some "started" }));
    case "editStatus without a note clears the one describing the old status" (fun () ->
        Alcotest.check
          Alcotest.(result issue_t error)
          ""
          (Ok { (issue ()) with status = Issue.Status.Done })
          (Action.run (issue ~status_note:"started" ()) Issue_actions.Edit_status.action
             { status = Issue.Status.Done; note = None }));
    case "editPriority sets the priority" (fun () ->
        Alcotest.check
          Alcotest.(result issue_t error)
          ""
          (Ok { (issue ()) with priority = Issue.Priority.High })
          (Action.run (issue ()) Issue_actions.Edit_priority.action
             { priority = Issue.Priority.High }));
    case "delete leaves the issue alone, because the column it sets is not on it" (fun () ->
        Alcotest.check
          Alcotest.(result issue_t error)
          ""
          (Ok (issue ()))
          (Action.run (issue ()) Issue_actions.Delete.action ()));
    case "restore leaves the trash row alone for the same reason" (fun () ->
        Alcotest.check
          Alcotest.(result deleted_issue_t error)
          ""
          (Ok (deleted_issue (issue ())))
          (Action.run (deleted_issue (issue ())) Issue_actions.Restore.action ()));
  ]

(* --- the project: the half with preconditions ----------------------------- *)

let project_offers =
  [
    case "editStatus is refused while an issue is doing" (fun () ->
        Alcotest.check listed "" (Some (Some "finish or drop 1 issue first"))
          (offered "editStatus" Project_actions.group (project ~doing:1 ())));
    case "the refusal counts what it is refusing over" (fun () ->
        Alcotest.check listed "" (Some (Some "finish or drop 3 issues first"))
          (offered "editStatus" Project_actions.group (project ~doing:3 ())));
    case "editStatus is runnable with nothing doing" (fun () ->
        Alcotest.check listed "" (Some None)
          (offered "editStatus" Project_actions.group (project ~todo:2 ~done_:5 ())));
    case "editTitle is offered whatever the counts say" (fun () ->
        Alcotest.check listed "" (Some None)
          (offered "editTitle" Project_actions.group (project ~doing:3 ())));
    case "editBody is offered whatever the counts say" (fun () ->
        Alcotest.check listed "" (Some None)
          (offered "editBody" Project_actions.group (project ~doing:3 ())));
    case "delete is refused while the project is active" (fun () ->
        Alcotest.check listed "" (Some (Some "archive it first"))
          (offered "delete" Project_actions.trash (project ())));
    case "delete is runnable once it is archived" (fun () ->
        Alcotest.check listed "" (Some None)
          (offered "delete" Project_actions.trash (project ~status:Project.Status.Archived ())));
    case "addIssue is refused on an archived project" (fun () ->
        Alcotest.check listed "" (Some (Some "project is archived"))
          (offered "addIssue" Project_actions.creators (project ~status:Project.Status.Archived ())));
    case "addIssue is runnable on an active one" (fun () ->
        Alcotest.check listed "" (Some None)
          (offered "addIssue" Project_actions.creators (project ())));
    case "restore is refused once the slug has been taken again" (fun () ->
        Alcotest.check listed "" (Some (Some {|project "tt" exists again|}))
          (offered "restore" Project_actions.deleted_group
             (restorable ~slug_taken:true (project ()))));
    case "restore is runnable while the slug is free" (fun () ->
        Alcotest.check listed "" (Some None)
          (offered "restore" Project_actions.deleted_group (restorable (project ()))));
  ]

(* --- the project: what execute refuses ------------------------------------ *)

let project_writes =
  [
    case "run enforces the hooks before executing" (fun () ->
        check_bool "" (is_conflict (Action.run (project ()) Project_actions.Delete.action ())));
    case "editStatus refused by the hook does not reach execute" (fun () ->
        check_bool ""
          (is_conflict
             (Action.run (project ~doing:1 ()) Project_actions.Edit_status.action
                { status = Project.Status.Archived })));
    case "editTitle refuses a blank title here too" (fun () ->
        check_bool ""
          (is_invalid (Action.run (project ()) Project_actions.Edit_title.action { title = " " })));
    case "restore hands the store the trash row and nothing else" (fun () ->
        Alcotest.check
          Alcotest.(result deleted_project_t error)
          ""
          (Ok { inner = project (); deleted_at = stamp })
          (Action.run (restorable (project ())) Project_actions.Restore.action ()));
    case "create enforces the hooks before creating" (fun () ->
        check_bool ""
          (is_conflict
             (Action.run
                (project ~status:Project.Status.Archived ())
                Project_actions.Add_issue.action
                { title = "x"; body = None; priority = None })));
    case "addIssue defaults the body and the priority" (fun () ->
        Alcotest.check
          Alcotest.(result issue_draft error)
          ""
          (Ok { project_id = 1; title = "ship"; body = ""; priority = Issue.Priority.Normal })
          (Action.run (project ()) Project_actions.Add_issue.action
             { title = " ship "; body = None; priority = None }));
    case "addIssue carries what it was given" (fun () ->
        Alcotest.check
          Alcotest.(result issue_draft error)
          ""
          (Ok { project_id = 1; title = "ship"; body = "why"; priority = Issue.Priority.High })
          (Action.run (project ()) Project_actions.Add_issue.action
             { title = "ship"; body = Some "why"; priority = Some Issue.Priority.High }));
    case "addIssue refuses a blank title" (fun () ->
        check_bool ""
          (is_invalid
             (Action.run (project ()) Project_actions.Add_issue.action
                { title = "  "; body = None; priority = None })));
    case "createProject refuses a slug the loaded list already holds" (fun () ->
        check_bool ""
          (is_conflict
             (Action.run
                [ project () ]
                Project_actions.Create_project.action
                { slug = "tt"; title = None; body = None })));
    case "createProject accepts one it does not" (fun () ->
        Alcotest.check
          Alcotest.(result project_draft error)
          ""
          (Ok { slug = "other"; title = "another"; body = "" })
          (Action.run
             [ project () ]
             Project_actions.Create_project.action
             { slug = "other"; title = Some "another"; body = None }));
    case "createProject refuses a blank slug" (fun () ->
        check_bool ""
          (is_invalid
             (Action.run [] Project_actions.Create_project.action
                { slug = " "; title = None; body = None })));
  ]

(* --- the wire ------------------------------------------------------------- *)

let wire =
  [
    case "the issue group is offered in registration order" (fun () ->
        Alcotest.(check (list string))
          ""
          [ "editTitle"; "editBody"; "editStatus"; "editPriority" ]
          (keys_of Issue_actions.group (issue ())));
    case "the project group is offered in registration order" (fun () ->
        Alcotest.(check (list string))
          ""
          [ "editTitle"; "editBody"; "editStatus" ]
          (keys_of Project_actions.group (project ())));
    case "a refused action is still offered" (fun () ->
        Alcotest.(check (list string))
          ""
          [ "editTitle"; "editBody"; "editStatus" ]
          (keys_of Project_actions.group (project ~doing:1 ())));
    case "dispatch refuses what the hooks refused" (fun () ->
        check_bool ""
          (is_conflict
             (dispatch (project ~doing:1 ()) Project_actions.group "editStatus"
                {|{"status":"archived"}|})));
    case "a creator's dispatch refuses what the hooks refused" (fun () ->
        check_bool ""
          (is_conflict
             (dispatch
                (project ~status:Project.Status.Archived ())
                Project_actions.creators "addIssue" {|{"title":"x"}|})));
    case "an unknown key is invalid" (fun () ->
        check_bool "" (is_invalid (dispatch (issue ()) Issue_actions.group "explode" "{}")));
    case "a missing required field is invalid" (fun () ->
        check_bool "" (is_invalid (dispatch (issue ()) Issue_actions.group "editTitle" "{}")));
    case "a wrongly typed field is invalid" (fun () ->
        check_bool ""
          (is_invalid (dispatch (issue ()) Issue_actions.group "editTitle" {|{"title":5}|})));
    case "a field the schema does not advertise is invalid" (fun () ->
        check_bool ""
          (is_invalid
             (dispatch (issue ()) Issue_actions.group "editTitle" {|{"title":"x","bogus":1}|})));
    case "an empty payload accepts an empty object" (fun () ->
        Alcotest.check
          Alcotest.(result issue_t error)
          ""
          (Ok (issue ()))
          (dispatch (issue ()) Issue_actions.trash "delete" "{}"));
    case "an empty payload refuses arguments it never advertised" (fun () ->
        check_bool ""
          (is_invalid (dispatch (issue ()) Issue_actions.trash "delete" {|{"why":"because"}|})));
    case "an enum accepts a name it advertises" (fun () ->
        Alcotest.check
          Alcotest.(result issue_t error)
          ""
          (Ok { (issue ()) with status = Issue.Status.Doing })
          (dispatch (issue ()) Issue_actions.group "editStatus" {|{"status":"doing"}|}));
    case "an enum refuses a name it does not" (fun () ->
        check_bool ""
          (is_invalid
             (dispatch (issue ()) Issue_actions.group "editStatus" {|{"status":"blocked"}|})));
    case "an enum refuses the constructor name" (fun () ->
        check_bool ""
          (is_invalid (dispatch (issue ()) Issue_actions.group "editStatus" {|{"status":"Doing"}|})));
    case "an optional enum may be omitted" (fun () ->
        Alcotest.check
          Alcotest.(result issue_draft error)
          ""
          (Ok { project_id = 1; title = "x"; body = ""; priority = Issue.Priority.Normal })
          (dispatch (project ()) Project_actions.creators "addIssue" {|{"title":"x"}|}));
    case "an optional enum refuses the null its own schema offers" (fun () ->
        check_bool ""
          (is_invalid
             (dispatch (project ()) Project_actions.creators "addIssue"
                {|{"title":"x","priority":null}|})));
  ]

(* --- keys that appear in two groups --------------------------------------- *)

(* [editTitle], [editBody] and [editStatus] are registered against both objects.
   A dispatcher that resolved the key before it resolved the object would have
   to pick one, and what it would get back is a decode failure rather than a
   type error — so these are the checks that say the key alone is not enough. *)
let shared_keys =
  [
    case "an issue status is not a project status" (fun () ->
        check_bool ""
          (is_invalid
             (dispatch (project ()) Project_actions.group "editStatus" {|{"status":"doing"}|})));
    case "a project status is not an issue status" (fun () ->
        check_bool ""
          (is_invalid
             (dispatch (issue ()) Issue_actions.group "editStatus" {|{"status":"archived"}|})));
    case "the issue payload's note is not advertised by the project's editStatus" (fun () ->
        check_bool ""
          (is_invalid
             (dispatch (project ()) Project_actions.group "editStatus"
                {|{"status":"archived","note":"x"}|})));
    (* [editTitle]'s two payloads are the same shape, so nothing but the
       object's type keeps them apart — and the type is enough only because the
       groups are separate values. *)
    case "editTitle's two payloads are interchangeable, and its two groups are not" (fun () ->
        Alcotest.check
          Alcotest.(result issue_t error)
          ""
          (Ok { (issue ()) with title = "x" })
          (dispatch (issue ()) Issue_actions.group "editTitle" {|{"title":"x"}|});
        Alcotest.check
          Alcotest.(result project_t error)
          ""
          (Ok { (project ()) with title = "x" })
          (dispatch (project ()) Project_actions.group "editTitle" {|{"title":"x"}|}));
    case "the wire agrees with the typed path" (fun () ->
        Alcotest.check
          Alcotest.(result issue_t error)
          ""
          (Action.run (issue ()) Issue_actions.Edit_title.action { title = " new " })
          (dispatch (issue ()) Issue_actions.group "editTitle" {|{"title":" new "}|}));
    case "the creator wire agrees with the typed path" (fun () ->
        Alcotest.check
          Alcotest.(result issue_draft error)
          ""
          (Action.run (project ()) Project_actions.Add_issue.action
             { title = "ship"; body = None; priority = None })
          (dispatch (project ()) Project_actions.creators "addIssue" {|{"title":"ship"}|}));
  ]

(* --- the derived schemas -------------------------------------------------- *)

(* Snapshots, so a change to a payload type shows up here as a diff. These are
   what [tt issue show] hands an agent. Note the property order: the deriver
   emits them in the reverse of the order the type declares them. *)
let schemas =
  [
    case "editTitle advertises a required field, described" (fun () ->
        Alcotest.(check string)
          ""
          {|{"type":"object","properties":{"title":{"description":"What to call the issue.","type":"string"}},"required":["title"],"additionalProperties":false}|}
          (schema "editTitle" Issue_actions.group));
    case "editBody advertises the same shape with a different rule behind it" (fun () ->
        Alcotest.(check string)
          ""
          {|{"type":"object","properties":{"body":{"description":"What the issue is about. Blank clears it.","type":"string"}},"required":["body"],"additionalProperties":false}|}
          (schema "editBody" Issue_actions.group));
    case "an issue's editStatus advertises its three states" (fun () ->
        Alcotest.(check string)
          ""
          {|{"type":"object","properties":{"note":{"description":"Why it moved. Left out, whatever described the old status goes with it.","type":["string","null"]},"status":{"type":"string","enum":["todo","doing","done"]}},"required":["status"],"additionalProperties":false}|}
          (schema "editStatus" Issue_actions.group));
    case "a project's editStatus advertises its two" (fun () ->
        Alcotest.(check string)
          ""
          {|{"type":"object","properties":{"status":{"type":"string","enum":["active","archived"]}},"required":["status"],"additionalProperties":false}|}
          (schema "editStatus" Project_actions.group));
    case "editPriority advertises the wire names, not the integers stored" (fun () ->
        Alcotest.(check string)
          ""
          {|{"type":"object","properties":{"priority":{"type":"string","enum":["normal","high"]}},"required":["priority"],"additionalProperties":false}|}
          (schema "editPriority" Issue_actions.group));
    case "an action with no arguments advertises an object with no properties" (fun () ->
        Alcotest.(check string)
          "" {|{"type":"object","properties":{},"required":[],"additionalProperties":false}|}
          (schema "delete" Issue_actions.trash));
    (* An optional field of a primitive type widens to ["string","null"]; an
       optional field of any other type is wrapped in anyOf instead. One
       deriver, two encodings of the same idea. *)
    case "addIssue advertises an optional enum as anyOf and an optional string as a union"
      (fun () ->
        Alcotest.(check string)
          ""
          {|{"type":"object","properties":{"priority":{"description":"How far up the list it sorts. Normal unless said otherwise.","anyOf":[{"type":"string","enum":["normal","high"]},{"type":"null"}]},"body":{"description":"What it is about.","type":["string","null"]},"title":{"description":"What to call the issue.","type":"string"}},"required":["title"],"additionalProperties":false}|}
          (schema "addIssue" Project_actions.creators));
    case "createProject advertises one required field and two optional ones" (fun () ->
        Alcotest.(check string)
          ""
          {|{"type":"object","properties":{"body":{"description":"What it is for.","type":["string","null"]},"title":{"description":"What to call it.","type":["string","null"]},"slug":{"description":"The short name the project is addressed by.","type":"string"}},"required":["slug"],"additionalProperties":false}|}
          (schema "createProject" Project_actions.root));
  ]

(* --- where the two derivers disagree -------------------------------------- *)

(* They read one type definition and do not read each other. The schema says
   [note] may be null; the decoder, told by [@yojson.option] that the field is
   absent-or-a-string, rejects an explicit null. Nothing in either deriver
   reports the disagreement — only this pair of checks does. *)
let ppx_disagreement =
  [
    case "the advertised schema accepts an explicit null" (fun () ->
        Alcotest.(check string)
          "" {|["string","null"]|}
          (Yojson.Safe.from_string (schema "editStatus" Issue_actions.group)
          |> Yojson.Safe.Util.member "properties"
          |> Yojson.Safe.Util.member "note" |> Yojson.Safe.Util.member "type"
          |> Yojson.Safe.to_string));
    case "the decoder does not" (fun () ->
        check_bool ""
          (is_invalid
             (dispatch (issue ()) Issue_actions.group "editStatus"
                {|{"status":"doing","note":null}|})));
  ]

(* Which doc comments reach the schema, and which are dropped. [~ocaml_doc]
   carries a field's comment into [description] whether the field is required or
   optional, and through the [anyOf] an optional field of a non-primitive type
   widens to. What it loses is a field whose type has a schema of its own: the
   deriver substitutes that type's schema for the property wholesale, so both
   enums registered as required fields reach a frontend as a bare property name.

   The comment also has to be placed where OCaml can attach it, which is after
   the field's attributes rather than before them. A misplaced one is silently
   dropped, and warning 50 — the thing that would have said so — is off by
   default; what catches it here is that ocamlformat refuses the file. *)
let described key group name =
  Form.of_schema (Yojson.Safe.from_string (schema key group))
  |> Result.to_option
  |> Fun.flip Option.bind (fun fields ->
      Option.bind
        (List.find_opt (fun (f : Form.field) -> f.name = name) fields)
        (fun (f : Form.field) -> f.description))

let descriptions =
  [
    case "a required field keeps its doc comment" (fun () ->
        Alcotest.(check (option string))
          "" (Some "What to call the issue.")
          (described "editTitle" Issue_actions.group "title"));
    case "and so does an optional one" (fun () ->
        Alcotest.(check (option string))
          ""
          (Some "Why it moved. Left out, whatever described the old status goes with it.")
          (described "editStatus" Issue_actions.group "note"));
    case "and an optional one wrapped in anyOf" (fun () ->
        Alcotest.(check (option string))
          "" (Some "How far up the list it sorts. Normal unless said otherwise.")
          (described "addIssue" Project_actions.creators "priority"));
    case "a field whose type has a schema of its own does not" (fun () ->
        Alcotest.(check (option string))
          "" None
          (described "editStatus" Issue_actions.group "status");
        Alcotest.(check (option string))
          "" None
          (described "editPriority" Issue_actions.group "priority"));
  ]

(* --- the enums ------------------------------------------------------------ *)

(* Three declarations, four consumers. [to_string] is a match and therefore
   cannot miss a constructor; [of_string] and [names] can, and these are the
   checks that would catch them coming apart. *)
let enums =
  [
    case "an enum decodes its wire name" (fun () ->
        check_bool "" (Project.Status.t_of_yojson (`String "archived") = Project.Status.Archived));
    case "an enum encodes the name it decoded" (fun () ->
        Alcotest.(check string)
          "" {|"archived"|}
          (Yojson.Safe.to_string (Project.Status.yojson_of_t Project.Status.Archived)));
    case "an enum advertises exactly the names it accepts" (fun () ->
        Alcotest.(check (list string))
          ""
          (List.filter (fun name -> Project.Status.of_string name <> None) Project.Status.names)
          Project.Status.names);
    case "every name round-trips through both directions" (fun () ->
        Alcotest.(check (list string))
          "" Issue.Status.names
          (List.map
             (fun name ->
               match Issue.Status.of_string name with
               | Some value -> Issue.Status.to_string value
               | None -> "missing")
             Issue.Status.names));
    case "an enum refuses a constructor name" (fun () ->
        check_bool ""
          (match Project.Status.t_of_yojson (`String "Archived") with
          | _ -> false
          | exception Ppx_yojson_conv_lib.Yojson_conv.Of_yojson_error _ -> true));
    case "an enum refuses a value that is not a string" (fun () ->
        check_bool ""
          (match Project.Status.t_of_yojson (`Int 1) with
          | _ -> false
          | exception Ppx_yojson_conv_lib.Yojson_conv.Of_yojson_error _ -> true));
    case "priority's SQL representation is the same pair, ordered" (fun () ->
        check_bool ""
          (Issue.Priority.to_int Issue.Priority.High > Issue.Priority.to_int Issue.Priority.Normal
          && Issue.Priority.of_int 1 = Some Issue.Priority.High
          && Issue.Priority.of_int 2 = None));
  ]

let clock =
  [
    case "the clock stamps RFC3339 UTC to the second" (fun () ->
        let stamp = Clock.now () in
        check_bool "" (String.length stamp = 20 && stamp.[10] = 'T' && stamp.[19] = 'Z'));
  ]

let suite =
  [
    ("issue: what is offered", issue_offers);
    ("issue: what execute refuses", issue_writes);
    ("project: what is offered", project_offers);
    ("project: what execute refuses", project_writes);
    ("the wire", wire);
    ("keys registered against both objects", shared_keys);
    ("the derived schemas", schemas);
    ("where the two derivers disagree", ppx_disagreement);
    ("which doc comments survive", descriptions);
    ("the enums", enums);
    ("the clock", clock);
  ]
