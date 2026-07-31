(** The loads and the writes, and nothing else.

    This file is the point of the module. The row type, the column list, the liveness predicates and
    the SQL are all private, so nothing outside [services.ml] can name the projects table — and
    every read below therefore carries its soft-delete filter by construction. A caller cannot write
    the query that forgets it, because it cannot write a query at all.

    Every read is a fresh query. Nothing is cached, so what a frontend rendered is a snapshot and a
    write is checked against the row as it is now. *)

open Platform

(** {1 Reading} *)

val list : Db.conn -> (Models.t list, Db.error) result
(** Live projects in creation order, each carrying its issue counts. The counts are what {!Actions}'
    refusals read, so a project loaded any other way would offer a menu it could not justify. *)

val find : slug:string -> Db.conn -> (Models.t option, Db.error) result
val trashed : Db.conn -> (Models.t Deleted.t list, Db.error) result

(** {1 Writing}

    One call per action group, named once where the group is registered — see {!Wire.group}. Nothing
    here can tell {!delete} from {!update}: a soft delete is an update, and both take a {!Models.t}.
    What keeps them apart is that each is named exactly once. *)

val create : Models.draft -> Db.conn -> (Models.t, Db.error) result
(** Inserts with the id omitted, takes the id back from [RETURNING], and loads the row through the
    same projection every other read uses — so what comes out is the stored object rather than a
    hopeful copy of the draft. *)

val update : Models.t -> Db.conn -> (unit, Db.error) result
(** Writes the editable columns and stamps [updated_at]. The stamp is taken here rather than by the
    action, which is what keeps every [execute] a pure function and the whole action layer testable
    with no clock.

    [slug] is not written by anything: no action changes it, and the partial unique index it sits
    under is the last thing an edit should be able to trip. *)

val delete : Models.t -> Db.conn -> (unit, Db.error) result
val restore : Models.t Deleted.t -> Db.conn -> (unit, Db.error) result
