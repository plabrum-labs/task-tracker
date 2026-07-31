(** Everything a project can be asked, and the two things that make one.

    This is the half of the domain that has preconditions. Every issue action is always runnable; a
    project refuses three things, and all three refusals read something that is on the object only
    because the store put it there. A hook earns its keep where an action is a verb with a
    precondition, and a CRUD-shaped edit is not one of those. Creating something still is.

    One action is one [%action …]; its payload shape is in {!Schemas}. A creator is an ordinary
    action whose [execute] writes a different table — [addIssue] inserts an issue, [createProject] a
    project — and nothing about the declaration marks either as special, because [execute] holds the
    transaction and what it writes is its own business.

    See [../issue/actions.ml] for the shape of an [%action] declaration. *)

open Platform

(** The editable columns, written and reported as one. [updated_at] is the store's to stamp. *)
let saved (project : Models.t) conn =
  Db.broken (Services.update project conn)
  |> Result.map (fun () -> Models.subject project ^ ": saved")

let issues n = if n = 1 then "1 issue" else Printf.sprintf "%d issues" n

module Edit_title =
  [%action
  include Action.Defaults

  type obj = Models.t
  type payload = Schemas.edit_title

  let key = "editTitle"

  let execute (project : obj) (p : payload) conn =
    let title = String.trim p.title in
    if title = "" then Error (Error.Invalid "title is required")
    else saved { project with title } conn]

module Edit_body =
  [%action
  include Action.Defaults

  type obj = Models.t
  type payload = Schemas.edit_body

  let key = "editBody"
  let execute (project : obj) (p : payload) conn = saved { project with body = p.body } conn]

module Edit_status =
  [%action
  include Action.Defaults

  type obj = Models.t
  type payload = Schemas.edit_status

  let key = "editStatus"

  (* The refusal is stated against the object rather than against the payload, so it holds whichever
   status was asked for — archiving is the only move that could break it, and asking to stay active
   is refused too. That is what a hook seeing only the object costs, and the alternative is a rule
   split between [is_disabled] and [execute]. *)
  let is_disabled (project : obj) =
    if project.doing > 0 then Some (Printf.sprintf "finish or drop %s first" (issues project.doing))
    else None

  let execute (project : obj) (p : payload) conn = saved { project with status = p.status } conn]

module Delete =
  [%action
  include Action.Defaults
  include Wire.No_payload

  type obj = Models.t

  let key = "delete"

  let is_disabled (project : obj) =
    match project.status with
    | Models.Status.Active -> Some "archive it first"
    | Models.Status.Archived -> None

  let execute project () conn =
    Db.broken (Services.delete project conn)
    |> Result.map (fun () -> Models.subject project ^ ": deleted")]

module Restore =
  [%action
  include Action.Defaults
  include Wire.No_payload

  type obj = Models.restorable

  let key = "restore"

  (* The one refusal a hook can state only because its object was widened to carry the answer. The
   partial unique index is still what guarantees it; this is what turns a constraint violation into
   a sentence. *)
  let is_disabled (r : obj) =
    if r.slug_taken then Some (Printf.sprintf "project %S exists again" r.deleted.inner.slug)
    else None

  let execute (r : obj) () conn =
    Db.broken (Services.restore r.deleted conn)
    |> Result.map (fun () -> Models.subject r.deleted.inner ^ ": restored")]

module Add_issue =
  [%action
  include Action.Defaults

  type obj = Models.t
  type payload = Schemas.add_issue

  let key = "addIssue"

  let is_disabled (project : obj) =
    match project.status with
    | Models.Status.Archived -> Some "project is archived"
    | Models.Status.Active -> None

  (* An action on a project writing to the issues table. The store assigns the id, so the message
   reads the row it wrote rather than the draft it built. *)
  let execute (project : obj) (p : payload) conn =
    let title = String.trim p.title in
    if title = "" then Error (Error.Invalid "title is required")
    else
      let draft =
        {
          Issue.project_id = project.id;
          title;
          body = Option.value p.body ~default:"";
          priority = Option.value p.priority ~default:Issue.Priority.Normal;
        }
      in
      Db.broken (Issue.Services.create draft conn)
      |> Result.map (fun (issue : Issue.t) -> Issue.subject issue ^ ": created")]

module Create_project =
  [%action
  include Action.Defaults

  type obj = Models.t list
  type payload = Schemas.create_project

  let key = "createProject"

  (* The duplicate check cannot be a hook, because a hook is given the parent and not the payload — it
   can be told there are projects and not which slug is being asked for. So the loaded list is the
   parent, and the refusal comes from [execute]. The partial unique index is still what guarantees
   it; this is only what turns a constraint violation into a sentence. *)
  let execute projects (p : payload) conn =
    let slug = String.trim p.slug in
    if slug = "" then Error (Error.Invalid "slug is required")
    else if List.exists (fun (project : Models.t) -> project.slug = slug) projects then
      Error (Error.Conflict (Printf.sprintf "project %S already exists" slug))
    else
      let draft =
        {
          Models.slug;
          title = Option.value p.title ~default:"";
          body = Option.value p.body ~default:"";
        }
      in
      Db.broken (Services.create draft conn)
      |> Result.map (fun (project : Models.t) -> Models.subject project ^ ": created")]

(** Everything a live project offers, in the order it is offered: the edits, then leaving, then the
    one thing it makes. [addIssue]'s [execute] writes a different table, and nothing about its place
    in this list says so. *)
let group : Models.t Wire.group =
  [ Edit_title.entry; Edit_body.entry; Edit_status.entry; Delete.entry; Add_issue.entry ]

(** What a row in the trash offers, which is coming back and nothing else. Offered against the
    restorable type, so an edit cannot reach it. *)
let deleted_group : Models.restorable Wire.group = [ Restore.entry ]

(** The one action with no object to address. Its parent is the list of live projects, which is what
    a uniqueness refusal has to read — the one split that is about what availability is checked
    against rather than about what gets written. *)
let root : Models.t list Wire.group = [ Create_project.entry ]
