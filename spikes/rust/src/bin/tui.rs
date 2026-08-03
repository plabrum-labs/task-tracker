//! The terminal the view in `tui.rs` is rendered to, and nothing else: the
//! crossterm setup, the event loop, and the teardown.
//!
//! The loop is the whole of what this file adds — read a key, ask `tui::on_key`
//! what it means, hand the meaning to `tui::apply`, draw. Neither of the first
//! two touches a terminal or a database, which is why `tests/frontend.rs` can
//! drive the same three steps with no pty and no sleeps.

use std::io;

use crossterm::event::{self, Event, KeyEventKind};
use tt_spike::domains::schema;
use tt_spike::frontend::tui;
use tt_spike::platform::db;

#[tokio::main]
async fn main() -> io::Result<()> {
    let url = std::env::var("TT_DB").unwrap_or_else(|_| "sqlite:tt.db?mode=rwc".to_string());
    let db = match db::connect(&url).await {
        Ok(db) => db,
        Err(e) => {
            eprintln!("{e}");
            return Ok(());
        }
    };
    if let Err(e) = schema::initialise(&db).await {
        eprintln!("{e}");
        return Ok(());
    }

    let mut terminal = ratatui::init();
    let mut state = tui::start(&db).await;

    while !state.quit {
        terminal.draw(|frame| tui::render(&state, frame))?;
        // Windows reports a press and a release; taking only the press keeps
        // one keystroke from being two intents.
        if let Event::Key(key) = event::read()?
            && key.kind == KeyEventKind::Press
        {
            let intent = tui::on_key(&state, key.code);
            state = tui::apply(&db, state, intent).await;
        }
    }

    ratatui::restore();
    Ok(())
}
