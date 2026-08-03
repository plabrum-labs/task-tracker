//! A small task tracker, in Rust, built to be compared against the OCaml spike
//! beside it line for line.
//!
//! The tree is grouped rather than flat, mirroring the OCaml side's `platform`
//! / `domains` / `frontend`. Each layer is the only one that knows about the
//! thing below it:
//!
//! - `platform/` — the framework, with no persisted object in it. `action.rs`
//!   is what an action is, typed and free of any transport; `wire.rs` is the
//!   JSON edge both frontends talk to; `form.rs` is the form built from a
//!   schema; `db.rs` runs a query and names no table; `error.rs`, `clock.rs`
//!   and `deleted.rs` are the small shared types.
//! - `domains/` — one directory per persisted object. `schema.rs` names the
//!   base tables, `pub(in crate::domains)` so the domains see them and the
//!   frontends cannot. Each `issue` and `project` module holds its object, its
//!   queries (`services.rs`) and its actions.
//! - `frontend/` — the two frontends, `cli.rs` and `tui.rs`, both built from
//!   what an action advertises rather than from any action's name.
//!
//! See `../README.md` for what this is comparing against.

pub mod domains;
pub mod frontend;
pub mod platform;

pub use domains::issue::Issue;
pub use domains::project::Project;
pub use platform::action::{Action, Creator, Offered};
pub use platform::deleted::Deleted;
pub use platform::error::Error;
pub use platform::wire::{CreatorEntry, CreatorGroup, Entry, Group};
