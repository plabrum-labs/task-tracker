(** The terminal the state machine in [lib/tui.ml] is driven from, and nothing else.

    Draw, wait for a key, decide what it means, apply it. The three steps are three functions in
    [lib/tui.ml] and none of them opens a terminal, which is what lets the tests drive the same loop
    with no pty in it.

    [~nosig:false] leaves ISIG alone, so Ctrl-C interrupts the process the way it does everywhere
    else rather than arriving as a keystroke nothing handles.

    The database comes from [$TT_DB], defaulting to [sqlite3:tt.db], so the TUI and the CLI look at
    the same rows. *)

let run conn =
  let term = Notty_unix.Term.create ~nosig:false () in
  let rec loop state =
    Notty_unix.Term.image term (Tt.Tui.render state);
    match Notty_unix.Term.event term with
    | `End -> ()
    | `Key key ->
        let state = Tt.Tui.apply conn state (Tt.Tui.on_key state key) in
        if state.Tt.Tui.quit then () else loop state
    | `Resize _ | `Mouse _ | `Paste _ -> loop state
  in
  loop (Tt.Tui.start conn);
  Notty_unix.Term.release term

let () =
  let uri = Option.value (Sys.getenv_opt "TT_DB") ~default:"sqlite3:tt.db" in
  match
    Result.bind (Tt.Store.connect uri) (fun conn ->
        Result.map (fun () -> conn) (Tt.Store.initialise conn))
  with
  | Error e -> prerr_endline (Tt.Store.show_error e)
  | Ok conn -> run conn
