(** The issues table, and the only file that names it.

    The split is the same one {!Wire} makes for JSON: {!Models.t} names no column and no query. What
    [services.mli] adds on top is the reason this file has one at all — it exports the loads and the
    writes, and keeps the row type, the column list and the SQL private. The base table cannot be
    named from outside, so every read is guaranteed to carry its liveness predicate.

    The queries are SQL text. That is one definition of the schema in [schema.sql] and a second in
    the column list below, which is the price of an ORM that does not generate its rows from the
    schema. What it buys is that everything SQLite can say is sayable: [STRICT], [CHECK], a join,
    mixed [ORDER BY] directions, [RETURNING].

    It names the projects table too, in the join and in the liveness predicate. A join is a join;
    what the directory split buys is that no {e project} query lives here, not that the word never
    appears. *)

open Caqti_request.Infix
open Platform

let ( let* ) = Result.bind

let status =
  Db.enum_column ~name:"issue_status" ~to_string:Models.Status.to_string
    ~of_string:Models.Status.of_string ~names:Models.Status.names

(** [priority] is the one column whose SQL representation is not the wire name.
    {!Models.Priority.to_int} is the same pair of values written as a match, which is what makes it
    exhaustive. *)
let priority =
  Caqti_type.custom Caqti_type.int
    ~encode:(fun value -> Ok (Models.Priority.to_int value))
    ~decode:(fun n ->
      match Models.Priority.of_int n with
      | Some value -> Ok value
      | None -> Error (Printf.sprintf "priority: %d is not 0 or 1" n))

(** The issues table joined to its project, so [project_slug] arrives with the row rather than being
    paired up afterwards in OCaml. *)
module Row = struct
  type t = {
    id : int;
    project_id : int;
    project_slug : string;
    title : string;
    body : string;
    status : Models.Status.t;
    priority : Models.Priority.t;
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
      @@ proj status (fun r -> r.status)
      @@ proj priority (fun r -> r.priority)
      @@ proj (option string) (fun r -> r.status_note)
      @@ proj string (fun r -> r.created_at)
      @@ proj string (fun r -> r.updated_at)
      @@ proj (option string) (fun r -> r.deleted_at)
      @@ proj_end)
end

(* --- reading ------------------------------------------------------------- *)

(** High priority first, oldest first within that. Two directions in one clause, in SQL, so nothing
    downstream has to remember to re-sort. *)
let query predicate =
  Printf.sprintf
    "SELECT %s FROM issues i JOIN projects p ON p.id = i.project_id WHERE %s ORDER BY i.priority \
     DESC, i.created_at ASC, i.id ASC"
    Row.columns predicate

let live_of_project =
  Caqti_type.(string ->* Row.ty)
    (query "i.deleted_at IS NULL AND p.deleted_at IS NULL AND p.slug = ?")

let live =
  Caqti_type.(int ->? Row.ty) (query "i.deleted_at IS NULL AND p.deleted_at IS NULL AND i.id = ?")

(** The one read that asks nothing of the project but that it be there. A row just inserted is
    loaded through this, and so is an issue in the trash whose project also went. *)
let by_id = Caqti_type.(int ->? Row.ty) (query "i.id = ?")

let deleted = Caqti_type.(unit ->* Row.ty) (query "i.deleted_at IS NOT NULL")

(** The counts, as one grouped query folded in OCaml.

    It reads the issues table, so it is the issues' to answer even though a project is what displays
    it — see {!Project.Services} for the caller. A [FILTER] clause per status would do it in SQL and
    put the three wire names in a fourth place. The fold is a match instead, which is exhaustive
    over the type. *)
let count_rows =
  Caqti_type.(unit ->* t3 int status int)
    "SELECT project_id, status, COUNT(*) FROM issues WHERE deleted_at IS NULL GROUP BY project_id, \
     status"

let tally rows project_id =
  List.fold_left
    (fun (todo, doing, done_) (id, status, n) ->
      if id <> project_id then (todo, doing, done_)
      else
        match status with
        | Models.Status.Todo -> (todo + n, doing, done_)
        | Models.Status.Doing -> (todo, doing + n, done_)
        | Models.Status.Done -> (todo, doing, done_ + n))
    (0, 0, 0) rows

let counts_by_project conn =
  let* rows = Db.collect conn count_rows () in
  Ok (tally rows)

let of_row (r : Row.t) : Models.t =
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

let list ~project_slug conn : (Models.t list, Db.error) result =
  let* rows = Db.collect conn live_of_project project_slug in
  Ok (List.map of_row rows)

let find ~id conn : (Models.t option, Db.error) result =
  let* row = Db.find_opt conn live id in
  Ok (Option.map of_row row)

let find_any conn id =
  let* row = Db.find_opt conn by_id id in
  Ok (Option.map of_row row)

(** The trash is by the row's own [deleted_at] and nothing else.

    A project going does not put its issues here — it hides them, because {!list} requires a live
    project. That is what makes restoring a project bring back exactly the issues that were not
    deleted in their own right: one row was written on the way out, so one row is cleared on the way
    back. *)
let trashed conn : (Models.t Deleted.t list, Db.error) result =
  let* rows = Db.collect conn deleted () in
  Ok
    (List.filter_map
       (fun (r : Row.t) ->
         Option.map (fun deleted_at -> { Deleted.inner = of_row r; deleted_at }) r.deleted_at)
       rows)

(* --- writing ------------------------------------------------------------- *)

let update_row =
  Caqti_type.(t7 string string status priority (option string) string int ->. unit)
    "UPDATE issues SET title = ?, body = ?, status = ?, priority = ?, status_note = ?, updated_at \
     = ? WHERE id = ?"

let stamp_deleted =
  Caqti_type.(t3 string string int ->. unit)
    "UPDATE issues SET deleted_at = ?, updated_at = ? WHERE id = ?"

let clear_deleted =
  Caqti_type.(t2 string int ->. unit)
    "UPDATE issues SET deleted_at = NULL, updated_at = ? WHERE id = ?"

let insert =
  Caqti_type.(t7 int string string status priority string string ->! int)
    "INSERT INTO issues (project_id, title, body, status, priority, created_at, updated_at) VALUES \
     (?, ?, ?, ?, ?, ?, ?) RETURNING id"

(** Persisting what an action returned. {!Action.run} produced the new value; this only writes it,
    so the enforcement point stays where {!Action} put it.

    [updated_at] is stamped here rather than by the action, which keeps every [execute] a pure
    function of the object and its payload — the thing that lets the whole action layer be tested
    with no clock and no database. *)
let update (issue : Models.t) conn : (unit, Db.error) result =
  Db.exec conn update_row
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
let delete (issue : Models.t) conn : (unit, Db.error) result =
  let stamp = Clock.now () in
  Db.exec conn stamp_deleted (stamp, stamp, issue.id)

let restore (deleted : Models.t Deleted.t) conn : (unit, Db.error) result =
  Db.exec conn clear_deleted (Clock.now (), deleted.inner.id)

let create (draft : Models.draft) conn : (Models.t, Db.error) result =
  let stamp = Clock.now () in
  let* id =
    Db.find conn insert
      (draft.project_id, draft.title, draft.body, Models.Status.Todo, draft.priority, stamp, stamp)
  in
  Db.created "the issue" find_any conn id
