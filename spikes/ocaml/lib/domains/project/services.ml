(** The projects table, and the only file that names it.

    The split is the same one {!Wire} makes for JSON: {!Models.t} names no column and no query. What
    [services.mli] adds on top is the reason this file has one at all — it exports the loads and the
    writes, and keeps the row type, the column list and the SQL private. The base table cannot be
    named from outside, so every read is guaranteed to carry its liveness predicate.

    The counts come from {!Issue.Services.counts_by_project} rather than from a query here. They
    read the issues table, and the only rule that makes this split mean anything is that a domain's
    services are the sole namer of its own table. *)

open Caqti_request.Infix
open Platform

let ( let* ) = Result.bind

let status =
  Db.enum_column ~name:"project_status" ~to_string:Models.Status.to_string
    ~of_string:Models.Status.of_string ~names:Models.Status.names

(** The projects table as one row.

    Not {!Models.t}: it carries [deleted_at], which no live projection has, and not the counts,
    which are a second query. {!Caqti_type.product} builds this record directly, so each column
    names the field it fills — but the constructor's parameters are still positional, and swapping
    two same-typed columns between [columns] and the projections below compiles clean. That is the
    residue of the tuple decoder rather than its removal. *)
module Row = struct
  type t = {
    id : int;
    slug : string;
    title : string;
    body : string;
    status : Models.Status.t;
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
      @@ proj status (fun r -> r.status)
      @@ proj string (fun r -> r.created_at)
      @@ proj string (fun r -> r.updated_at)
      @@ proj (option string) (fun r -> r.deleted_at)
      @@ proj_end)
end

(* --- reading ------------------------------------------------------------- *)

(** [created_at] is a second, so it is not a total order — two rows written in the same second tie,
    and SQLite is then free to return them either way round. The id breaks the tie. *)
let query predicate =
  Printf.sprintf "SELECT %s FROM projects WHERE %s ORDER BY created_at, id" Row.columns predicate

let live = Caqti_type.(unit ->* Row.ty) (query "deleted_at IS NULL")
let live_by_slug = Caqti_type.(string ->? Row.ty) (query "deleted_at IS NULL AND slug = ?")
let live_by_id = Caqti_type.(int ->? Row.ty) (query "deleted_at IS NULL AND id = ?")
let deleted = Caqti_type.(unit ->* Row.ty) (query "deleted_at IS NOT NULL")

let of_row counts (r : Row.t) : Models.t =
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

let list conn : (Models.t list, Db.error) result =
  let* rows = Db.collect conn live () in
  let* counts = Issue.Services.counts_by_project conn in
  Ok (List.map (of_row counts) rows)

let one conn request params =
  let* row = Db.find_opt conn request params in
  match row with
  | None -> Ok None
  | Some row ->
      let* counts = Issue.Services.counts_by_project conn in
      Ok (Some (of_row counts row))

let find ~slug conn : (Models.t option, Db.error) result = one conn live_by_slug slug
let find_by_id id conn = one conn live_by_id id

(** The trash is by the row's own [deleted_at] and nothing else.

    A project going does not put its issues in the issue trash — it hides them, because
    {!Issue.Services.list} requires a live project. That is what makes restoring a project bring
    back exactly the issues that were not deleted in their own right: one row was written on the way
    out, so one row is cleared on the way back. *)
let trashed conn : (Models.t Deleted.t list, Db.error) result =
  let* rows = Db.collect conn deleted () in
  let* counts = Issue.Services.counts_by_project conn in
  Ok
    (List.filter_map
       (fun (r : Row.t) ->
         Option.map (fun deleted_at -> { Deleted.inner = of_row counts r; deleted_at }) r.deleted_at)
       rows)

(* --- writing ------------------------------------------------------------- *)

let update_row =
  Caqti_type.(t5 string string status string int ->. unit)
    "UPDATE projects SET title = ?, body = ?, status = ?, updated_at = ? WHERE id = ?"

let stamp_deleted =
  Caqti_type.(t3 string string int ->. unit)
    "UPDATE projects SET deleted_at = ?, updated_at = ? WHERE id = ?"

let clear_deleted =
  Caqti_type.(t2 string int ->. unit)
    "UPDATE projects SET deleted_at = NULL, updated_at = ? WHERE id = ?"

let insert =
  Caqti_type.(t6 string string string status string string ->! int)
    "INSERT INTO projects (slug, title, body, status, created_at, updated_at) VALUES (?, ?, ?, ?, \
     ?, ?) RETURNING id"

(** Persisting what an action returned. {!Action.run} produced the new value; this only writes it,
    so the enforcement point stays where {!Action} put it.

    [updated_at] is stamped here rather than by the action, which keeps every [execute] a pure
    function of the object and its payload — the thing that lets the whole action layer be tested
    with no clock and no database. *)
let update (project : Models.t) conn : (unit, Db.error) result =
  Db.exec conn update_row (project.title, project.body, project.status, Clock.now (), project.id)

(** The soft delete, which is an update like any other — which is exactly why the type of what an
    action returned cannot say which of these to call, and why the group it was registered in has
    to. *)
let delete (project : Models.t) conn : (unit, Db.error) result =
  let stamp = Clock.now () in
  Db.exec conn stamp_deleted (stamp, stamp, project.id)

let restore (deleted : Models.t Deleted.t) conn : (unit, Db.error) result =
  Db.exec conn clear_deleted (Clock.now (), deleted.inner.id)

let create (draft : Models.draft) conn : (Models.t, Db.error) result =
  let stamp = Clock.now () in
  let* id =
    Db.find conn insert (draft.slug, draft.title, draft.body, Models.Status.Active, stamp, stamp)
  in
  Db.created "the project" (fun conn id -> find_by_id id conn) conn id
