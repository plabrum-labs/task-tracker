//! The command line the tree in `cli.rs` is evaluated against, and nothing
//! else: the runtime, the printing and the exit code.
//!
//! The database comes from `--db` or `$TT_DB`, defaulting to
//! `sqlite:tt.db?mode=rwc`.

use std::process::ExitCode;

use tt_spike::domains::schema;
use tt_spike::frontend::cli;
use tt_spike::platform::db;

/// A refusal is the object's answer, not a usage error, so it is neither clap's
/// 2 nor a success. 123 is what cmdliner calls `Exit.some_error`, which is what
/// the OCaml side returns for the same thing — distinct codes for `Invalid`,
/// `Conflict` and `Broken` are a real `tt`'s problem and not this spike's.
const REFUSED: u8 = 123;

#[tokio::main]
async fn main() -> ExitCode {
    let matches = cli::command().get_matches();

    let outcome = match db::connect(&cli::database(&matches)).await {
        Err(e) => Err(e.into()),
        Ok(db) => match schema::initialise(&db).await {
            Err(e) => Err(e.into()),
            Ok(()) => cli::run(&db, &matches).await,
        },
    };

    match outcome {
        Ok(output) => {
            println!("{output}");
            ExitCode::SUCCESS
        }
        Err(e) => {
            eprintln!("{e}");
            ExitCode::from(REFUSED)
        }
    }
}
