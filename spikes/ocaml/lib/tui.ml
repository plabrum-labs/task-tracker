(** The erased path with a person on the end of it.

    Same three calls as {!Command}: {!Wire.available} for the menu, {!Form.of_schema} for the
    arguments, {!Wire.submit} for the write. Nothing here names an action key — a row appears in a
    menu because the action is registered, and its form has the fields its payload type derived.
    Adding a fifth issue action changes no line of this file.

    Every screen is one movable list of rows, and a row is either somewhere to go or something to
    do. That is what lets three screens share one cursor, one Enter and one renderer, and it is the
    shape the action framework suggests rather than one imposed on it: the children of an object and
    the actions over it are both just things you can pick.

    Nothing is cached across a write. A row carries the key it would run and nothing else — no
    object and no closure — so {!write} loads the row again and the hooks are checked against it as
    it is now rather than as the menu drew it.

    {2 Three functions rather than a session}

    {!render} and {!on_key} are pure and {!apply} is where the database is. Notty draws an image and
    hands back a keystroke; there is no widget holding state between the two, so the state machine
    is written out. What that buys is that a test asserts what a key {e means} separately from what
    it {e does}, which no incremental toolkit allows. *)

module A = Notty.A
module I = Notty.I

let dim = A.(fg lightblack)
let bold = A.(st bold)
let browsing = "up/down to move, enter to pick, esc to go back, q to quit"
let filling = "tab to move, left/right to choose, enter to submit, esc to go back"

(** Where the cursor is. [Detail] carries the slug as well as the id because the way back out of a
    deleted issue is the project it was in, and by then the issue is not there to be asked. *)
type screen = Projects | Issues of string | Detail of { project : string; id : int }

let parent = function
  | Projects -> None
  | Issues _ -> Some Projects
  | Detail { project; _ } -> Some (Issues project)

(** A row is either somewhere to go or something to do. A refused row stays in the list with its
    reason rather than being dropped, which is the whole of what a disabled hook buys a person over
    a fixed set of commands. *)
type row =
  | Go of { label : string; target : screen }
  | Do of { key : string; disabled : string option; schema : Yojson.Safe.t }

let label = function
  | Go row -> row.label
  | Do { key; disabled = None; _ } -> key
  | Do { key; disabled = Some reason; _ } -> Printf.sprintf "%s (%s)" key reason

(** A form control. A text box for a string, a cycling selector for an enum — the first control the
    schema has ever asked this frontend to derive that is not an edit field. *)
type control = Editing of string | Choosing of { values : string list; index : int }

let value_of = function
  | Editing text -> text
  | Choosing { values; index } -> Option.value (List.nth_opt values index) ~default:""

type entered = { field : Form.field; control : control }
type form = { key : string; entries : entered list; focus : int }

type t = {
  screen : screen;
  header : string;
  rows : row list;
  selected : int;
  form : form option;
      (** [Some] while a form is up. The screen underneath does not change, so leaving a form is
          dropping this and nothing else. *)
  status : string;
  quit : bool;
}

(** What a keystroke means. Nothing about it touches a database, which is what makes {!on_key}
    assertable on its own. *)
type intent =
  | Ignored
  | Move of int
  | Pick
  | Back
  | Quit
  | Insert of char
  | Rub
  | Next_field
  | Cycle of int
  | Submit

(* --- reading a screen ---------------------------------------------------- *)

let ( let* ) = Result.bind

let live_project conn slug =
  let* found = Store.broken (Store.project ~slug conn) in
  match found with
  | Some project -> Ok project
  | None -> Error (Error.Invalid (Printf.sprintf "no project %S" slug))

let live_issue conn id =
  let* found = Store.broken (Store.issue ~id conn) in
  match found with
  | Some issue -> Ok issue
  | None -> Error (Error.Invalid (Printf.sprintf "no issue %d" id))

let do_rows obj group =
  List.map
    (fun ((entry : _ Wire.entry), disabled) ->
      Do { key = entry.key; disabled; schema = entry.schema })
    (Wire.available obj group)

(** One screen, read fresh. The header is what the screen is about and the rows are what can be done
    from it, in that order. *)
let load conn screen : (string * row list, Error.t) result =
  match screen with
  | Projects ->
      let* projects = Store.broken (Store.projects conn) in
      Ok
        ( "projects",
          do_rows projects Project_actions.root
          @ List.map
              (fun (p : Project.t) ->
                Go
                  {
                    label =
                      Printf.sprintf "%-12s %-24s %-8s %d/%d/%d" p.slug p.title
                        (Project.Status.to_string p.status)
                        p.todo p.doing p.done_;
                    target = Issues p.slug;
                  })
              projects )
  | Issues slug ->
      let* project = live_project conn slug in
      let* issues = Store.broken (Store.issues ~project_slug:slug conn) in
      Ok
        ( Printf.sprintf "%s: %s" (Project.subject project) project.title,
          do_rows project Project_actions.group
          @ do_rows project Project_actions.trash
          @ do_rows project Project_actions.creators
          @ List.map
              (fun (i : Issue.t) ->
                Go
                  {
                    label =
                      Printf.sprintf "%-4d %-8s %-6s %s" i.id (Issue.Status.to_string i.status)
                        (Issue.Priority.to_string i.priority)
                        i.title;
                    target = Detail { project = slug; id = i.id };
                  })
              issues )
  | Detail { id; _ } ->
      let* issue = live_issue conn id in
      Ok
        ( Printf.sprintf "%s: %s" (Issue.subject issue) issue.title,
          do_rows issue Issue_actions.group @ do_rows issue Issue_actions.trash )

(* --- the write ----------------------------------------------------------- *)

(** Load, dispatch, persist. The groups a screen offers are listed here and the store call each one
    ends in is not: that is the group's own, stated where the actions are registered. What is left
    is which object a screen's actions are about, which is the one thing a registration cannot know.
*)
let write conn screen ~key ~payload : (string, Error.t) result =
  let attempt group load () =
    if not (Wire.holds key group) then None
    else Some (Result.bind (load ()) (fun obj -> Wire.submit conn group obj ~key ~payload))
  in
  let attempts =
    match screen with
    | Projects ->
        let projects () = Store.broken (Store.projects conn) in
        [ attempt Project_actions.root projects ]
    | Issues slug ->
        let project () = live_project conn slug in
        [
          attempt Project_actions.group project;
          attempt Project_actions.trash project;
          attempt Project_actions.creators project;
        ]
    | Detail { id; _ } ->
        let issue () = live_issue conn id in
        [ attempt Issue_actions.group issue; attempt Issue_actions.trash issue ]
  in
  match List.find_map (fun attempt -> attempt ()) attempts with
  | Some outcome -> outcome
  | None -> Error (Error.Invalid (Printf.sprintf "no action %S" key))

(* --- keys ---------------------------------------------------------------- *)

(** Pure. A form takes the keys a form takes and the list takes the rest, and nothing here decides
    what a key {e does} — only what it means. *)
let on_key state (key : Notty.Unescape.key) =
  match (state.form, key) with
  | Some _, (`Escape, []) -> Back
  | Some _, ((`Tab | `Arrow `Down), []) -> Next_field
  | Some _, (`Enter, []) -> Submit
  | Some _, (`Backspace, []) -> Rub
  | Some _, (`Arrow `Left, []) -> Cycle (-1)
  | Some _, (`Arrow `Right, []) -> Cycle 1
  | Some _, (`ASCII c, []) -> Insert c
  | None, (`Arrow `Up, []) -> Move (-1)
  | None, (`Arrow `Down, []) -> Move 1
  | None, (`Enter, []) -> Pick
  | None, (`Escape, []) -> Back
  | None, (`ASCII 'q', []) -> Quit
  | (None | Some _), _ -> Ignored

(* --- applying an intent -------------------------------------------------- *)

let control_of (field : Form.field) =
  match field.kind with
  | Form.Text | Form.Optional_text -> Editing ""
  | Form.Enum values -> Choosing { values; index = 0 }

(** Read the current screen back, and fall out to the parent if the object it is about has gone.
    That is how deleting the thing you are looking at navigates, and it names no action key to do
    it. *)
let rec reload conn state =
  match load conn state.screen with
  | Ok (header, rows) ->
      { state with header; rows; selected = min state.selected (max 0 (List.length rows - 1)) }
  | Error e -> (
      match parent state.screen with
      | None -> { state with rows = []; status = Error.to_string e }
      | Some parent -> reload conn { state with screen = parent; selected = 0 })

let start conn =
  reload conn
    {
      screen = Projects;
      header = "";
      rows = [];
      selected = 0;
      form = None;
      status = browsing;
      quit = false;
    }

let map_focused state f =
  match state.form with
  | None -> state
  | Some form ->
      let entries =
        List.mapi (fun i entry -> if i = form.focus then f entry else entry) form.entries
      in
      { state with form = Some { form with entries } }

let pick conn state =
  match List.nth_opt state.rows state.selected with
  | None -> state
  | Some (Go { target; _ }) ->
      reload conn { state with screen = target; selected = 0; status = browsing }
  | Some (Do { key; disabled = Some reason; _ }) ->
      { state with status = Printf.sprintf "%s: %s" key reason }
  | Some (Do { key; disabled = None; schema }) -> (
      (* A schema the form cannot render is reported rather than approximated,
         so an action is never handed a payload missing half of what it asked
         for. *)
      match Form.of_schema schema with
      | Error message -> { state with status = Printf.sprintf "%s: %s" key message }
      | Ok fields ->
          let entries = List.map (fun field -> { field; control = control_of field }) fields in
          { state with form = Some { key; entries; focus = 0 }; status = filling })

let submit conn state form =
  let values = List.map (fun entry -> (entry.field, value_of entry.control)) form.entries in
  match write conn state.screen ~key:form.key ~payload:(Form.payload values) with
  (* A refusal leaves the form up with its values intact, because that is where
     the fix usually is. *)
  | Error e -> { state with status = Printf.sprintf "%s: %s" form.key (Error.to_string e) }
  | Ok message -> reload conn { state with form = None; status = message }

(** The half with the database in it. *)
let apply conn state intent =
  match intent with
  | Ignored -> state
  | Quit -> { state with quit = true }
  | Move n ->
      let last = max 0 (List.length state.rows - 1) in
      { state with selected = max 0 (min last (state.selected + n)) }
  | Back -> (
      match state.form with
      | Some _ -> { state with form = None; status = browsing }
      | None -> (
          match parent state.screen with
          | None -> state
          | Some parent -> reload conn { state with screen = parent; selected = 0 }))
  | Pick -> pick conn state
  | Next_field -> (
      match state.form with
      | Some form when form.entries <> [] ->
          {
            state with
            form = Some { form with focus = (form.focus + 1) mod List.length form.entries };
          }
      | None | Some _ -> state)
  | Insert c ->
      map_focused state (fun entry ->
          match entry.control with
          | Editing text -> { entry with control = Editing (text ^ String.make 1 c) }
          | Choosing _ -> entry)
  | Rub ->
      map_focused state (fun entry ->
          match entry.control with
          | Editing "" -> entry
          | Editing text ->
              { entry with control = Editing (String.sub text 0 (String.length text - 1)) }
          | Choosing _ -> entry)
  | Cycle n ->
      map_focused state (fun entry ->
          match entry.control with
          | Editing _ -> entry
          | Choosing { values; index } ->
              let count = List.length values in
              if count = 0 then entry
              else
                {
                  entry with
                  control = Choosing { values; index = (((index + n) mod count) + count) mod count };
                })
  | Submit -> ( match state.form with None -> state | Some form -> submit conn state form)

(* --- rendering ----------------------------------------------------------- *)

(** Pure in the sense that matters: it reads the state and touches nothing else, so a test renders
    the image to text and asserts against what came out. *)
let render state =
  let body =
    match state.form with
    | None ->
        List.mapi
          (fun i row ->
            let pointer = if i = state.selected then "> " else "  " in
            let attr =
              match row with Do { disabled = Some _; _ } -> dim | Do _ | Go _ -> A.empty
            in
            I.string attr (pointer ^ label row))
          state.rows
    | Some form ->
        I.string bold form.key
        :: List.mapi
             (fun i entry ->
               let pointer = if i = form.focus then "> " else "  " in
               let mark = if entry.field.Form.required then "*" else "" in
               (* The label beside a field is its description, which
                  [ppx_deriving_jsonschema ~ocaml_doc] took from the doc comment
                  on the payload's field. *)
               let label =
                 Option.value entry.field.Form.description ~default:entry.field.Form.name
               in
               I.string A.empty
                 (Printf.sprintf "%s%s%s [%s]  %s" pointer entry.field.Form.name mark
                    (value_of entry.control) label))
             form.entries
        @ [ I.string dim "* required" ]
  in
  I.vcat
    ((I.string bold state.header :: I.void 0 1 :: body) @ [ I.void 0 1; I.string dim state.status ])
