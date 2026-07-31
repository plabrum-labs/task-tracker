//! A small task tracker, in Rust, built to be compared against the OCaml spike
//! beside it line for line.
//!
//! The layering is the one the root `CLAUDE.md` describes, and each layer is
//! the only file that knows about the thing below it:
//!
//! - `action.rs` — what an action is. Typed, and free of any transport.
//! - `wire.rs` — the JSON edge both frontends talk to.
//! - `issue.rs`, `project.rs`, `deleted.rs` — the domain objects. No column and
//!   no query anywhere in them.
//! - `issue_actions.rs`, `project_actions.rs` — the declarations, and the
//!   registration lists.
//! - `store.rs` — the SQL edge. The tables are private to it.
//! - `form.rs`, `cli.rs`, `tui.rs` — the two frontends, both built from what an
//!   action advertises rather than from any action's name.
//!
//! See `../README.md` for what this is comparing against.

pub mod action;
pub mod cli;
pub mod clock;
pub mod deleted;
pub mod error;
pub mod form;
pub mod issue;
pub mod issue_actions;
pub mod project;
pub mod project_actions;
pub mod store;
pub mod tui;
pub mod wire;

pub use action::{Action, Creator, Offered};
pub use deleted::Deleted;
pub use error::Error;
pub use issue::Issue;
pub use project::Project;
pub use wire::{CreatorEntry, CreatorGroup, Entry, Group};
