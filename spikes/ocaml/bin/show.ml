(** What the erased path looks like from outside: read the object, see what it offers, pick one,
    supply arguments. A TUI user and an agent do the same thing, so this is roughly
    [tt project show tt] followed by [tt project action tt addIssue '{…}'].

    Against a throwaway in-memory database, so it prints the same thing every time it is run. *)

open Tt

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
  let* conn = Store.broken (Store.connect "sqlite3::memory:") in
  let* () = Store.broken (Store.initialise conn) in

  let* projects = Store.broken (Store.projects conn) in
  print_offers "no projects yet" (actions projects Project_actions.root);

  let* _ =
    Wire.submit conn Project_actions.root projects ~key:"createProject"
      ~payload:(Yojson.Safe.from_string {|{"slug":"tt","title":"task tracker"}|})
  in
  let* project = Store.broken (Store.project ~slug:"tt" conn) in
  let project = Option.get project in
  let* _ =
    Wire.submit conn Project_actions.creators project ~key:"addIssue"
      ~payload:(Yojson.Safe.from_string {|{"title":"ship the mvp","priority":"high"}|})
  in

  let* project = Store.broken (Store.project ~slug:"tt" conn) in
  let project = Option.get project in
  let* issues = Store.broken (Store.issues ~project_slug:"tt" conn) in
  print_offers "the project, with one issue to do"
    (actions project Project_actions.group
    @ actions project Project_actions.trash
    @ actions project Project_actions.creators);
  List.iter
    (fun issue ->
      print_offers "the issue"
        (actions issue Issue_actions.group @ actions issue Issue_actions.trash))
    issues;
  Ok ()

let () = match program with Ok () -> () | Error e -> print_endline (Error.to_string e)
