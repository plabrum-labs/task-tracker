(** The domain and the wire.

    Availability is a pure function of the object, so what an object offers is asserted against
    objects built in memory with no database in sight. Running an action is not: [execute] holds the
    transaction and writes for itself now, so a run goes against a real in-memory SQLite connection
    — the same fixture [test_store.ml] uses — and a case asserts the message it returned and, where
    the point is what it wrote, reads the row back. The schema half is pure again: it is what
    [[@@deriving jsonschema]] put beside the payload type, and needs neither a clock nor a database.

    The store's own half is [test_store.ml] and the frontends' is [test_frontend.ml]. *)

open Tt
open Tt.Platform
open Tt.Domains
open Tt.Frontend

let stamp = "2026-01-01T00:00:00Z"

(** Objects built in memory, for the availability hooks — which read only the object. A run needs a
    row that exists, and reaches for {!fixture} instead. *)
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

(* --- a real connection, for the runs ------------------------------------- *)

let ok = function Ok value -> value | Error e -> Alcotest.failf "store: %s" (Db.show_error e)

(** One connection, the schema applied, and one project [tt] (id 1) with one issue (id 1) under it.
    A run goes against these; an [addIssue] then makes issue 2, a second [createProject] project 2.
*)
let fixture () =
  let conn = ok (Db.connect "sqlite3::memory:") in
  ok (Db.apply_ddl conn Schema.ddl);
  let project =
    ok (Project.Services.create { slug = "tt"; title = "task tracker"; body = "" } conn)
  in
  let issue =
    ok
      (Issue.Services.create
         { project_id = project.id; title = "a title"; body = "a body"; priority = Normal }
         conn)
  in
  (conn, project, issue)

let reload_issue conn id =
  match ok (Issue.Services.find ~id conn) with Some i -> i | None -> Alcotest.fail "issue gone"

let reload_project conn slug =
  match ok (Project.Services.find ~slug conn) with
  | Some p -> p
  | None -> Alcotest.fail "project gone"

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

(** [None] is "not offered at all", [Some None] runnable, [Some (Some reason)] refused with its
    reason. Three cases, and only two of them are states an action can be in — which is the whole of
    what {!Wire.available} says. *)
let listed = Alcotest.(option (option string))

(* --- reaching the wire --------------------------------------------------- *)

let offered key group obj =
  List.assoc_opt key
    (List.map (fun ((e : _ Wire.entry), disabled) -> (e.key, disabled)) (Wire.available obj group))

let keys_of group obj = List.map (fun ((e : _ Wire.entry), _) -> e.key) (Wire.available obj group)

let dispatch conn obj group key payload =
  Wire.dispatch obj group ~key ~payload:(Yojson.Safe.from_string payload) conn

let schema key group =
  let e = List.find (fun (e : _ Wire.entry) -> e.key = key) group in
  Yojson.Safe.to_string e.schema

let is_conflict = function
  | Error (Error.Conflict _) -> true
  | Error (Error.Invalid _ | Error.Broken _) | Ok _ -> false

let is_invalid = function
  | Error (Error.Invalid _) -> true
  | Error (Error.Conflict _ | Error.Broken _) | Ok _ -> false

let case name f = Alcotest.test_case name `Quick f
let check_bool name value = Alcotest.(check bool) name true value
let message = Alcotest.(result string error)

(* --- the issue: every action is always offered --------------------------- *)

(* There is no WIP rule and nothing else about an issue constrains what may be
   done to it, so no issue action has a refusal at all. The honest pair for each
   key is therefore "always offered" plus whatever [execute] refuses — not an
   invented rule to fill the shape. *)
let issue_offers =
  [
    case "editTitle is offered on a done issue" (fun () ->
        Alcotest.check listed "" (Some None)
          (offered "editTitle" Issue.Actions.group (issue ~status:Issue.Status.Done ())));
    case "editBody is offered on a done issue" (fun () ->
        Alcotest.check listed "" (Some None)
          (offered "editBody" Issue.Actions.group (issue ~status:Issue.Status.Done ())));
    case "editStatus is offered on a done issue" (fun () ->
        Alcotest.check listed "" (Some None)
          (offered "editStatus" Issue.Actions.group (issue ~status:Issue.Status.Done ())));
    case "editPriority is offered on a high-priority issue" (fun () ->
        Alcotest.check listed "" (Some None)
          (offered "editPriority" Issue.Actions.group (issue ~priority:Issue.Priority.High ())));
    case "delete is offered on any issue" (fun () ->
        Alcotest.check listed "" (Some None)
          (offered "delete" Issue.Actions.trash (issue ~status:Issue.Status.Doing ())));
    case "restore is offered on any deleted issue" (fun () ->
        Alcotest.check listed "" (Some None)
          (offered "restore" Issue.Actions.deleted_group (deleted_issue (issue ()))));
  ]

(* --- the issue: what execute writes and refuses --------------------------- *)

let issue_writes =
  [
    case "editTitle trims its input and reports the write" (fun () ->
        let conn, _, issue = fixture () in
        Alcotest.check message "" (Ok "issue 1: saved")
          (Action.run issue Issue.Actions.Edit_title.action { title = " new " } conn);
        Alcotest.(check string) "the trimmed title landed" "new" (reload_issue conn issue.id).title);
    case "editTitle refuses a blank title, and writes nothing" (fun () ->
        let conn, _, issue = fixture () in
        check_bool "refused"
          (is_invalid (Action.run issue Issue.Actions.Edit_title.action { title = "   " } conn));
        Alcotest.(check string) "the old title stands" "a title" (reload_issue conn issue.id).title);
    case "editBody accepts a blank body, because that is how you clear one" (fun () ->
        let conn, _, issue = fixture () in
        Alcotest.check message "" (Ok "issue 1: saved")
          (Action.run issue Issue.Actions.Edit_body.action { body = "" } conn);
        Alcotest.(check string) "" "" (reload_issue conn issue.id).body);
    case "editStatus records its optional note" (fun () ->
        let conn, _, issue = fixture () in
        ignore
          (Action.run issue Issue.Actions.Edit_status.action
             { status = Issue.Status.Doing; note = Some "started" }
             conn
            : (string, Error.t) result);
        Alcotest.check issue_t ""
          { issue with status = Issue.Status.Doing; status_note = Some "started" }
          (reload_issue conn issue.id));
    case "editStatus without a note clears the one describing the old status" (fun () ->
        let conn, _, issue = fixture () in
        ignore
          (Action.run issue Issue.Actions.Edit_status.action
             { status = Issue.Status.Doing; note = Some "started" }
             conn
            : (string, Error.t) result);
        ignore
          (Action.run (reload_issue conn issue.id) Issue.Actions.Edit_status.action
             { status = Issue.Status.Done; note = None }
             conn
            : (string, Error.t) result);
        check_bool "" ((reload_issue conn issue.id).status_note = None));
    case "editPriority sets the priority" (fun () ->
        let conn, _, issue = fixture () in
        ignore
          (Action.run issue Issue.Actions.Edit_priority.action
             { priority = Issue.Priority.High }
             conn
            : (string, Error.t) result);
        check_bool "" ((reload_issue conn issue.id).priority = Issue.Priority.High));
    case "delete stamps the row and reports it, and the issue leaves the live reads" (fun () ->
        let conn, _, issue = fixture () in
        Alcotest.check message "" (Ok "issue 1: deleted")
          (Action.run issue Issue.Actions.Delete.action () conn);
        check_bool "gone from the live reads" (ok (Issue.Services.find ~id:issue.id conn) = None));
    case "restore clears the stamp and brings the row back" (fun () ->
        let conn, _, issue = fixture () in
        ok (Issue.Services.delete issue conn);
        let deleted =
          match ok (Issue.Services.trashed conn) with
          | d :: _ -> d
          | [] -> Alcotest.fail "not in the trash"
        in
        Alcotest.check message "" (Ok "issue 1: restored")
          (Action.run deleted Issue.Actions.Restore.action () conn);
        check_bool "back in the live reads" (ok (Issue.Services.find ~id:issue.id conn) <> None));
  ]

(* --- the project: the half with preconditions ----------------------------- *)

let project_offers =
  [
    case "editStatus is refused while an issue is doing" (fun () ->
        Alcotest.check listed "" (Some (Some "finish or drop 1 issue first"))
          (offered "editStatus" Project.Actions.group (project ~doing:1 ())));
    case "the refusal counts what it is refusing over" (fun () ->
        Alcotest.check listed "" (Some (Some "finish or drop 3 issues first"))
          (offered "editStatus" Project.Actions.group (project ~doing:3 ())));
    case "editStatus is runnable with nothing doing" (fun () ->
        Alcotest.check listed "" (Some None)
          (offered "editStatus" Project.Actions.group (project ~todo:2 ~done_:5 ())));
    case "editTitle is offered whatever the counts say" (fun () ->
        Alcotest.check listed "" (Some None)
          (offered "editTitle" Project.Actions.group (project ~doing:3 ())));
    case "editBody is offered whatever the counts say" (fun () ->
        Alcotest.check listed "" (Some None)
          (offered "editBody" Project.Actions.group (project ~doing:3 ())));
    case "delete is refused while the project is active" (fun () ->
        Alcotest.check listed "" (Some (Some "archive it first"))
          (offered "delete" Project.Actions.trash (project ())));
    case "delete is runnable once it is archived" (fun () ->
        Alcotest.check listed "" (Some None)
          (offered "delete" Project.Actions.trash (project ~status:Project.Status.Archived ())));
    case "addIssue is refused on an archived project" (fun () ->
        Alcotest.check listed "" (Some (Some "project is archived"))
          (offered "addIssue" Project.Actions.creators (project ~status:Project.Status.Archived ())));
    case "addIssue is runnable on an active one" (fun () ->
        Alcotest.check listed "" (Some None)
          (offered "addIssue" Project.Actions.creators (project ())));
    case "restore is refused once the slug has been taken again" (fun () ->
        Alcotest.check listed "" (Some (Some {|project "tt" exists again|}))
          (offered "restore" Project.Actions.deleted_group
             (restorable ~slug_taken:true (project ()))));
    case "restore is runnable while the slug is free" (fun () ->
        Alcotest.check listed "" (Some None)
          (offered "restore" Project.Actions.deleted_group (restorable (project ()))));
  ]

(* --- the project: what execute writes and refuses ------------------------- *)

let project_writes =
  [
    case "run enforces the hooks before executing" (fun () ->
        let conn, project, _ = fixture () in
        check_bool "a delete of an active project is a conflict"
          (is_conflict (Action.run project Project.Actions.Delete.action () conn));
        check_bool "and nothing was written" (ok (Project.Services.find ~slug:"tt" conn) <> None));
    case "editStatus refused by the hook does not reach execute" (fun () ->
        let conn, _, _ = fixture () in
        (* One issue doing, so archiving is refused by the hook. *)
        let doing = { (reload_project conn "tt") with doing = 1 } in
        check_bool ""
          (is_conflict
             (Action.run doing Project.Actions.Edit_status.action
                { status = Project.Status.Archived }
                conn));
        check_bool "the project is still active"
          ((reload_project conn "tt").status = Project.Status.Active));
    case "editTitle refuses a blank title here too" (fun () ->
        let conn, project, _ = fixture () in
        check_bool ""
          (is_invalid (Action.run project Project.Actions.Edit_title.action { title = " " } conn)));
    case "restore brings a deleted project back" (fun () ->
        let conn, project, _ = fixture () in
        ok (Project.Services.update { project with status = Project.Status.Archived } conn);
        ok (Project.Services.delete project conn);
        let deleted =
          match ok (Project.Services.trashed conn) with
          | d :: _ -> d
          | [] -> Alcotest.fail "not in the trash"
        in
        Alcotest.check message "" (Ok "project tt: restored")
          (Action.run (restorable deleted.inner) Project.Actions.Restore.action () conn);
        check_bool "and is live again" (ok (Project.Services.find ~slug:"tt" conn) <> None));
    case "create enforces the hooks before creating" (fun () ->
        let conn, _, _ = fixture () in
        let archived = { (reload_project conn "tt") with status = Project.Status.Archived } in
        check_bool ""
          (is_conflict
             (Action.run archived Project.Actions.Add_issue.action
                { title = "x"; body = None; priority = None }
                conn)));
    case "addIssue defaults the body and the priority" (fun () ->
        let conn, project, _ = fixture () in
        Alcotest.check message "" (Ok "issue 2: created")
          (Action.run project Project.Actions.Add_issue.action
             { title = " ship "; body = None; priority = None }
             conn);
        let created = reload_issue conn 2 in
        check_bool ""
          (created.title = "ship" && created.body = "" && created.priority = Issue.Priority.Normal));
    case "addIssue carries what it was given" (fun () ->
        let conn, project, _ = fixture () in
        ignore
          (Action.run project Project.Actions.Add_issue.action
             { title = "ship"; body = Some "why"; priority = Some Issue.Priority.High }
             conn
            : (string, Error.t) result);
        let created = reload_issue conn 2 in
        check_bool ""
          (created.title = "ship" && created.body = "why" && created.priority = Issue.Priority.High));
    case "addIssue refuses a blank title" (fun () ->
        let conn, project, _ = fixture () in
        check_bool ""
          (is_invalid
             (Action.run project Project.Actions.Add_issue.action
                { title = "  "; body = None; priority = None }
                conn)));
    case "createProject refuses a slug the loaded list already holds" (fun () ->
        let conn, _, _ = fixture () in
        let projects = ok (Project.Services.list conn) in
        check_bool ""
          (is_conflict
             (Action.run projects Project.Actions.Create_project.action
                { slug = "tt"; title = None; body = None }
                conn)));
    case "createProject accepts one it does not" (fun () ->
        let conn, _, _ = fixture () in
        let projects = ok (Project.Services.list conn) in
        Alcotest.check message "" (Ok "project other: created")
          (Action.run projects Project.Actions.Create_project.action
             { slug = "other"; title = Some "another"; body = None }
             conn);
        let created = reload_project conn "other" in
        check_bool "" (created.title = "another" && created.body = ""));
    case "createProject refuses a blank slug" (fun () ->
        let conn, _, _ = fixture () in
        check_bool ""
          (is_invalid
             (Action.run [] Project.Actions.Create_project.action
                { slug = " "; title = None; body = None }
                conn)));
  ]

(* --- the wire ------------------------------------------------------------- *)

let wire =
  [
    case "the issue group is offered in registration order" (fun () ->
        Alcotest.(check (list string))
          ""
          [ "editTitle"; "editBody"; "editStatus"; "editPriority" ]
          (keys_of Issue.Actions.group (issue ())));
    case "the project group is offered in registration order" (fun () ->
        Alcotest.(check (list string))
          ""
          [ "editTitle"; "editBody"; "editStatus" ]
          (keys_of Project.Actions.group (project ())));
    case "a refused action is still offered" (fun () ->
        Alcotest.(check (list string))
          ""
          [ "editTitle"; "editBody"; "editStatus" ]
          (keys_of Project.Actions.group (project ~doing:1 ())));
    case "dispatch refuses what the hooks refused, before any write" (fun () ->
        let conn, _, _ = fixture () in
        let doing = { (reload_project conn "tt") with doing = 1 } in
        check_bool ""
          (is_conflict
             (dispatch conn doing Project.Actions.group "editStatus" {|{"status":"archived"}|})));
    case "a creator's dispatch refuses what the hooks refused" (fun () ->
        let conn, _, _ = fixture () in
        let archived = { (reload_project conn "tt") with status = Project.Status.Archived } in
        check_bool ""
          (is_conflict
             (dispatch conn archived Project.Actions.creators "addIssue" {|{"title":"x"}|})));
    case "an unknown key is invalid" (fun () ->
        let conn, _, issue = fixture () in
        check_bool "" (is_invalid (dispatch conn issue Issue.Actions.group "explode" "{}")));
    case "a missing required field is invalid" (fun () ->
        let conn, _, issue = fixture () in
        check_bool "" (is_invalid (dispatch conn issue Issue.Actions.group "editTitle" "{}")));
    case "a wrongly typed field is invalid" (fun () ->
        let conn, _, issue = fixture () in
        check_bool ""
          (is_invalid (dispatch conn issue Issue.Actions.group "editTitle" {|{"title":5}|})));
    case "a field the schema does not advertise is invalid" (fun () ->
        let conn, _, issue = fixture () in
        check_bool ""
          (is_invalid
             (dispatch conn issue Issue.Actions.group "editTitle" {|{"title":"x","bogus":1}|})));
    case "an empty payload accepts an empty object, and the write goes through" (fun () ->
        let conn, _, issue = fixture () in
        Alcotest.check message "" (Ok "issue 1: deleted")
          (dispatch conn issue Issue.Actions.trash "delete" "{}"));
    case "an empty payload refuses arguments it never advertised" (fun () ->
        let conn, _, issue = fixture () in
        check_bool ""
          (is_invalid (dispatch conn issue Issue.Actions.trash "delete" {|{"why":"because"}|})));
    case "an enum accepts a name it advertises" (fun () ->
        let conn, _, issue = fixture () in
        Alcotest.check message "" (Ok "issue 1: saved")
          (dispatch conn issue Issue.Actions.group "editStatus" {|{"status":"doing"}|});
        check_bool "" ((reload_issue conn issue.id).status = Issue.Status.Doing));
    case "an enum refuses a name it does not" (fun () ->
        let conn, _, issue = fixture () in
        check_bool ""
          (is_invalid
             (dispatch conn issue Issue.Actions.group "editStatus" {|{"status":"blocked"}|})));
    case "an enum refuses the constructor name" (fun () ->
        let conn, _, issue = fixture () in
        check_bool ""
          (is_invalid (dispatch conn issue Issue.Actions.group "editStatus" {|{"status":"Doing"}|})));
    case "an optional enum may be omitted" (fun () ->
        let conn, project, _ = fixture () in
        Alcotest.check message "" (Ok "issue 2: created")
          (dispatch conn project Project.Actions.creators "addIssue" {|{"title":"x"}|});
        check_bool "" ((reload_issue conn 2).priority = Issue.Priority.Normal));
    case "an optional enum refuses the null its own schema offers" (fun () ->
        let conn, project, _ = fixture () in
        check_bool ""
          (is_invalid
             (dispatch conn project Project.Actions.creators "addIssue"
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
        let conn, _, _ = fixture () in
        let project = reload_project conn "tt" in
        check_bool ""
          (is_invalid
             (dispatch conn project Project.Actions.group "editStatus" {|{"status":"doing"}|})));
    case "a project status is not an issue status" (fun () ->
        let conn, _, issue = fixture () in
        check_bool ""
          (is_invalid
             (dispatch conn issue Issue.Actions.group "editStatus" {|{"status":"archived"}|})));
    case "the issue payload's note is not advertised by the project's editStatus" (fun () ->
        let conn, _, _ = fixture () in
        let project = reload_project conn "tt" in
        check_bool ""
          (is_invalid
             (dispatch conn project Project.Actions.group "editStatus"
                {|{"status":"archived","note":"x"}|})));
    (* [editTitle]'s two payloads are the same shape, so nothing but the
       object's type keeps them apart — and the type is enough only because the
       groups are separate values. *)
    case "editTitle's two payloads are interchangeable, and its two groups are not" (fun () ->
        let conn, project, issue = fixture () in
        Alcotest.check message "" (Ok "issue 1: saved")
          (dispatch conn issue Issue.Actions.group "editTitle" {|{"title":"x"}|});
        Alcotest.check message "" (Ok "project tt: saved")
          (dispatch conn project Project.Actions.group "editTitle" {|{"title":"x"}|}));
    (* The wire and the typed path reach the same action, so on a fresh fixture
       each they return the same message — the write and the reason for it are
       one thing, seen twice. *)
    case "the wire agrees with the typed path" (fun () ->
        let typed =
          let conn, _, issue = fixture () in
          Action.run issue Issue.Actions.Edit_title.action { title = " new " } conn
        in
        let wired =
          let conn, _, issue = fixture () in
          dispatch conn issue Issue.Actions.group "editTitle" {|{"title":" new "}|}
        in
        Alcotest.check message "" typed wired);
    case "the creator wire agrees with the typed path" (fun () ->
        let typed =
          let conn, project, _ = fixture () in
          Action.run project Project.Actions.Add_issue.action
            { title = "ship"; body = None; priority = None }
            conn
        in
        let wired =
          let conn, project, _ = fixture () in
          dispatch conn project Project.Actions.creators "addIssue" {|{"title":"ship"}|}
        in
        Alcotest.check message "" typed wired);
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
          (schema "editTitle" Issue.Actions.group));
    case "editBody advertises the same shape with a different rule behind it" (fun () ->
        Alcotest.(check string)
          ""
          {|{"type":"object","properties":{"body":{"description":"What the issue is about. Blank clears it.","type":"string"}},"required":["body"],"additionalProperties":false}|}
          (schema "editBody" Issue.Actions.group));
    case "an issue's editStatus advertises its three states" (fun () ->
        Alcotest.(check string)
          ""
          {|{"type":"object","properties":{"note":{"description":"Why it moved. Left out, whatever described the old status goes with it.","type":["string","null"]},"status":{"type":"string","enum":["todo","doing","done"]}},"required":["status"],"additionalProperties":false}|}
          (schema "editStatus" Issue.Actions.group));
    case "a project's editStatus advertises its two" (fun () ->
        Alcotest.(check string)
          ""
          {|{"type":"object","properties":{"status":{"type":"string","enum":["active","archived"]}},"required":["status"],"additionalProperties":false}|}
          (schema "editStatus" Project.Actions.group));
    case "editPriority advertises the wire names, not the integers stored" (fun () ->
        Alcotest.(check string)
          ""
          {|{"type":"object","properties":{"priority":{"type":"string","enum":["normal","high"]}},"required":["priority"],"additionalProperties":false}|}
          (schema "editPriority" Issue.Actions.group));
    case "an action with no arguments advertises an object with no properties" (fun () ->
        Alcotest.(check string)
          "" {|{"type":"object","properties":{},"required":[],"additionalProperties":false}|}
          (schema "delete" Issue.Actions.trash));
    (* An optional field of a primitive type widens to ["string","null"]; an
       optional field of any other type is wrapped in anyOf instead. One
       deriver, two encodings of the same idea. *)
    case "addIssue advertises an optional enum as anyOf and an optional string as a union"
      (fun () ->
        Alcotest.(check string)
          ""
          {|{"type":"object","properties":{"priority":{"description":"How far up the list it sorts. Normal unless said otherwise.","anyOf":[{"type":"string","enum":["normal","high"]},{"type":"null"}]},"body":{"description":"What it is about.","type":["string","null"]},"title":{"description":"What to call the issue.","type":"string"}},"required":["title"],"additionalProperties":false}|}
          (schema "addIssue" Project.Actions.creators));
    case "createProject advertises one required field and two optional ones" (fun () ->
        Alcotest.(check string)
          ""
          {|{"type":"object","properties":{"body":{"description":"What it is for.","type":["string","null"]},"title":{"description":"What to call it.","type":["string","null"]},"slug":{"description":"The short name the project is addressed by.","type":"string"}},"required":["slug"],"additionalProperties":false}|}
          (schema "createProject" Project.Actions.root));
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
          (Yojson.Safe.from_string (schema "editStatus" Issue.Actions.group)
          |> Yojson.Safe.Util.member "properties"
          |> Yojson.Safe.Util.member "note" |> Yojson.Safe.Util.member "type"
          |> Yojson.Safe.to_string));
    case "the decoder does not" (fun () ->
        let conn, _, issue = fixture () in
        check_bool ""
          (is_invalid
             (dispatch conn issue Issue.Actions.group "editStatus"
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
          (described "editTitle" Issue.Actions.group "title"));
    case "and so does an optional one" (fun () ->
        Alcotest.(check (option string))
          ""
          (Some "Why it moved. Left out, whatever described the old status goes with it.")
          (described "editStatus" Issue.Actions.group "note"));
    case "and an optional one wrapped in anyOf" (fun () ->
        Alcotest.(check (option string))
          "" (Some "How far up the list it sorts. Normal unless said otherwise.")
          (described "addIssue" Project.Actions.creators "priority"));
    case "a field whose type has a schema of its own does not" (fun () ->
        Alcotest.(check (option string))
          "" None
          (described "editStatus" Issue.Actions.group "status");
        Alcotest.(check (option string))
          "" None
          (described "editPriority" Issue.Actions.group "priority"));
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
    ("issue: what execute writes", issue_writes);
    ("project: what is offered", project_offers);
    ("project: what execute writes", project_writes);
    ("the wire", wire);
    ("keys registered against both objects", shared_keys);
    ("the derived schemas", schemas);
    ("where the two derivers disagree", ppx_disagreement);
    ("which doc comments survive", descriptions);
    ("the enums", enums);
    ("the clock", clock);
  ]
