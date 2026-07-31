(** What the erased path looks like from outside: read the object, see what it offers, pick one,
    supply arguments. A TUI user and an agent do the same thing, so this is roughly
    [tt project show tt] followed by [tt project action tt addIssue '{…}'].

    Against a throwaway in-memory database, so it prints the same thing every time it is run. *)

open Tt
open Tt.Platform
open Tt.Domains

let ( let* ) = Result.bind

let print_offers label offers =
  Printf.printf "\n== %s ==\n" label;
  List.iter
    (fun (key, state, schema) ->
      Printf.printf "\n%S (%s)\n%s\n" key state (Yojson.Safe.pretty_to_string schema))
    offers

let state = function None -> "runnable" | Some reason -> "refused: " ^ reason

let actions obj group =
  List.map
    (fun ((e : _ Wire.entry), disabled) -> (e.key, state disabled, e.schema))
    (Wire.available obj group)

let program =
  let* conn = Db.broken (Db.connect "sqlite3::memory:") in
  let* () = Db.broken (Db.apply_ddl conn Schema.ddl) in

  let* projects = Db.broken (Project.Services.list conn) in
  print_offers "no projects yet" (actions projects Project.Actions.root);

  let* _ =
    Db.transaction conn (fun () ->
        Wire.dispatch projects Project.Actions.root ~key:"createProject"
          ~payload:(Yojson.Safe.from_string {|{"slug":"tt","title":"task tracker"}|})
          conn)
  in
  let* project = Db.broken (Project.Services.find ~slug:"tt" conn) in
  let project = Option.get project in
  let* _ =
    Db.transaction conn (fun () ->
        Wire.dispatch project Project.Actions.creators ~key:"addIssue"
          ~payload:(Yojson.Safe.from_string {|{"title":"ship the mvp","priority":"high"}|})
          conn)
  in

  let* project = Db.broken (Project.Services.find ~slug:"tt" conn) in
  let project = Option.get project in
  let* issues = Db.broken (Issue.Services.list ~project_slug:"tt" conn) in
  print_offers "the project, with one issue to do"
    (actions project Project.Actions.group
    @ actions project Project.Actions.trash
    @ actions project Project.Actions.creators);
  List.iter
    (fun issue ->
      print_offers "the issue"
        (actions issue Issue.Actions.group @ actions issue Issue.Actions.trash))
    issues;
  Ok ()

let () = match program with Ok () -> () | Error e -> print_endline (Error.to_string e)
