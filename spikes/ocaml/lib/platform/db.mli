(** Running a query, and nothing about what is queried.

    What this file does {e not} export is the point of the split: it has no reads and no writes,
    because it has no tables. Those are each domain's [services.mli], and the guarantee that a
    caller cannot write the query that forgets a soft-delete filter lives there, once per object,
    rather than in one file that knows every table. *)

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

val transaction : conn -> (unit -> ('a, Error.t) result) -> ('a, Error.t) result
(** [transaction conn f] runs [f] inside one [BEGIN]…[COMMIT], rolling back on any [Error] — the
    guarantee behind "one public call is one transaction". The frontends open it at their edge and
    the connection reaches an action's [execute]; a refusal after rows are written undoes them.

    A driver-level transaction failure is a {!Error.Broken}, so the result is {!Error.t} rather than
    {!error}: the body it wraps is already an edge computation in {!Error.t}. *)

val apply_ddl : conn -> string -> (unit, error) result
(** [apply_ddl conn sql] runs a DDL text one statement at a time. Safe on a database that already
    has it. The text is the caller's — see [domains/schema.sql]. *)

val emitted_ddl : conn -> (string list, error) result
(** What the database actually holds, read back from [sqlite_master], so a test can assert that the
    schema this spike designed is the schema it got. *)

(** {1 What a domain's services are built from} *)

val exec : conn -> ('p, unit, [< `Zero ]) Caqti_request.t -> 'p -> (unit, error) result
val find : conn -> ('p, 'r, [< `One ]) Caqti_request.t -> 'p -> ('r, error) result

val find_opt :
  conn -> ('p, 'r, [< `Zero | `One ]) Caqti_request.t -> 'p -> ('r option, error) result

val collect :
  conn -> ('p, 'r, [< `Zero | `One | `Many ]) Caqti_request.t -> 'p -> ('r list, error) result

val enum_column :
  name:string ->
  to_string:('a -> string) ->
  of_string:(string -> 'a option) ->
  names:string list ->
  'a Caqti_type.t
(** An enum's SQL type, from the same [to_string]/[of_string] pair its wire decoder and its JSON
    schema are built from, so a column and a payload cannot disagree about the spelling. *)

val created :
  string -> (conn -> 'id -> ('r option, error) result) -> conn -> 'id -> ('r, error) result
(** [created what load conn id] loads a just-inserted row and turns an empty load into
    {!Missing_after_insert}. *)
