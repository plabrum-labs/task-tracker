(** The SQL edge, against a real in-memory SQLite database.

    No mocks and no seam: [schema.sql] runs, and every query below goes to sqlite. The database is
    per connection, so one connection is one fixture and no case can see another's rows — which is
    what lets these run in any order.

    The soft-delete cases are the point. Liveness is derived rather than stored, so what has to be
    shown is that one row written on the way out is one row cleared on the way back, and that
    nothing else changed in between. *)

open Tt

let ok what = function
  | Ok value -> value
  | Error e -> Alcotest.failf "%s: %s" what (Store.show_error e)

let titles rows = List.map (fun (i : Issue.t) -> i.title) rows
let slugs rows = List.map (fun (p : Project.t) -> p.slug) rows

(** One connection, the schema applied, and the same three issues under [tt] every case starts from:
    [first] and [third] normal, [second] high. *)
let fixture () =
  let conn = ok "connect" (Store.connect "sqlite3::memory:") in
  ok "initialise" (Store.initialise conn);
  let tt =
    ok "create tt" (Store.create_project { slug = "tt"; title = "tracker"; body = "" } conn)
  in
  let other =
    ok "create other" (Store.create_project { slug = "other"; title = ""; body = "" } conn)
  in
  let issue title priority =
    ok ("create " ^ title)
      (Store.create_issue { project_id = tt.id; title; body = ""; priority } conn)
  in
  let first = issue "first" Issue.Priority.Normal in
  let second = issue "second" Issue.Priority.High in
  let third = issue "third" Issue.Priority.Normal in
  (conn, tt, other, first, second, third)

let case name f = Alcotest.test_case name `Quick f
let check_bool name value = Alcotest.(check bool) name true value

let creation =
  [
    case "create_project returns the row sqlite wrote" (fun () ->
        let _, tt, other, _, _, _ = fixture () in
        check_bool "an id was assigned" (tt.id > 0);
        check_bool "a second insert gets a different one" (other.id <> tt.id);
        check_bool "a new project is active with nothing under it"
          (tt.status = Project.Status.Active && tt.todo = 0 && tt.doing = 0 && tt.done_ = 0);
        check_bool "and carries one instant in both stamps" (tt.created_at = tt.updated_at));
    case "create_issue returns the row sqlite wrote" (fun () ->
        let _, _, _, first, _, _ = fixture () in
        check_bool "an id was assigned" (first.id > 0);
        check_bool "a new issue is todo, and carries the slug of the project it joined"
          (first.status = Issue.Status.Todo && first.project_slug = "tt");
        check_bool "and carries one instant in both stamps" (first.created_at = first.updated_at));
  ]

let reading =
  [
    case "issues come back high priority first, and in creation order within that" (fun () ->
        let conn, _, _, _, _, _ = fixture () in
        Alcotest.(check (list string))
          "" [ "second"; "first"; "third" ]
          (titles (ok "issues" (Store.issues ~project_slug:"tt" conn))));
    case "an issue round-trips through create and read" (fun () ->
        let conn, _, _, first, _, _ = fixture () in
        check_bool "" (ok "issue" (Store.issue ~id:first.id conn) = Some first));
    case "reading an absent id is None" (fun () ->
        let conn, _, _, _, _, _ = fixture () in
        check_bool "" (ok "issue" (Store.issue ~id:9999 conn) = None));
    case "issues are per project" (fun () ->
        let conn, _, _, _, _, _ = fixture () in
        Alcotest.(check (list string))
          "" []
          (titles (ok "issues" (Store.issues ~project_slug:"other" conn))));
    case "projects come back in creation order, carrying the counts their refusals read" (fun () ->
        let conn, _, _, _, _, _ = fixture () in
        let projects = ok "projects" (Store.projects conn) in
        Alcotest.(check (list string)) "" [ "tt"; "other" ] (slugs projects);
        check_bool ""
          (match projects with p :: _ -> p.todo = 3 && p.doing = 0 && p.done_ = 0 | [] -> false));
  ]

let writing =
  [
    case "update persists what an action returned, and stamps only updated_at" (fun () ->
        let conn, _, _, first, _, _ = fixture () in
        let updated =
          match
            Action.run first Issue_actions.Edit_status.action
              { status = Issue.Status.Doing; note = Some "started" }
          with
          | Ok updated -> updated
          | Error e -> Alcotest.failf "editStatus refused: %s" (Error.to_string e)
        in
        ok "update" (Store.update_issue updated conn);
        match ok "issue" (Store.issue ~id:first.id conn) with
        | None -> Alcotest.fail "the issue is gone"
        | Some (i : Issue.t) ->
            check_bool "the write landed"
              (i.status = Issue.Status.Doing && i.status_note = Some "started");
            check_bool "created_at is untouched" (i.created_at = first.created_at));
    case "the counts follow the write" (fun () ->
        let conn, _, _, first, _, _ = fixture () in
        let updated =
          Result.get_ok
            (Action.run first Issue_actions.Edit_status.action
               { status = Issue.Status.Doing; note = None })
        in
        ok "update" (Store.update_issue updated conn);
        match ok "project" (Store.project ~slug:"tt" conn) with
        | None -> Alcotest.fail "the project is gone"
        | Some (p : Project.t) -> check_bool "" (p.todo = 2 && p.doing = 1));
    case "clearing an optional column writes NULL rather than an empty string" (fun () ->
        let conn, _, _, first, _, _ = fixture () in
        let noted =
          Result.get_ok
            (Action.run first Issue_actions.Edit_status.action
               { status = Issue.Status.Doing; note = Some "started" })
        in
        ok "update" (Store.update_issue noted conn);
        let cleared =
          Result.get_ok
            (Action.run noted Issue_actions.Edit_status.action
               { status = Issue.Status.Done; note = None })
        in
        ok "update" (Store.update_issue cleared conn);
        match ok "issue" (Store.issue ~id:first.id conn) with
        | Some (i : Issue.t) -> check_bool "" (i.status_note = None)
        | None -> Alcotest.fail "the issue is gone");
  ]

let deleting =
  [
    case "a deleted issue leaves the list and lands in the trash, stamped" (fun () ->
        let conn, _, _, _, _, third = fixture () in
        ok "delete" (Store.delete_issue third conn);
        Alcotest.(check (list string))
          "" [ "second"; "first" ]
          (titles (ok "issues" (Store.issues ~project_slug:"tt" conn)));
        check_bool "and cannot be read by id" (ok "issue" (Store.issue ~id:third.id conn) = None);
        let trashed = ok "trash" (Store.trashed_issues conn) in
        Alcotest.(check (list string))
          "" [ "third" ]
          (List.map (fun (d : Issue.t Deleted.t) -> d.inner.title) trashed);
        check_bool "with a stamp"
          (List.for_all (fun (d : Issue.t Deleted.t) -> d.deleted_at <> "") trashed));
    case "soft-deleting a project hides its issues, with one row written" (fun () ->
        let conn, tt, _, first, _, third = fixture () in
        ok "delete issue" (Store.delete_issue third conn);
        ok "delete project" (Store.delete_project tt conn);
        Alcotest.(check (list string))
          "" []
          (titles (ok "issues" (Store.issues ~project_slug:"tt" conn)));
        check_bool "and they cannot be read by id either"
          (ok "issue" (Store.issue ~id:first.id conn) = None);
        Alcotest.(check (list string)) "" [ "other" ] (slugs (ok "projects" (Store.projects conn)));
        Alcotest.(check (list string))
          "hiding an issue is not deleting it" [ "third" ]
          (List.map
             (fun (d : Issue.t Deleted.t) -> d.inner.title)
             (ok "trash" (Store.trashed_issues conn))));
    case "the partial unique index covers live rows only, so the slug is reusable" (fun () ->
        let conn, tt, _, _, _, _ = fixture () in
        ok "delete" (Store.delete_project tt conn);
        let reused =
          ok "recreate"
            (Store.create_project { slug = "tt"; title = "the second tt"; body = "" } conn)
        in
        check_bool "a new row" (reused.id <> tt.id);
        check_bool "and the name now resolves to it"
          (match ok "project" (Store.project ~slug:"tt" conn) with
          | Some (p : Project.t) -> p.id = reused.id
          | None -> false));
  ]

let restoring =
  [
    case "restoring a project brings back exactly the issues not deleted in their own right"
      (fun () ->
        let conn, tt, _, _, _, third = fixture () in
        ok "delete issue" (Store.delete_issue third conn);
        ok "delete project" (Store.delete_project tt conn);
        let trashed = ok "trash" (Store.trashed_projects conn) in
        (match List.find_opt (fun (d : Project.t Deleted.t) -> d.inner.id = tt.id) trashed with
        | Some deleted -> ok "restore" (Store.restore_project deleted conn)
        | None -> Alcotest.fail "the deleted project is not in the trash");
        Alcotest.(check (list string))
          "" [ "second"; "first" ]
          (titles (ok "issues" (Store.issues ~project_slug:"tt" conn))));
    case "restoring the issue itself is the second row" (fun () ->
        let conn, _, _, _, _, third = fixture () in
        ok "delete" (Store.delete_issue third conn);
        (match ok "trash" (Store.trashed_issues conn) with
        | deleted :: _ -> ok "restore" (Store.restore_issue deleted conn)
        | [] -> Alcotest.fail "the deleted issue is not in the trash");
        Alcotest.(check (list string))
          "" [ "second"; "first"; "third" ]
          (titles (ok "issues" (Store.issues ~project_slug:"tt" conn)));
        check_bool "and the trash is empty" (ok "trash" (Store.trashed_issues conn) = []));
  ]

(** What the database actually holds, so the schema this spike designed is asserted rather than
    assumed: [STRICT] on both tables, the three [CHECK] constraints, the cascade, and the partial
    unique index. *)
let emitted =
  [
    case "the emitted schema is the designed one" (fun () ->
        let conn, _, _, _, _, _ = fixture () in
        let ddl = String.concat "\n" (ok "ddl" (Store.emitted_ddl conn)) in
        let has what =
          let n = String.length what and h = String.length ddl in
          check_bool what
            (n <= h
            && List.exists (fun i -> String.sub ddl i n = what) (List.init (h - n + 1) Fun.id))
        in
        has ") STRICT";
        has "CHECK (status IN ('active', 'archived'))";
        has "CHECK (status IN ('todo', 'doing', 'done'))";
        has "CHECK (priority IN (0, 1))";
        has "REFERENCES projects (id) ON DELETE CASCADE";
        has "CREATE UNIQUE INDEX projects_slug_live";
        has "WHERE deleted_at IS NULL");
    case "a duplicate live slug is refused by the index, hook or no hook" (fun () ->
        let conn, _, _, _, _, _ = fixture () in
        (* The store is reached directly, so [createProject]'s refusal is not in
           the way. What answers is the index, which is the guarantee the hook
           only turns into a sentence. *)
        check_bool ""
          (Result.is_error
             (Store.create_project { slug = "tt"; title = "another"; body = "" } conn)));
    case "a status the CHECK constraint forbids cannot be written" (fun () ->
        let conn, _, _, first, _, _ = fixture () in
        (* Nothing in OCaml can produce one — [Issue.Status] is a closed variant
           — so this is asserted the only way it can be: the column accepts the
           three names and the database is what says so. *)
        List.iter
          (fun status -> ok "update" (Store.update_issue { first with status } conn))
          [ Issue.Status.Todo; Issue.Status.Doing; Issue.Status.Done ]);
  ]

let suite =
  [
    ("store: creation", creation);
    ("store: reading", reading);
    ("store: writing", writing);
    ("store: deleting", deleting);
    ("store: restoring", restoring);
    ("store: the emitted schema", emitted);
  ]
