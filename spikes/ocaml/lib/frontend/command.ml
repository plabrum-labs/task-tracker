(** The erased path with a shell or an agent on the end of it.

    Same three calls as {!Tui}: {!Wire.available} for what is on offer, {!Form.of_schema} for the
    arguments, {!Wire.submit} for the write. Nothing here names an action key — an action gets a
    subcommand because it is registered, and that subcommand's options are the fields its payload
    type derived. Adding a fifth issue action changes no line of this file.

    Two ways in, over the same routes. [action KEY JSON] is the erased path itself: one command
    carrying any action's payload as a blob, which is what an agent holding the output of [show]
    already has. The per-action subcommands are the same dispatch with the blob spelled out as
    options, for a person at a prompt. Both end at {!Wire.submit}, so neither can reach an action
    the other cannot.

    What this file still states is how an address becomes an object. A group knows what writes its
    result; it does not know that a project is found by slug and a trash row by slug among the
    deleted. That pairing is the four lines of {!project_routes} and the three of {!issue_routes}.

    A module of the library rather than the executable, so a test can evaluate a command against an
    [~argv] with no terminal involved. What is built here is a {!Cmdliner.Cmd.t} yielding either
    what to print or the refusal to report, and [bin/cli.ml] is left with the printing and the exit
    code. *)

open Platform
open Domains
open Cmdliner

type outcome = (string, Error.t) result

let ( let* ) = Result.bind

(* --- rendering ----------------------------------------------------------- *)

let project_line (p : Project.t) =
  Printf.sprintf "%-12s %-24s %-8s %d todo, %d doing, %d done" p.slug p.title
    (Project.Status.to_string p.status)
    p.todo p.doing p.done_

let issue_line (i : Issue.t) =
  Printf.sprintf "%-4d %-12s %-8s %-6s %s" i.id i.project_slug (Issue.Status.to_string i.status)
    (Issue.Priority.to_string i.priority)
    i.title

(** The domain types carry no ppx — deriving them would make the wire format a property of the
    domain rather than of this edge — so the one place that spells their fields out is here. *)
let project_json (p : Project.t) : Yojson.Safe.t =
  `Assoc
    [
      ("slug", `String p.slug);
      ("title", `String p.title);
      ("body", `String p.body);
      ("status", Project.Status.yojson_of_t p.status);
      ("todo", `Int p.todo);
      ("doing", `Int p.doing);
      ("done", `Int p.done_);
      ("created_at", `String p.created_at);
      ("updated_at", `String p.updated_at);
    ]

let issue_json (i : Issue.t) : Yojson.Safe.t =
  `Assoc
    [
      ("id", `Int i.id);
      ("project", `String i.project_slug);
      ("title", `String i.title);
      ("body", `String i.body);
      ("status", Issue.Status.yojson_of_t i.status);
      ("priority", Issue.Priority.yojson_of_t i.priority);
      ("status_note", match i.status_note with None -> `Null | Some note -> `String note);
      ("created_at", `String i.created_at);
      ("updated_at", `String i.updated_at);
    ]

(** One offered action, as the JSON an agent reads before it picks anything.

    The schema is passed through exactly as it was derived. Rendering it — as a form, as a set of
    options, as an agent's tool definition — is the caller's business. *)
let offer ~key ~schema disabled : Yojson.Safe.t =
  `Assoc
    (("key", `String key)
     ::
     (match disabled with
     | None -> [ ("state", `String "runnable") ]
     | Some reason -> [ ("state", `String "refused"); ("reason", `String reason) ])
    @ [ ("arguments", schema) ])

let offers obj group =
  List.map
    (fun ((entry : _ Wire.entry), disabled) -> offer ~key:entry.key ~schema:entry.schema disabled)
    (Wire.available obj group)

(* --- resolution ---------------------------------------------------------- *)

let live_project slug conn =
  let* found = Db.broken (Project.Services.find ~slug conn) in
  match found with
  | Some project -> Ok project
  | None -> Error (Error.Invalid (Printf.sprintf "no project %S" slug))

(** A trash row is loaded with the one thing [restore]'s refusal reads: whether the slug it wants
    back has been taken by a live project since. *)
let restorable_project slug conn =
  let* trashed = Db.broken (Project.Services.trashed conn) in
  let* live = Db.broken (Project.Services.list conn) in
  match List.find_opt (fun (d : Project.t Deleted.t) -> d.inner.slug = slug) trashed with
  | None -> Error (Error.Invalid (Printf.sprintf "no deleted project %S" slug))
  | Some deleted ->
      Ok { Project.deleted; slug_taken = List.exists (fun (p : Project.t) -> p.slug = slug) live }

let live_issue id conn =
  let* found = Db.broken (Issue.Services.find ~id conn) in
  match found with
  | Some issue -> Ok issue
  | None -> Error (Error.Invalid (Printf.sprintf "no issue %d" id))

let trashed_issue id conn =
  let* trashed = Db.broken (Issue.Services.trashed conn) in
  match List.find_opt (fun (d : Issue.t Deleted.t) -> d.inner.id = id) trashed with
  | Some deleted -> Ok deleted
  | None -> Error (Error.Invalid (Printf.sprintf "no deleted issue %d" id))

(* --- the writes ---------------------------------------------------------- *)

type 'address route = {
  key : string;
  schema : Yojson.Safe.t;
  write : 'address -> payload:Yojson.Safe.t -> Db.conn -> (string, Error.t) result;
}
(** One registered action as this frontend needs it: the key it answers to, the schema its
    subcommand renders, and the write.

    The write is load, dispatch, persist — in that order, so {!Action.run} stays the only path to a
    write and the hooks are checked against the row as it is now rather than as some earlier [show]
    reported it. The object type and the result type are both erased here, which is what lets four
    groups that agree on neither sit in one list. *)

let routes ~load group =
  List.map
    (fun (entry : _ Wire.entry) ->
      {
        key = entry.key;
        schema = entry.schema;
        write =
          (fun address ~payload conn ->
            let* obj = load address conn in
            Wire.submit conn group obj ~key:entry.key ~payload);
      })
    group.Wire.entries

let project_routes =
  routes ~load:live_project Project.Actions.group
  @ routes ~load:live_project Project.Actions.trash
  @ routes ~load:live_project Project.Actions.creators
  @ routes ~load:restorable_project Project.Actions.deleted_group

let issue_routes =
  routes ~load:live_issue Issue.Actions.group
  @ routes ~load:live_issue Issue.Actions.trash
  @ routes ~load:trashed_issue Issue.Actions.deleted_group

(** The root creator has no object to address, so its parent is the list of live projects — which is
    exactly what its uniqueness refusal has to read. *)
let root_routes =
  routes ~load:(fun () conn -> Db.broken (Project.Services.list conn)) Project.Actions.root

let run_route routes address ~key ~payload conn =
  match List.find_opt (fun route -> route.key = key) routes with
  | None -> Error (Error.Invalid (Printf.sprintf "no action %S" key))
  | Some route -> route.write address ~payload conn

(* --- the reads ----------------------------------------------------------- *)

let project_ls conn =
  let* projects = Db.broken (Project.Services.list conn) in
  Ok (String.concat "\n" (List.map project_line projects))

let issue_ls ~project_slug conn =
  let* slugs =
    match project_slug with
    | Some slug ->
        let* project = live_project slug conn in
        Ok [ project.slug ]
    | None ->
        let* projects = Db.broken (Project.Services.list conn) in
        Ok (List.map (fun (p : Project.t) -> p.slug) projects)
  in
  let* rows =
    List.fold_left
      (fun acc slug ->
        let* rows = acc in
        let* found = Db.broken (Issue.Services.list ~project_slug:slug conn) in
        Ok (rows @ found))
      (Ok []) slugs
  in
  Ok (String.concat "\n" (List.map issue_line rows))

let project_show slug conn =
  let* project = live_project slug conn in
  let actions =
    offers project Project.Actions.group
    @ offers project Project.Actions.trash
    @ offers project Project.Actions.creators
  in
  Ok
    (Yojson.Safe.pretty_to_string
       (`Assoc [ ("project", project_json project); ("actions", `List actions) ]))

let issue_show id conn =
  let* issue = live_issue id conn in
  let actions = offers issue Issue.Actions.group @ offers issue Issue.Actions.trash in
  Ok
    (Yojson.Safe.pretty_to_string
       (`Assoc [ ("issue", issue_json issue); ("actions", `List actions) ]))

(** The trash, with what each row offers — which is [restore] and nothing else, because that is the
    only action registered against the deleted types. *)
let trash conn =
  let* projects = Db.broken (Project.Services.trashed conn) in
  let* issues = Db.broken (Issue.Services.trashed conn) in
  let* live = Db.broken (Project.Services.list conn) in
  let project (d : Project.t Deleted.t) =
    let restorable =
      {
        Project.deleted = d;
        slug_taken = List.exists (fun (p : Project.t) -> p.slug = d.inner.slug) live;
      }
    in
    `Assoc
      [
        ("project", `String d.inner.slug);
        ("deleted_at", `String d.deleted_at);
        ("actions", `List (offers restorable Project.Actions.deleted_group));
      ]
  in
  let issue (d : Issue.t Deleted.t) =
    `Assoc
      [
        ("issue", `Int d.inner.id);
        ("title", `String d.inner.title);
        ("deleted_at", `String d.deleted_at);
        ("actions", `List (offers d Issue.Actions.deleted_group));
      ]
  in
  Ok
    (Yojson.Safe.pretty_to_string
       (`Assoc
          [
            ("projects", `List (List.map project projects));
            ("issues", `List (List.map issue issues));
          ]))

(* --- the command tree ---------------------------------------------------- *)

let db =
  let env = Cmd.Env.info "TT_DB" ~doc:"The database to open." in
  Arg.(value & opt string "sqlite3:tt.db" & info [ "db" ] ~env ~docv:"URI" ~doc:"The database.")

(** Every leaf opens the database, initialises it and runs one thing. *)
let against uri f : outcome =
  let* conn = Db.broken (Db.connect uri) in
  let* () = Db.broken (Db.apply_ddl conn Schema.ddl) in
  f conn

let simple name ~doc term = Cmd.make (Cmd.info name ~doc) term

(** One option per field of the payload, and — cmdliner rejecting what it was not told about —
    nothing else.

    [required] comes from the schema, so a missing [--title] is a usage error rather than a payload
    the decoder refuses further in. The requirement is not restated here, only enforced earlier;
    what a title has to {e contain} is still the action's, which is why a blank one gets past this
    and is refused by [execute].

    The [--help] text is the field's own doc comment, carried into the schema by
    [ppx_deriving_jsonschema ~ocaml_doc]. An [Enum] field becomes [Arg.enum] over the values the
    schema advertises, so a bad status is a usage error listing the alternatives rather than a
    decode failure quoting a type name. *)
let conv (field : Form.field) =
  match field.kind with
  | Form.Text | Form.Optional_text -> Arg.string
  | Form.Enum values -> Arg.enum (List.map (fun value -> (value, value)) values)

let option (field : Form.field) : (Form.field * string) option Term.t =
  let doc =
    String.concat " "
      (List.filter
         (fun part -> part <> "")
         [
           (if field.required then "Required." else "Optional.");
           Option.value field.description ~default:"";
           (match field.kind with
           | Form.Enum values -> Printf.sprintf "One of %s." (String.concat ", " values)
           | Form.Text | Form.Optional_text -> "");
         ])
  in
  let info = Arg.info [ field.name ] ~docv:"VALUE" ~doc in
  let opt = Arg.opt (Arg.some (conv field)) None info in
  let pair value = (field, value) in
  if field.required then Term.(const (fun value -> Some (pair value)) $ Arg.required opt)
  else Term.(const (Option.map pair) $ Arg.value opt)

(** The options of one action, collected into what {!Form.payload} takes.

    A [Term.t] holds one parsed value, so a payload of unknown width is a fold: each field's option
    is applied to the terms of the fields after it, and the empty payload is where it bottoms out.
    An option that was not passed is dropped rather than sent, which is the encoding {!Form.payload}
    settled on. *)
let options fields =
  let collect field rest =
    let add value rest = match value with None -> rest | Some value -> value :: rest in
    Term.(const add $ option field $ rest)
  in
  List.fold_right collect fields (Term.const [])

(** One subcommand per registered action, whether or not it applies right now.

    A hook belongs to an object and a command tree does not have one yet — the subcommand is parsed
    before the row has been read. So [delete] is a command on an active project too, and the refusal
    comes back from the write against the live object rather than from the parser. This is the same
    snapshot the TUI's menu draws, taken earlier. *)
let generated ~uri ~address route =
  let info = Cmd.info route.key ~doc:(Printf.sprintf "Run the $(b,%s) action." route.key) in
  match Form.of_schema route.schema with
  (* A schema with no options to render is reported rather than approximated,
     exactly as the TUI reports it. A subcommand that dropped the field it could
     not render would be refused by the decoder for a reason nothing in --help
     mentions. *)
  | Error message ->
      Cmd.make info (Term.const (Error (Error.Invalid (Printf.sprintf "%s: %s" route.key message))))
  | Ok fields ->
      let run uri address values =
        against uri (route.write address ~payload:(Form.payload values))
      in
      Cmd.make info Term.(const run $ uri $ address $ options fields)

let raw ~uri ~address ~routes ~at =
  let key =
    Arg.(required & pos at (some string) None & info [] ~docv:"KEY" ~doc:"The action to run.")
  in
  let payload =
    Arg.(
      value
      & pos (at + 1) string "{}"
      & info [] ~docv:"JSON" ~doc:"The action's arguments, as a JSON object.")
  in
  let run uri address key payload =
    match Yojson.Safe.from_string payload with
    | payload -> against uri (run_route routes address ~key ~payload)
    | exception Yojson.Json_error message -> Error (Error.Invalid message)
  in
  let info = Cmd.info "action" ~doc:"Run an action by key, passing its arguments as JSON." in
  Cmd.make info Term.(const run $ uri $ address $ key $ payload)

let slug ~at =
  Arg.(required & Arg.pos at (some string) None & info [] ~docv:"SLUG" ~doc:"The project's slug.")

let issue_id ~at =
  Arg.(required & Arg.pos at (some int) None & info [] ~docv:"ID" ~doc:"The issue's id.")

let project_command =
  let address = slug ~at:0 in
  Cmd.group
    (Cmd.info "project" ~doc:"Projects.")
    (simple "ls" ~doc:"List live projects." Term.(const (fun uri -> against uri project_ls) $ db)
    :: simple "show" ~doc:"Print a project and what it offers."
         Term.(const (fun uri slug -> against uri (project_show slug)) $ db $ slug ~at:0)
    :: raw ~uri:db ~address ~routes:project_routes ~at:1
    :: List.map (generated ~uri:db ~address) project_routes)

let issue_command =
  let address = issue_id ~at:0 in
  let project_opt =
    Arg.(value & opt (some string) None & info [ "project" ] ~docv:"SLUG" ~doc:"Only this project.")
  in
  Cmd.group (Cmd.info "issue" ~doc:"Issues.")
    (simple "ls" ~doc:"List live issues of live projects."
       Term.(
         const (fun uri project_slug -> against uri (issue_ls ~project_slug)) $ db $ project_opt)
    :: simple "show" ~doc:"Print an issue and what it offers."
         Term.(const (fun uri id -> against uri (issue_show id)) $ db $ issue_id ~at:0)
    :: raw ~uri:db ~address ~routes:issue_routes ~at:1
    :: List.map (generated ~uri:db ~address) issue_routes)

(** The root creator, which has no object to address and so no positional to take one. Both forms
    are here for the same reason they are everywhere else: the blob for an agent, the options for a
    person. *)
let root_commands =
  let address = Term.const () in
  raw ~uri:db ~address ~routes:root_routes ~at:0
  :: List.map (generated ~uri:db ~address) root_routes

(** Everything the database can be asked, as one command tree. Evaluating it yields what to print,
    or the refusal to report. *)
let main () : outcome Cmd.t =
  let info = Cmd.info "tt" ~doc:"A task tracker, driven from a command line." in
  Cmd.group info
    ([
       project_command;
       issue_command;
       simple "trash" ~doc:"What has been deleted, and what it offers."
         Term.(const (fun uri -> against uri trash) $ db);
     ]
    @ root_commands)
