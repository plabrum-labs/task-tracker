(** The command line the tree in [lib/command.ml] is evaluated against, and nothing else.

    The database comes from [--db] or [$TT_DB], defaulting to [sqlite3:tt.db], and it is opened
    inside the term — so this file is left with the printing and the exit code. *)

open Cmdliner

let () =
  match Cmd.eval_value (Tt.Command.main ()) with
  | Ok (`Ok (Ok output)) -> print_endline output
  (* A refusal is the object's answer, not a usage error. [some_error] is what
     the generated help already documents for it; distinct codes for [Invalid],
     [Conflict] and [Broken] would have to be declared with [~exits] to keep
     them in step, which is a real [tt]'s problem and not this spike's. *)
  | Ok (`Ok (Error e)) ->
      prerr_endline (Tt.Error.to_string e);
      exit Cmd.Exit.some_error
  | Ok (`Help | `Version) -> ()
  | Error (`Parse | `Term) -> exit Cmd.Exit.cli_error
  | Error `Exn -> exit Cmd.Exit.internal_error
