(** Everything a project can be asked, and the two things that make one.

    This is the half of the domain that has preconditions. Every issue action is always runnable; a
    project refuses three things, and all three refusals read something that is on the object only
    because the store put it there. A hook earns its keep where an action is a verb with a
    precondition, and a CRUD-shaped edit is not one of those. Creating something still is.

    A creator is an ordinary action whose [out] is not its [obj]: [addIssue] is offered against a
    project and returns an {!Issue.draft}. Nothing about the declaration marks it as special, and
    nothing has to — the group it is registered in is what INSERTs the result.

    See [issue_actions.ml] for why each [Spec] is named. *)

open Ppx_yojson_conv_lib.Yojson_conv.Primitives

module Edit_title = struct
  module Spec = struct
    include Action.Defaults

    type obj = Project.t
    type out = Project.t

    type payload = { title : string  (** What to call the project. *) }
    [@@deriving yojson, jsonschema ~ocaml_doc]

    let key = "editTitle"

    let execute (project : obj) p =
      let title = String.trim p.title in
      if title = "" then Error (Error.Invalid "title is required") else Ok { project with title }
  end

  include Wire.Make (Spec)
end

module Edit_body = struct
  module Spec = struct
    include Action.Defaults

    type obj = Project.t
    type out = Project.t

    type payload = { body : string  (** What the project is for. Blank clears it. *) }
    [@@deriving yojson, jsonschema ~ocaml_doc]

    let key = "editBody"
    let execute (project : obj) p = Ok { project with body = p.body }
  end

  include Wire.Make (Spec)
end

let issues n = if n = 1 then "1 issue" else Printf.sprintf "%d issues" n

module Edit_status = struct
  module Spec = struct
    include Action.Defaults

    type obj = Project.t
    type out = Project.t

    type payload = {
      status : Project.Status.t;  (** Whether the project is still being worked on. *)
    }
    [@@deriving yojson, jsonschema ~ocaml_doc]

    let key = "editStatus"

    (* The refusal is stated against the object rather than against the payload,
       so it holds whichever status was asked for — archiving is the only move
       that could break it, and asking to stay active is refused too. That is
       what a hook seeing only the object costs, and the alternative is a rule
       split between [is_disabled] and [execute]. *)
    let is_disabled (project : obj) =
      if project.doing > 0 then
        Some (Printf.sprintf "finish or drop %s first" (issues project.doing))
      else None

    let execute (project : obj) p = Ok { project with status = p.status }
  end

  include Wire.Make (Spec)
end

module Delete = struct
  module Spec = struct
    include Action.Defaults
    include Wire.No_payload

    type obj = Project.t
    type out = Project.t

    let key = "delete"

    let is_disabled (project : obj) =
      match project.status with
      | Project.Status.Active -> Some "archive it first"
      | Project.Status.Archived -> None

    let execute project () = Ok project
  end

  include Wire.Make (Spec)
end

module Restore = struct
  module Spec = struct
    include Action.Defaults
    include Wire.No_payload

    type obj = Project.restorable
    type out = Project.t Deleted.t

    let key = "restore"

    (* The one refusal a hook can state only because its object was widened to
       carry the answer. The partial unique index is still what guarantees it;
       this is what turns a constraint violation into a sentence. *)
    let is_disabled (r : obj) =
      if r.slug_taken then Some (Printf.sprintf "project %S exists again" r.deleted.inner.slug)
      else None

    let execute (r : obj) () = Ok r.deleted
  end

  include Wire.Make (Spec)
end

module Add_issue = struct
  module Spec = struct
    include Action.Defaults

    type obj = Project.t
    type out = Issue.draft

    type payload = {
      title : string;  (** What to call the issue. *)
      body : string option; [@yojson.option] [@jsonschema.option]  (** What it is about. *)
      priority : Issue.Priority.t option; [@yojson.option] [@jsonschema.option]
          (** How far up the list it sorts. Normal unless said otherwise. *)
    }
    [@@deriving yojson, jsonschema ~ocaml_doc]

    let key = "addIssue"

    let is_disabled (project : obj) =
      match project.status with
      | Project.Status.Archived -> Some "project is archived"
      | Project.Status.Active -> None

    let execute (project : obj) p =
      let title = String.trim p.title in
      if title = "" then Error (Error.Invalid "title is required")
      else
        Ok
          {
            Issue.project_id = project.id;
            title;
            body = Option.value p.body ~default:"";
            priority = Option.value p.priority ~default:Issue.Priority.Normal;
          }
  end

  include Wire.Make (Spec)
end

module Create_project = struct
  module Spec = struct
    include Action.Defaults

    type obj = Project.t list
    type out = Project.draft

    type payload = {
      slug : string;  (** The short name the project is addressed by. *)
      title : string option; [@yojson.option] [@jsonschema.option]  (** What to call it. *)
      body : string option; [@yojson.option] [@jsonschema.option]  (** What it is for. *)
    }
    [@@deriving yojson, jsonschema ~ocaml_doc]

    let key = "createProject"

    (* The duplicate check cannot be a hook, because a hook is given the parent
       and not the payload — it can be told there are projects and not which
       slug is being asked for. So the loaded list is the parent, and the
       refusal comes from [execute]. The partial unique index is still what
       guarantees it; this is only what turns a constraint violation into a
       sentence. *)
    let execute projects p =
      let slug = String.trim p.slug in
      if slug = "" then Error (Error.Invalid "slug is required")
      else if List.exists (fun (project : Project.t) -> project.slug = slug) projects then
        Error (Error.Conflict (Printf.sprintf "project %S already exists" slug))
      else
        Ok
          {
            Project.slug;
            title = Option.value p.title ~default:"";
            body = Option.value p.body ~default:"";
          }
  end

  include Wire.Make (Spec)
end

(** The edits, in the order they are offered. *)
let group : (Project.t, Project.t, Store.conn) Wire.group =
  {
    entries = [ Edit_title.entry; Edit_body.entry; Edit_status.entry ];
    persist =
      (fun conn project ->
        Store.broken (Store.update_project project conn)
        |> Result.map (fun () -> Project.subject project ^ ": saved"));
  }

(** Leaving. Separate from {!group} only because the write that follows is. *)
let trash : (Project.t, Project.t, Store.conn) Wire.group =
  {
    entries = [ Delete.entry ];
    persist =
      (fun conn project ->
        Store.broken (Store.delete_project project conn)
        |> Result.map (fun () -> Project.subject project ^ ": deleted"));
  }

(** What a row in the trash offers, which is coming back and nothing else. *)
let deleted_group : (Project.restorable, Project.t Deleted.t, Store.conn) Wire.group =
  {
    entries = [ Restore.entry ];
    persist =
      (fun conn deleted ->
        Store.broken (Store.restore_project deleted conn)
        |> Result.map (fun () -> Project.subject deleted.inner ^ ": restored"));
  }

(** What a project can make. The result is a draft rather than an issue: an action produces a value
    and the store assigns the id, so the type of what [execute] returns is the type of what has not
    been written yet. *)
let creators : (Project.t, Issue.draft, Store.conn) Wire.group =
  {
    entries = [ Add_issue.entry ];
    persist =
      (fun conn draft ->
        Store.broken (Store.create_issue draft conn)
        |> Result.map (fun (issue : Issue.t) -> Issue.subject issue ^ ": created"));
  }

(** The one action with no object to address. Its parent is the list of live projects, which is what
    a uniqueness refusal has to read. *)
let root : (Project.t list, Project.draft, Store.conn) Wire.group =
  {
    entries = [ Create_project.entry ];
    persist =
      (fun conn draft ->
        Store.broken (Store.create_project draft conn)
        |> Result.map (fun (project : Project.t) -> Project.subject project ^ ": created"));
  }
