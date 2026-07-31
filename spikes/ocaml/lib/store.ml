(** The SQL edge. Everything that knows about a database is here.

    The split is the same one {!Wire} makes for JSON: {!Issue.t} and {!Project.t} name no column and
    no query. What [store.mli] adds on top is the reason this file has one at all — it exports the
    loads and the writes, and keeps the row types, the column lists and the SQL private. The base
    tables cannot be named from outside, so every read is guaranteed to carry its liveness
    predicate.

    The queries are SQL text. That is one definition of the schema in [schema.sql] and a second in
    the column lists below, which is the price of an ORM that does not generate its rows from the
    schema — the same two definitions the Rust spike has. What it buys is that everything SQLite can
    say is sayable: [STRICT], [CHECK], a partial unique index, a join, mixed [ORDER BY] directions,
    [RETURNING].

    Blocking throughout. SQLite has no waiting in it, so a promise would be ceremony with nothing
    behind it. *)

open Caqti_request.Infix

type conn = (module Caqti_blocking.CONNECTION)
type error = Query_failed of string | Missing_after_insert of string

let show_error = function
  | Query_failed message -> message
  | Missing_after_insert what -> what ^ " vanished between the insert and the read"

let broken result = Result.map_error (fun e -> Error.Broken (show_error e)) result

(* Caqti's error is an open polymorphic variant spread over four constructors.
   It is narrowed to one closed type here, at the boundary, rather than being
   let out into the rest of the tree as a row type a caller has to match with
   [#Caqti_error.t as e]. *)
let failed e = Query_failed (Caqti_error.show e)
let ( let* ) = Result.bind

let exec (conn : conn) request params =
  let (module Db : Caqti_blocking.CONNECTION) = conn in
  Result.map_error failed (Db.exec request params)

let find (conn : conn) request params =
  let (module Db : Caqti_blocking.CONNECTION) = conn in
  Result.map_error failed (Db.find request params)

let find_opt (conn : conn) request params =
  let (module Db : Caqti_blocking.CONNECTION) = conn in
  Result.map_error failed (Db.find_opt request params)

let collect (conn : conn) request params =
  let (module Db : Caqti_blocking.CONNECTION) = conn in
  Result.map_error failed (Db.collect_list request params)

(* --- the schema ---------------------------------------------------------- *)

(** [PRAGMA foreign_keys] is per connection rather than per database, so it is not in [schema.sql]
    and cannot be. *)
let foreign_keys = Caqti_type.(unit ->. unit) ~oneshot:true "PRAGMA foreign_keys = ON"

let connect uri : (conn, error) result =
  match Caqti_blocking.connect (Uri.of_string uri) with
  | Error e -> Error (failed e)
  | Ok conn -> Result.map (fun () -> conn) (exec conn foreign_keys ())

(** One statement per request is Caqti's rule, so the file is split on its statement terminator. A
    [;] inside a comment would break this, which is why there is not one. *)
let statements sql =
  String.split_on_char ';' sql |> List.map String.trim |> List.filter (fun s -> s <> "")

let initialise conn : (unit, error) result =
  List.fold_left
    (fun acc sql ->
      let* () = acc in
      exec conn (Caqti_type.(unit ->. unit) ~oneshot:true sql) ())
    (Ok ()) (statements Schema.ddl)

(* --- column types -------------------------------------------------------- *)

(** An enum's SQL type, from the same two matches its decoder and its schema are built from.
    [decode] is where a value the [CHECK] constraint would have prevented arrives; it cannot be
    anything but a read failure. *)
let enum_column ~name ~to_string ~of_string ~names =
  Caqti_type.enum ~encode:to_string
    ~decode:(fun wire ->
      match of_string wire with
      | Some value -> Ok value
      | None ->
          Error (Printf.sprintf "%s: %S is not one of %s" name wire (String.concat ", " names)))
    name

let project_status =
  enum_column ~name:"project_status" ~to_string:Project.Status.to_string
    ~of_string:Project.Status.of_string ~names:Project.Status.names

let issue_status =
  enum_column ~name:"issue_status" ~to_string:Issue.Status.to_string
    ~of_string:Issue.Status.of_string ~names:Issue.Status.names

(** [priority] is the one column whose SQL representation is not the wire name.
    {!Issue.Priority.to_int} is the same pair of values written as a match, which is what makes it
    exhaustive. *)
let priority =
  Caqti_type.custom Caqti_type.int
    ~encode:(fun value -> Ok (Issue.Priority.to_int value))
    ~decode:(fun n ->
      match Issue.Priority.of_int n with
      | Some value -> Ok value
      | None -> Error (Printf.sprintf "priority: %d is not 0 or 1" n))

(* --- rows ---------------------------------------------------------------- *)

(** The projects table as one row.

    Not {!Project.t}: it carries [deleted_at], which no live projection has, and not the counts,
    which are a second query. {!Caqti_type.product} builds this record directly, so each column
    names the field it fills — but the constructor's parameters are still positional, and swapping
    two same-typed columns between [columns] and the projections below compiles clean. That is the
    residue of the tuple decoder rather than its removal. *)
module Project_row = struct
  type t = {
    id : int;
    slug : string;
    title : string;
    body : string;
    status : Project.Status.t;
    created_at : string;
    updated_at : string;
    deleted_at : string option;
  }

  let columns = "id, slug, title, body, status, created_at, updated_at, deleted_at"

  let ty =
    Caqti_type.(
      product (fun id slug title body status created_at updated_at deleted_at ->
          { id; slug; title; body; status; created_at; updated_at; deleted_at })
      @@ proj int (fun r -> r.id)
      @@ proj string (fun r -> r.slug)
      @@ proj string (fun r -> r.title)
      @@ proj string (fun r -> r.body)
      @@ proj project_status (fun r -> r.status)
      @@ proj string (fun r -> r.created_at)
      @@ proj string (fun r -> r.updated_at)
      @@ proj (option string) (fun r -> r.deleted_at)
      @@ proj_end)
end

(** The issues table joined to its project, so [project_slug] arrives with the row rather than being
    paired up afterwards in OCaml. *)
module Issue_row = struct
  type t = {
    id : int;
    project_id : int;
    project_slug : string;
    title : string;
    body : string;
    status : Issue.Status.t;
    priority : Issue.Priority.t;
    status_note : string option;
    created_at : string;
    updated_at : string;
    deleted_at : string option;
  }

  let columns =
    "i.id, i.project_id, p.slug, i.title, i.body, i.status, i.priority, i.status_note, \
     i.created_at, i.updated_at, i.deleted_at"

  let ty =
    Caqti_type.(
      product
        (fun
          id
          project_id
          project_slug
          title
          body
          status
          priority
          status_note
          created_at
          updated_at
          deleted_at
        ->
          {
            id;
            project_id;
            project_slug;
            title;
            body;
            status;
            priority;
            status_note;
            created_at;
            updated_at;
            deleted_at;
          })
      @@ proj int (fun r -> r.id)
      @@ proj int (fun r -> r.project_id)
      @@ proj string (fun r -> r.project_slug)
      @@ proj string (fun r -> r.title)
      @@ proj string (fun r -> r.body)
      @@ proj issue_status (fun r -> r.status)
      @@ proj priority (fun r -> r.priority)
      @@ proj (option string) (fun r -> r.status_note)
      @@ proj string (fun r -> r.created_at)
      @@ proj string (fun r -> r.updated_at)
      @@ proj (option string) (fun r -> r.deleted_at)
      @@ proj_end)
end

(* --- reading ------------------------------------------------------------- *)

(** [created_at] is a second, so it is not a total order — two rows written in the same second tie,
    and SQLite is then free to return them either way round. The id breaks the tie. *)
let projects_query predicate =
  Printf.sprintf "SELECT %s FROM projects WHERE %s ORDER BY created_at, id" Project_row.columns
    predicate

let live_projects = Caqti_type.(unit ->* Project_row.ty) (projects_query "deleted_at IS NULL")

let live_project_by_slug =
  Caqti_type.(string ->? Project_row.ty) (projects_query "deleted_at IS NULL AND slug = ?")

let live_project_by_id =
  Caqti_type.(int ->? Project_row.ty) (projects_query "deleted_at IS NULL AND id = ?")

let deleted_projects =
  Caqti_type.(unit ->* Project_row.ty) (projects_query "deleted_at IS NOT NULL")

(** High priority first, oldest first within that. Two directions in one clause, in SQL, so nothing
    downstream has to remember to re-sort. *)
let issues_query predicate =
  Printf.sprintf
    "SELECT %s FROM issues i JOIN projects p ON p.id = i.project_id WHERE %s ORDER BY i.priority \
     DESC, i.created_at ASC, i.id ASC"
    Issue_row.columns predicate

let live_issues_of_project =
  Caqti_type.(string ->* Issue_row.ty)
    (issues_query "i.deleted_at IS NULL AND p.deleted_at IS NULL AND p.slug = ?")

let live_issue =
  Caqti_type.(int ->? Issue_row.ty)
    (issues_query "i.deleted_at IS NULL AND p.deleted_at IS NULL AND i.id = ?")

(** The one read that asks nothing of the project but that it be there. A row just inserted is
    loaded through this, and so is an issue in the trash whose project also went. *)
let issue_by_id = Caqti_type.(int ->? Issue_row.ty) (issues_query "i.id = ?")

let deleted_issues = Caqti_type.(unit ->* Issue_row.ty) (issues_query "i.deleted_at IS NOT NULL")

(** The counts, as one grouped query folded in OCaml.

    A [FILTER] clause per status would do it in SQL and put the three wire names in a fourth place.
    The fold is a match instead, which is exhaustive over the type. *)
let count_rows =
  Caqti_type.(unit ->* t3 int issue_status int)
    "SELECT project_id, status, COUNT(*) FROM issues WHERE deleted_at IS NULL GROUP BY project_id, \
     status"

let tally rows project_id =
  List.fold_left
    (fun (todo, doing, done_) (id, status, n) ->
      if id <> project_id then (todo, doing, done_)
      else
        match status with
        | Issue.Status.Todo -> (todo + n, doing, done_)
        | Issue.Status.Doing -> (todo, doing + n, done_)
        | Issue.Status.Done -> (todo, doing, done_ + n))
    (0, 0, 0) rows

let to_project counts (r : Project_row.t) : Project.t =
  let todo, doing, done_ = counts r.id in
  {
    id = r.id;
    slug = r.slug;
    title = r.title;
    body = r.body;
    status = r.status;
    todo;
    doing;
    done_;
    created_at = r.created_at;
    updated_at = r.updated_at;
  }

let to_issue (r : Issue_row.t) : Issue.t =
  {
    id = r.id;
    project_id = r.project_id;
    project_slug = r.project_slug;
    title = r.title;
    body = r.body;
    status = r.status;
    priority = r.priority;
    status_note = r.status_note;
    created_at = r.created_at;
    updated_at = r.updated_at;
  }

let counted conn =
  let* rows = collect conn count_rows () in
  Ok (tally rows)

let projects conn : (Project.t list, error) result =
  let* rows = collect conn live_projects () in
  let* counts = counted conn in
  Ok (List.map (to_project counts) rows)

let one_project conn request params =
  let* row = find_opt conn request params in
  match row with
  | None -> Ok None
  | Some row ->
      let* counts = counted conn in
      Ok (Some (to_project counts row))

let project ~slug conn : (Project.t option, error) result =
  one_project conn live_project_by_slug slug

let project_by_id id conn = one_project conn live_project_by_id id

let issues ~project_slug conn : (Issue.t list, error) result =
  let* rows = collect conn live_issues_of_project project_slug in
  Ok (List.map to_issue rows)

let issue ~id conn : (Issue.t option, error) result =
  let* row = find_opt conn live_issue id in
  Ok (Option.map to_issue row)

let issue_row conn id =
  let* row = find_opt conn issue_by_id id in
  Ok (Option.map to_issue row)

(** The trash is by the row's own [deleted_at] and nothing else.

    A project going does not put its issues here — it hides them, because {!issues} requires a live
    project. That is what makes restoring a project bring back exactly the issues that were not
    deleted in their own right: one row was written on the way out, so one row is cleared on the way
    back. *)
let trashed_projects conn : (Project.t Deleted.t list, error) result =
  let* rows = collect conn deleted_projects () in
  let* counts = counted conn in
  Ok
    (List.filter_map
       (fun (r : Project_row.t) ->
         Option.map
           (fun deleted_at -> { Deleted.inner = to_project counts r; deleted_at })
           r.deleted_at)
       rows)

let trashed_issues conn : (Issue.t Deleted.t list, error) result =
  let* rows = collect conn deleted_issues () in
  Ok
    (List.filter_map
       (fun (r : Issue_row.t) ->
         Option.map (fun deleted_at -> { Deleted.inner = to_issue r; deleted_at }) r.deleted_at)
       rows)

let emitted =
  Caqti_type.(unit ->* string) "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name"

let emitted_ddl conn : (string list, error) result = collect conn emitted ()

(* --- writing ------------------------------------------------------------- *)

let update_project_row =
  Caqti_type.(t5 string string project_status string int ->. unit)
    "UPDATE projects SET title = ?, body = ?, status = ?, updated_at = ? WHERE id = ?"

let update_issue_row =
  Caqti_type.(t7 string string issue_status priority (option string) string int ->. unit)
    "UPDATE issues SET title = ?, body = ?, status = ?, priority = ?, status_note = ?, updated_at \
     = ? WHERE id = ?"

let stamp_project_deleted =
  Caqti_type.(t3 string string int ->. unit)
    "UPDATE projects SET deleted_at = ?, updated_at = ? WHERE id = ?"

let stamp_issue_deleted =
  Caqti_type.(t3 string string int ->. unit)
    "UPDATE issues SET deleted_at = ?, updated_at = ? WHERE id = ?"

let clear_project_deleted =
  Caqti_type.(t2 string int ->. unit)
    "UPDATE projects SET deleted_at = NULL, updated_at = ? WHERE id = ?"

let clear_issue_deleted =
  Caqti_type.(t2 string int ->. unit)
    "UPDATE issues SET deleted_at = NULL, updated_at = ? WHERE id = ?"

let insert_project =
  Caqti_type.(t6 string string string project_status string string ->! int)
    "INSERT INTO projects (slug, title, body, status, created_at, updated_at) VALUES (?, ?, ?, ?, \
     ?, ?) RETURNING id"

let insert_issue =
  Caqti_type.(t7 int string string issue_status priority string string ->! int)
    "INSERT INTO issues (project_id, title, body, status, priority, created_at, updated_at) VALUES \
     (?, ?, ?, ?, ?, ?, ?) RETURNING id"

(** Persisting what an action returned. {!Action.run} produced the new value; this only writes it,
    so the enforcement point stays where {!Action} put it.

    [updated_at] is stamped here rather than by the action, which keeps every [execute] a pure
    function of the object and its payload — the thing that lets the whole action layer be tested
    with no clock and no database. *)
let update_project (project : Project.t) conn : (unit, error) result =
  exec conn update_project_row
    (project.title, project.body, project.status, Clock.now (), project.id)

let update_issue (issue : Issue.t) conn : (unit, error) result =
  exec conn update_issue_row
    ( issue.title,
      issue.body,
      issue.status,
      issue.priority,
      issue.status_note,
      Clock.now (),
      issue.id )

(** The soft delete, which is an update like any other — which is exactly why the type of what an
    action returned cannot say which of these to call, and why the group it was registered in has
    to. *)
let delete_project (project : Project.t) conn : (unit, error) result =
  let stamp = Clock.now () in
  exec conn stamp_project_deleted (stamp, stamp, project.id)

let delete_issue (issue : Issue.t) conn : (unit, error) result =
  let stamp = Clock.now () in
  exec conn stamp_issue_deleted (stamp, stamp, issue.id)

let restore_project (deleted : Project.t Deleted.t) conn : (unit, error) result =
  exec conn clear_project_deleted (Clock.now (), deleted.inner.id)

let restore_issue (deleted : Issue.t Deleted.t) conn : (unit, error) result =
  exec conn clear_issue_deleted (Clock.now (), deleted.inner.id)

(** Creation: insert, take the id back from [RETURNING], load the row. The load is what makes the
    returned object the stored one rather than a hopeful copy of the draft — a project carries
    counts and an issue carries its project's slug, and neither is on the row that was written. *)
let created what load conn id =
  let* found = load conn id in
  match found with Some row -> Ok row | None -> Error (Missing_after_insert what)

let create_project (draft : Project.draft) conn : (Project.t, error) result =
  let stamp = Clock.now () in
  let* id =
    find conn insert_project
      (draft.slug, draft.title, draft.body, Project.Status.Active, stamp, stamp)
  in
  created "the project" (fun conn id -> project_by_id id conn) conn id

let create_issue (draft : Issue.draft) conn : (Issue.t, error) result =
  let stamp = Clock.now () in
  let* id =
    find conn insert_issue
      (draft.project_id, draft.title, draft.body, Issue.Status.Todo, draft.priority, stamp, stamp)
  in
  created "the issue" issue_row conn id
