(** The loads and the writes, and nothing else.

    This file is the point of the module. The row types, the column lists, the liveness predicates
    and the SQL are all private, so nothing outside [store.ml] can name a base table — and every
    read below therefore carries its soft-delete filter by construction. A caller cannot write the
    query that forgets it, because it cannot write a query at all.

    That is the same trick [action.mli] uses to make {!Action.run} the only path to a write, applied
    to the soft-delete filter instead. It is also what stands in for SQL views: {!issues} is "live
    issues of live projects" as a function rather than as a view, and it is the only way to ask for
    issues.

    Every read is a fresh query. Nothing is cached, so what a frontend rendered is a snapshot and a
    write is checked against the row as it is now. *)

type conn
(** One open database. Blocking, because SQLite has no waiting in it and this application has no
    concurrency to arrange: [caqti-blocking] is the connector with no monadic layer over it, and
    there is nothing here that a promise would buy. *)

type error =
  | Query_failed of string  (** the database said no, or was not there *)
  | Missing_after_insert of string
      (** the case with no row to blame: a create writes, reads the row back through the same
          projection every other read uses, and that load can only come up empty if something
          removed the row in between *)

val show_error : error -> string

val broken : ('a, error) result -> ('a, Error.t) result
(** A store failure is neither a bad request nor a row saying no, so it reaches a caller as
    {!Error.Broken} rather than dressed up as one of the other two. This is the one conversion, and
    it is here because {!Error} is the domain's and knows nothing about a database. *)

val connect : string -> (conn, error) result
(** [connect uri] opens a database and turns foreign keys on. ["sqlite3::memory:"] is one database
    per connection, which is what makes a test fixture a connection. *)

val initialise : conn -> (unit, error) result
(** Runs [lib/schema.sql], which is safe on a database that already has it. *)

(** {1 Reading} *)

val projects : conn -> (Project.t list, error) result
(** Live projects in creation order, each carrying its issue counts. The counts are what
    {!Project_actions}' refusals read, so a project loaded any other way would offer a menu it could
    not justify. *)

val project : slug:string -> conn -> (Project.t option, error) result

val issues : project_slug:string -> conn -> (Issue.t list, error) result
(** Live issues of a live project, high priority first and oldest first within that —
    [ORDER BY priority DESC, created_at ASC], in SQL.

    Liveness is derived rather than stored: this requires both the issue's and the project's
    [deleted_at] to be null, so soft-deleting a project hides its issues with one row written and
    restoring it brings back exactly the issues that were not deleted in their own right. *)

val issue : id:int -> conn -> (Issue.t option, error) result
(** The same liveness rule as {!issues}: an issue of a deleted project is not found here either. *)

val trashed_projects : conn -> (Project.t Deleted.t list, error) result
val trashed_issues : conn -> (Issue.t Deleted.t list, error) result

val emitted_ddl : conn -> (string list, error) result
(** What the database actually holds, read back from [sqlite_master], so a test can assert that the
    schema this spike designed is the schema it got. *)

(** {1 Writing}

    One call per action group, named once where the group is registered — see {!Wire.group}. Nothing
    here can tell {!delete_project} from {!update_project}: a soft delete is an update, and both
    take a {!Project.t}. What keeps them apart is that each is named exactly once. *)

val create_project : Project.draft -> conn -> (Project.t, error) result
(** Inserts with the id omitted, takes the id back from [RETURNING], and loads the row through the
    same projection every other read uses — so what comes out is the stored object rather than a
    hopeful copy of the draft. *)

val create_issue : Issue.draft -> conn -> (Issue.t, error) result

val update_project : Project.t -> conn -> (unit, error) result
(** Writes the editable columns and stamps [updated_at]. The stamp is taken here rather than by the
    action, which is what keeps every [execute] a pure function and the whole action layer testable
    with no clock.

    [slug] is not written by anything: no action changes it, and the partial unique index it sits
    under is the last thing an edit should be able to trip. *)

val update_issue : Issue.t -> conn -> (unit, error) result
(** [project_id] is not written either: an issue does not move between projects. *)

val delete_project : Project.t -> conn -> (unit, error) result
val delete_issue : Issue.t -> conn -> (unit, error) result
val restore_project : Project.t Deleted.t -> conn -> (unit, error) result
val restore_issue : Issue.t Deleted.t -> conn -> (unit, error) result
