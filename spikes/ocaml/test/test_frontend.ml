(** What a frontend builds on top of the action framework: the form derived from an advertised
    schema, the TUI that renders it, and the command line that renders the same thing as options.

    The TUI is exercised the way a person exercises it, by keystroke, and asserted against the drawn
    frame. That needs no terminal and no synchronisation: {!Tt.Frontend.Tui.render} returns a
    {!Notty.image} and [Notty.Cap.dumb] turns it into plain text with the attributes stripped. What
    a key {e means} is asserted separately from what it {e does}, because {!Tt.Frontend.Tui.on_key}
    is a function of the state alone.

    The CLI is exercised the way a shell exercises it, by [argv]. It gets a temporary file rather
    than an in-memory database, because each of its invocations opens the database again and an
    in-memory one would be a new database each time. *)

open Tt
open Tt.Platform
open Tt.Domains
open Tt.Frontend

let ok what = function
  | Ok value -> value
  | Error e -> Alcotest.failf "%s: %s" what (Db.show_error e)

let case name f = Alcotest.test_case name `Quick f
let check_bool name value = Alcotest.(check bool) name true value

(* --- the form derived from a schema --------------------------------------- *)

let schema key (group : _ Wire.group) =
  (List.find (fun (e : _ Wire.entry) -> e.key = key) group).schema

let fields key group = Form.of_schema (schema key group)

let field =
  Alcotest.testable
    (fun ppf (f : Form.field) ->
      Format.fprintf ppf "%s%s %s %s" f.name
        (if f.required then "*" else "")
        (match f.kind with
        | Form.Text -> "text"
        | Form.Optional_text -> "text?"
        | Form.Enum values -> "<" ^ String.concat "|" values ^ ">")
        (Option.value f.description ~default:"-"))
    ( = )

let title : Form.field =
  {
    name = "title";
    required = true;
    kind = Form.Text;
    description = Some "What to call the issue.";
  }

let note : Form.field =
  {
    name = "note";
    required = false;
    kind = Form.Optional_text;
    description = Some "Why it moved. Left out, whatever described the old status goes with it.";
  }

let issue_status : Form.field =
  {
    name = "status";
    required = true;
    kind = Form.Enum [ "todo"; "doing"; "done" ];
    description = None;
  }

let priority : Form.field =
  {
    name = "priority";
    required = false;
    kind = Form.Enum [ "normal"; "high" ];
    description = Some "How far up the list it sorts. Normal unless said otherwise.";
  }

let json =
  Alcotest.testable (fun ppf j -> Format.pp_print_string ppf (Yojson.Safe.to_string j)) ( = )

let forms =
  [
    case "a required field is marked required, and carries its description" (fun () ->
        Alcotest.(check (result (list field) string))
          "" (Ok [ title ])
          (fields "editTitle" Issue.Actions.group));
    case "an action with no arguments yields an empty form" (fun () ->
        Alcotest.(check (result (list field) string))
          "" (Ok [])
          (fields "delete" Issue.Actions.trash));
    case "a type with no form field fails the whole form" (fun () ->
        Alcotest.(check (result (list field) string))
          "" (Error {|n: no form field for type "integer"|})
          (Form.of_schema
             (Yojson.Safe.from_string {|{"type":"object","properties":{"n":{"type":"integer"}}}|})));
    (* The schema lists properties in the reverse of the order the payload type
       declares them, so this is the order a form and a --help read in. *)
    case "an enum field carries the values the schema advertises, after the optional one" (fun () ->
        Alcotest.(check (result (list field) string))
          ""
          (Ok [ note; issue_status ])
          (fields "editStatus" Issue.Actions.group));
    case "an optional enum is the same field wrapped in anyOf, and not required" (fun () ->
        Alcotest.(check (result (list field) string))
          ""
          (Ok
             [
               priority;
               {
                 name = "body";
                 required = false;
                 kind = Form.Optional_text;
                 description = Some "What it is about.";
               };
               title;
             ])
          (fields "addIssue" Project.Actions.creators));
    (* The schema says [note] may be null and the decoder refuses null, so the
       form has to omit the field instead. Both halves of that are checked in
       [test_actions.ml]; this is the encoding that survives them. *)
    case "an untouched optional field is omitted rather than sent as null" (fun () ->
        Alcotest.check json "" (`Assoc []) (Form.payload [ (note, "") ]));
    case "a filled optional field is sent" (fun () ->
        Alcotest.check json ""
          (`Assoc [ ("note", `String "done") ])
          (Form.payload [ (note, "done") ]));
    case "an empty required field is still sent, for the action to refuse" (fun () ->
        Alcotest.check json "" (`Assoc [ ("title", `String "") ]) (Form.payload [ (title, "") ]));
    case "an enum sends its selection whether or not it is required" (fun () ->
        Alcotest.check json ""
          (`Assoc [ ("priority", `String "high") ])
          (Form.payload [ (priority, "high") ]));
  ]

(* --- the TUI -------------------------------------------------------------- *)

let seeded () =
  let conn = ok "connect" (Db.connect "sqlite3::memory:") in
  ok "initialise" (Db.apply_ddl conn Schema.ddl);
  let tt =
    ok "project" (Project.Services.create { slug = "tt"; title = "tracker"; body = "" } conn)
  in
  let issue title priority =
    ignore
      (ok "issue" (Issue.Services.create { project_id = tt.id; title; body = ""; priority } conn)
        : Issue.t)
  in
  issue "ship" Issue.Priority.High;
  issue "later" Issue.Priority.Normal;
  conn

let start () =
  let conn = seeded () in
  (conn, Tui.start conn)

let key conn state k = Tui.apply conn state (Tui.on_key state k)
let keys conn state ks = List.fold_left (key conn) state ks
let typing text = List.of_seq (Seq.map (fun c -> (`ASCII c, [])) (String.to_seq text))

(** The frame as text, one entry per row, trimmed. Rows rather than one blob because a menu entry is
    a whole line. *)
let lines ?(w = 90) ?(h = 24) state =
  let buf = Buffer.create 1024 in
  Notty.Render.to_buffer buf Notty.Cap.dumb (0, 0) (w, h) (Tui.render state);
  List.map String.trim (String.split_on_char '\n' (Buffer.contents buf))

let has state text = List.exists (fun line -> line = text) (lines state)

let contains text line =
  let n = String.length text in
  String.length line >= n
  && List.exists
       (fun i -> String.sub line i n = text)
       (List.init (String.length line - n + 1) Fun.id)

let mentions state text = List.exists (contains text) (lines state)
let row_of state text = List.find_index (contains text) (lines state)
let down = (`Arrow `Down, [])
let up = (`Arrow `Up, [])
let enter = (`Enter, [])
let escape = (`Escape, [])
let tab = (`Tab, [])
let left = (`Arrow `Left, [])
let right = (`Arrow `Right, [])

(** Move the cursor to the row whose label starts with [prefix], then press enter. Counting
    keystrokes in each case would tie it to how many actions the object happens to offer, which is
    the thing these tests are meant not to know. *)
let pick conn state prefix =
  let rec index i = function
    | [] -> Alcotest.failf "no row starting %S" prefix
    | row :: rest -> if String.starts_with ~prefix (Tui.label row) then i else index (i + 1) rest
  in
  let target = index 0 state.Tui.rows in
  let step = if target > state.Tui.selected then down else up in
  keys conn state (List.init (abs (target - state.Tui.selected)) (fun _ -> step) @ [ enter ])

(* What a key means, with no database anywhere near it. This is the half the
   nottui build could not assert on its own, because there the handler both
   decided and did. *)
let intents =
  [
    case "in a list, the arrows move and enter picks" (fun () ->
        let _, state = start () in
        check_bool "down" (Tui.on_key state down = Tui.Move 1);
        check_bool "up" (Tui.on_key state up = Tui.Move (-1));
        check_bool "enter" (Tui.on_key state enter = Tui.Pick);
        check_bool "q" (Tui.on_key state (`ASCII 'q', []) = Tui.Quit));
    case "in a form, the same keys mean something else" (fun () ->
        let conn, state = start () in
        let state = pick conn state "tt" in
        let state = pick conn state "editTitle" in
        check_bool "a form is up" (state.Tui.form <> None);
        check_bool "tab" (Tui.on_key state tab = Tui.Next_field);
        check_bool "enter" (Tui.on_key state enter = Tui.Submit);
        check_bool "q is typed" (Tui.on_key state (`ASCII 'q', []) = Tui.Insert 'q');
        check_bool "left" (Tui.on_key state left = Tui.Cycle (-1));
        check_bool "escape" (Tui.on_key state escape = Tui.Back));
  ]

let screens =
  [
    case "the first screen lists projects and offers the creator that makes one" (fun () ->
        let _, state = start () in
        check_bool "the project is there" (mentions state "tt");
        check_bool "with the cursor on the creator" (has state "> createProject"));
    case "entering a project shows what it offers, with the refusals it cannot justify" (fun () ->
        let conn, state = start () in
        let state = pick conn state "tt" in
        check_bool "the edits"
          (has state "> editTitle" && has state "editBody" && has state "editStatus");
        check_bool "the refusal" (has state "delete (archive it first)");
        check_bool "the creator" (has state "addIssue");
        check_bool "and the issues, high priority first"
          (match (row_of state "ship", row_of state "later") with
          | Some high, Some normal -> high < normal
          | _ -> false));
    case "entering an issue shows the issue's own actions and not the project's" (fun () ->
        let conn, state = start () in
        let state = pick conn state "tt" in
        let state = pick conn state "1 " in
        check_bool "" (has state "editPriority" && not (has state "addIssue")));
    case "escape goes back out" (fun () ->
        let conn, state = start () in
        let state = pick conn state "tt" in
        check_bool "not the project list" (not (has state "> createProject"));
        let state = key conn state escape in
        check_bool "back on it" (has state "> createProject"));
  ]

let forms_on_screen =
  [
    case "picking a refused action reports the reason instead of opening a form" (fun () ->
        let conn, state = start () in
        let state = pick conn state "tt" in
        let state = pick conn state "delete" in
        check_bool "" (has state "delete: archive it first" && not (has state "* required")));
    case "picking a runnable one opens the form its payload derived" (fun () ->
        let conn, state = start () in
        let state = pick conn state "tt" in
        let state = pick conn state "editTitle" in
        check_bool "the field, labelled by its description"
          (has state "> title* []  What to call the project.");
        check_bool "the legend" (has state "* required"));
    case "a blank submission is refused by the action and stays on the form" (fun () ->
        let conn, state = start () in
        let state = pick conn state "tt" in
        let state = pick conn state "editTitle" in
        let state = key conn state enter in
        check_bool "" (has state "editTitle: invalid: title is required" && has state "* required"));
    case "a filled submission performs the write and goes back" (fun () ->
        let conn, state = start () in
        let state = pick conn state "tt" in
        let state = pick conn state "editTitle" in
        let state = keys conn state (typing "renamed" @ [ enter ]) in
        check_bool "the write is reported" (mentions state "project tt: saved");
        check_bool "and the form is gone" (state.Tui.form = None);
        check_bool "and the new title is on screen" (mentions state "renamed"));
    case "an enum field renders as a selector and cycles through what the schema advertised"
      (fun () ->
        let conn, state = start () in
        let state = pick conn state "tt" in
        let state = pick conn state "editStatus" in
        check_bool "the first value" (has state "> status* [active]  status");
        let state = key conn state right in
        check_bool "the second" (has state "> status* [archived]  status");
        let state = key conn state right in
        check_bool "wrapping round" (has state "> status* [active]  status");
        let state = key conn state left in
        check_bool "and back the other way" (has state "> status* [archived]  status"));
    case "submitting the selector performs the write" (fun () ->
        let conn, state = start () in
        let state = pick conn state "tt" in
        let state = pick conn state "editStatus" in
        let state = keys conn state [ right; enter ] in
        check_bool "" (mentions state "project tt: saved" && mentions state "archived"));
    case "backspace rubs out what was typed" (fun () ->
        let conn, state = start () in
        let state = pick conn state "tt" in
        let state = pick conn state "editTitle" in
        let state = keys conn state (typing "abc" @ [ (`Backspace, []) ]) in
        check_bool "" (has state "> title* [ab]  What to call the project."));
  ]

let creating =
  [
    case "the root creator makes a project" (fun () ->
        let conn, state = start () in
        let state = pick conn state "createProject" in
        check_bool "the form is up" (has state "createProject");
        (* The fields are in the schema's order, which is the reverse of the
           type's: body, title, slug. *)
        let state = keys conn state [ tab; tab ] in
        let state = keys conn state (typing "docs" @ [ enter ]) in
        check_bool "created" (mentions state "project docs: created");
        check_bool "and listed" (mentions state "docs"));
    case "a project's creator makes an issue under it" (fun () ->
        let conn, state = start () in
        let state = pick conn state "tt" in
        let state = pick conn state "addIssue" in
        check_bool "the form is up" (has state "addIssue");
        check_bool "whose optional enum is a selector too"
          (has state
             "> priority [normal]  How far up the list it sorts. Normal unless said otherwise.");
        let state = keys conn state [ tab; tab ] in
        let state = keys conn state (typing "a third" @ [ enter ]) in
        check_bool "created" (mentions state "issue 3: created");
        check_bool "and listed under the project" (mentions state "a third"));
    case "deleting what the screen is about falls out to the parent" (fun () ->
        (* Archive the project, then delete it: the screen has nothing left to
           be about, so reloading it lands on the project list. Nothing here
           names an action key to do that. *)
        let conn, state = start () in
        let state = pick conn state "tt" in
        let state = pick conn state "editStatus" in
        let state = keys conn state [ right; enter ] in
        let state = pick conn state "delete" in
        let state = key conn state enter in
        check_bool "the delete was reported" (mentions state "project tt: deleted");
        check_bool "and the project list is what is on screen" (has state "> createProject"));
  ]

let leaving =
  [
    case "q leaves from a menu and is typed inside a form" (fun () ->
        let conn, state = start () in
        check_bool "not quitting yet" (not state.Tui.quit);
        check_bool "q quits" (key conn state (`ASCII 'q', [])).Tui.quit;
        let state = pick conn state "tt" in
        let state = pick conn state "editTitle" in
        let state = key conn state (`ASCII 'q', []) in
        check_bool "inside a form it is text" ((not state.Tui.quit) && mentions state "[q]"));
  ]

(* --- the command line ----------------------------------------------------- *)

(** What one command line did. [Rejected] is the parser's own refusal, so it is the case where
    nothing reached the database at all. *)
type outcome = Printed of string | Refused of Error.t | Rejected

let outcome =
  Alcotest.testable
    (fun ppf -> function
      | Printed output -> Format.fprintf ppf "printed %S" output
      | Refused e -> Format.fprintf ppf "refused %s" (Error.to_string e)
      | Rejected -> Format.pp_print_string ppf "rejected")
    ( = )

let quiet = Format.formatter_of_buffer (Buffer.create 1024)

let cli uri argv =
  match
    Cmdliner.Cmd.eval_value ~help:quiet ~err:quiet
      ~argv:(Array.of_list (("tt" :: argv) @ [ "--db"; uri ]))
      (Command.main ())
  with
  | Ok (`Ok (Ok output)) -> Printed output
  | Ok (`Ok (Error e)) -> Refused e
  | Ok (`Help | `Version) | Error (`Parse | `Term | `Exn) -> Rejected

(** One temporary file per case, so the command lines below are a narrative within a case and
    independent between cases. *)
let with_cli f () =
  let path = Filename.temp_file "tt-test" ".db" in
  Sys.remove path;
  Fun.protect
    ~finally:(fun () -> if Sys.file_exists path then Sys.remove path)
    (fun () -> f (cli ("sqlite3:" ^ path)))

let printed = function Printed _ -> true | Refused _ | Rejected -> false

let command_line =
  [
    case "an empty database lists nothing"
      (with_cli (fun cli -> Alcotest.check outcome "" (Printed "") (cli [ "project"; "ls" ])));
    case "the root creator makes the first project, by blob or by options"
      (with_cli (fun cli ->
           Alcotest.check outcome "" (Printed "project tt: created")
             (cli [ "action"; "createProject"; {|{"slug":"tt","title":"tracker"}|} ]);
           Alcotest.check outcome "" (Printed "project docs: created")
             (cli [ "createProject"; "--slug"; "docs" ]);
           Alcotest.check outcome "" (Refused (Error.Conflict {|project "tt" already exists|}))
             (cli [ "action"; "createProject"; {|{"slug":"tt"}|} ])));
    case "the options a subcommand takes are the fields of the schema"
      (with_cli (fun cli ->
           ignore (cli [ "action"; "createProject"; {|{"slug":"tt"}|} ] : outcome);
           Alcotest.check outcome "a required field is a required option" Rejected
             (cli [ "project"; "editTitle"; "tt" ]);
           Alcotest.check outcome "an option the schema does not advertise is refused" Rejected
             (cli [ "project"; "editTitle"; "tt"; "--title"; "x"; "--bogus"; "y" ]);
           Alcotest.check outcome "an enum option refuses a value the schema does not advertise"
             Rejected
             (cli [ "project"; "editStatus"; "tt"; "--status"; "nonsense" ]);
           Alcotest.check outcome "a blank one is refused by the action, not by the parser"
             (Refused (Error.Invalid "title is required"))
             (cli [ "project"; "editTitle"; "tt"; "--title"; "   " ]);
           Alcotest.check outcome "an unknown object is the store's answer, not the parser's"
             (Refused (Error.Invalid {|no project "nope"|}))
             (cli [ "project"; "editTitle"; "nope"; "--title"; "x" ])));
    case "the erased path reaches the action the options reach"
      (with_cli (fun cli ->
           ignore (cli [ "action"; "createProject"; {|{"slug":"tt"}|} ] : outcome);
           ignore
             (cli [ "project"; "action"; "tt"; "addIssue"; {|{"title":"write it"}|} ] : outcome);
           Alcotest.check outcome ""
             (cli [ "issue"; "action"; "1"; "editTitle"; {|{"title":"same"}|} ])
             (cli [ "issue"; "editTitle"; "1"; "--title"; "same" ]);
           Alcotest.check outcome "an unknown key is the group's refusal, not the parser's"
             (Refused (Error.Invalid {|no action "explode"|}))
             (cli [ "issue"; "action"; "1"; "explode"; "{}" ]);
           check_bool "a payload that is not JSON is invalid"
             (match cli [ "issue"; "action"; "1"; "editTitle"; "not json" ] with
             | Refused (Error.Invalid _) -> true
             | Printed _ | Refused (Error.Conflict _ | Error.Broken _) | Rejected -> false)));
    (* A subcommand exists because the action is registered, so what does not
       apply right now is still the object's answer rather than a missing
       command. *)
    case "a refused action reports its reason"
      (with_cli (fun cli ->
           ignore (cli [ "action"; "createProject"; {|{"slug":"tt"}|} ] : outcome);
           ignore
             (cli [ "project"; "action"; "tt"; "addIssue"; {|{"title":"write it"}|} ] : outcome);
           ignore (cli [ "issue"; "editStatus"; "1"; "--status"; "doing" ] : outcome);
           Alcotest.check outcome "" (Refused (Error.Conflict "archive it first"))
             (cli [ "project"; "delete"; "tt" ]);
           Alcotest.check outcome "and one that reads a count reports what it counted"
             (Refused (Error.Conflict "finish or drop 1 issue first"))
             (cli [ "project"; "editStatus"; "tt"; "--status"; "archived" ])));
  ]

(* --- the cascade, from outside -------------------------------------------- *)

let cascade =
  [
    case "an archived project takes its issues with it and brings them back"
      (with_cli (fun cli ->
           ignore (cli [ "action"; "createProject"; {|{"slug":"tt"}|} ] : outcome);
           ignore (cli [ "project"; "action"; "tt"; "addIssue"; {|{"title":"write"}|} ] : outcome);
           check_bool "the project has its issue"
             (printed (cli [ "issue"; "ls"; "--project"; "tt" ]));
           check_bool "archiving is allowed once nothing is doing"
             (printed (cli [ "project"; "editStatus"; "tt"; "--status"; "archived" ]));
           check_bool "and then it can be deleted" (printed (cli [ "project"; "delete"; "tt" ]));
           Alcotest.check outcome "its issues go with it, without a row being written for them"
             (Refused (Error.Invalid {|no project "tt"|}))
             (cli [ "issue"; "ls"; "--project"; "tt" ]);
           Alcotest.check outcome "the project itself leaves the list" (Printed "")
             (cli [ "project"; "ls" ]);
           check_bool "the trash holds it, offering the one action registered against a deleted row"
             (match cli [ "trash" ] with
             | Printed output ->
                 Yojson.Safe.Util.member "projects" (Yojson.Safe.from_string output)
                 |> Yojson.Safe.Util.to_list
                 |> List.exists (fun row ->
                     Yojson.Safe.Util.member "project" row = `String "tt"
                     && Yojson.Safe.Util.member "actions" row
                        |> Yojson.Safe.Util.to_list
                        |> List.map (Yojson.Safe.Util.member "key")
                        = [ `String "restore" ])
             | Refused _ | Rejected -> false);
           Alcotest.check outcome "restoring it is reported" (Printed "project tt: restored")
             (cli [ "project"; "action"; "tt"; "restore" ]);
           check_bool "and its issues come back with it"
             (printed (cli [ "issue"; "ls"; "--project"; "tt" ]))));
    (* The slug is only unique among live rows, so deleting frees it — and
       restoring afterwards is then refused with a sentence rather than a UNIQUE
       constraint violation. *)
    case "deleting frees the slug, and the old project can then no longer come back"
      (with_cli (fun cli ->
           ignore (cli [ "action"; "createProject"; {|{"slug":"tt"}|} ] : outcome);
           ignore (cli [ "project"; "editStatus"; "tt"; "--status"; "archived" ] : outcome);
           ignore (cli [ "project"; "delete"; "tt" ] : outcome);
           Alcotest.check outcome "" (Printed "project tt: created")
             (cli [ "action"; "createProject"; {|{"slug":"tt","title":"the second tt"}|} ]);
           Alcotest.check outcome "" (Refused (Error.Conflict {|project "tt" exists again|}))
             (cli [ "project"; "action"; "tt"; "restore" ])));
    (* What an agent reads before it picks anything: the schema as derived, not
       a rendering of it. *)
    case "show advertises what is offered, each with the schema its payload derived"
      (with_cli (fun cli ->
           ignore (cli [ "action"; "createProject"; {|{"slug":"tt"}|} ] : outcome);
           check_bool ""
             (match cli [ "project"; "show"; "tt" ] with
             | Printed output ->
                 Yojson.Safe.Util.member "actions" (Yojson.Safe.from_string output)
                 |> Yojson.Safe.Util.to_list
                 |> List.exists (fun offer ->
                     Yojson.Safe.Util.member "key" offer = `String "editStatus"
                     && Yojson.Safe.Util.member "arguments" offer
                        = schema "editStatus" Project.Actions.group)
             | Refused _ | Rejected -> false)));
  ]

let suite =
  [
    ("the form derived from a schema", forms);
    ("tui: what a key means", intents);
    ("tui: the three screens", screens);
    ("tui: forms", forms_on_screen);
    ("tui: creating", creating);
    ("tui: leaving", leaving);
    ("the command line", command_line);
    ("the cascade, from outside", cascade);
  ]
