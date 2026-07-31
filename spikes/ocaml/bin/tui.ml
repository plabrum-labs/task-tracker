(** The terminal the state machine in [lib/frontend/tui.ml] is driven from, and nothing else.

    Draw, wait for a key, decide what it means, apply it. The three steps are three functions in
    [lib/frontend/tui.ml] and none of them opens a terminal, which is what lets the tests drive the
    same loop with no pty in it.

    [~nosig:false] leaves ISIG alone, so Ctrl-C interrupts the process the way it does everywhere
    else rather than arriving as a keystroke nothing handles.

    The database comes from [$TT_DB], defaulting to [sqlite3:tt.db], so the TUI and the CLI look at
    the same rows. *)

let run conn =
  let term = Notty_unix.Term.create ~nosig:false () in
  let rec loop state =
    Notty_unix.Term.image term (Tt.Frontend.Tui.render state);
    match Notty_unix.Term.event term with
    | `End -> ()
    | `Key key ->
        let state = Tt.Frontend.Tui.apply conn state (Tt.Frontend.Tui.on_key state key) in
        if state.Tt.Frontend.Tui.quit then () else loop state
    | `Resize _ | `Mouse _ | `Paste _ -> loop state
  in
  loop (Tt.Frontend.Tui.start conn);
  Notty_unix.Term.release term

let () =
  let uri = Option.value (Sys.getenv_opt "TT_DB") ~default:"sqlite3:tt.db" in
  match
    Result.bind (Tt.Platform.Db.connect uri) (fun conn ->
        Result.map (fun () -> conn) (Tt.Platform.Db.apply_ddl conn Tt.Domains.Schema.ddl))
  with
  | Error e -> prerr_endline (Tt.Platform.Db.show_error e)
  | Ok conn -> run conn
