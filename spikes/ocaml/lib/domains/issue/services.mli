(** The loads and the writes, and nothing else.

    This file is the point of the module. The row type, the column list, the liveness predicates and
    the SQL are all private, so nothing outside [services.ml] can name the issues table — and every
    read below therefore carries its soft-delete filter by construction. A caller cannot write the
    query that forgets it, because it cannot write a query at all.

    That is the same trick [action.mli] uses to make {!Action.run} the only path to a write, applied
    to the soft-delete filter instead. It is also what stands in for SQL views: {!list} is "live
    issues of live projects" as a function rather than as a view, and it is the only way to ask for
    issues.

    Every read is a fresh query. Nothing is cached, so what a frontend rendered is a snapshot and a
    write is checked against the row as it is now. *)

open Platform

(** {1 Reading} *)

val list : project_slug:string -> Db.conn -> (Models.t list, Db.error) result
(** Live issues of a live project, high priority first and oldest first within that —
    [ORDER BY priority DESC, created_at ASC], in SQL.

    Liveness is derived rather than stored: this requires both the issue's and the project's
    [deleted_at] to be null, so soft-deleting a project hides its issues with one row written and
    restoring it brings back exactly the issues that were not deleted in their own right. *)

val find : id:int -> Db.conn -> (Models.t option, Db.error) result
(** The same liveness rule as {!list}: an issue of a deleted project is not found here either. *)

val trashed : Db.conn -> (Models.t Deleted.t list, Db.error) result

val counts_by_project : Db.conn -> (int -> int * int * int, Db.error) result
(** [todo, doing, done] per project id, over live issues. It reads the issues table, so it is stated
    here even though {!Project.Services} is what displays the result — the counts are what
    {!Project.Actions}' refusals read, and a project loaded without them would offer a menu it could
    not justify. *)

(** {1 Writing}

    One call per action group, named once where the group is registered — see {!Wire.group}. Nothing
    here can tell {!delete} from {!update}: a soft delete is an update, and both take a {!Models.t}.
    What keeps them apart is that each is named exactly once. *)

val create : Models.draft -> Db.conn -> (Models.t, Db.error) result
(** Inserts with the id omitted, takes the id back from [RETURNING], and loads the row through the
    same projection every other read uses — so what comes out is the stored object rather than a
    hopeful copy of the draft. The load is the one that asks nothing of the project but that it be
    there, so creating an issue under a project works regardless of how the project reads. *)

val update : Models.t -> Db.conn -> (unit, Db.error) result
(** Writes the editable columns and stamps [updated_at]. The stamp is taken here rather than by the
    action, which is what keeps every [execute] a pure function and the whole action layer testable
    with no clock.

    [project_id] is not written: an issue does not move between projects. *)

val delete : Models.t -> Db.conn -> (unit, Db.error) result
val restore : Models.t Deleted.t -> Db.conn -> (unit, Db.error) result
