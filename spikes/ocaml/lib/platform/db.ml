(** The database, with no table in it.

    Everything a query needs and nothing about what is being queried: a connection, the four ways to
    run a request, the narrowing of Caqti's error, and the two codecs a column type is built from. A
    domain's [services.ml] names the tables; this file cannot.

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

(** One public call is one transaction. [f] is run inside [BEGIN]…[COMMIT]; a refusal — any [Error]
    at all, including one after rows are written — rolls the whole thing back. The frontends open
    this at their edge and hand the connection down to an action's [execute], so what an action does
    to the transaction is undone in full if it, or anything after it, says no.

    A [transact] error from the driver — [start], [commit] or [rollback] failing — is the machine
    saying no rather than the row, so it reaches a caller as {!Error.Broken}. This is the one place
    that speaks {!Error.t} rather than {!error}, because the body it wraps is a caller's whole edge
    computation and that is already in {!Error.t}. *)
let transaction (conn : conn) (f : unit -> ('a, Error.t) result) : ('a, Error.t) result =
  let (module C : Caqti_blocking.CONNECTION) = conn in
  let broken e = Error.Broken (Caqti_error.show e) in
  match C.start () with
  | Error e -> Error (broken e)
  | Ok () -> (
      match f () with
      | Ok _ as ok -> ( match C.commit () with Ok () -> ok | Error e -> Error (broken e))
      | Error _ as refusal -> (
          match C.rollback () with Ok () -> refusal | Error e -> Error (broken e)))

(** One statement per request is Caqti's rule, so the file is split on its statement terminator. A
    [;] inside a comment would break this, which is why there is not one. *)
let statements sql =
  String.split_on_char ';' sql |> List.map String.trim |> List.filter (fun s -> s <> "")

(** Applies a DDL text, which is safe on a database that already has it. The text is the caller's:
    this file knows how to run it and not what is in it. *)
let apply_ddl conn sql : (unit, error) result =
  List.fold_left
    (fun acc sql ->
      let* () = acc in
      exec conn (Caqti_type.(unit ->. unit) ~oneshot:true sql) ())
    (Ok ()) (statements sql)

let emitted =
  Caqti_type.(unit ->* string) "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name"

let emitted_ddl conn : (string list, error) result = collect conn emitted ()

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

(** Creation: insert, take the id back from [RETURNING], load the row. The load is what makes the
    returned object the stored one rather than a hopeful copy of the draft — a project carries
    counts and an issue carries its project's slug, and neither is on the row that was written. *)
let created what load conn id =
  let* found = load conn id in
  match found with Some row -> Ok row | None -> Error (Missing_after_insert what)
