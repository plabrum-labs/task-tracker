//! The framework, with no persisted object in it.
//!
//! Everything an action, a wire, a form or the SQL edge needs, and nothing
//! about issues or projects: `db.rs` runs a query and names no table, `action`,
//! `wire` and `form` are the erased path, and `error`, `clock` and `deleted`
//! are the small shared types. A domain reaches down into this; nothing here
//! reaches back up.

pub mod action;
pub mod clock;
pub mod db;
pub mod deleted;
pub mod error;
pub mod form;
pub mod wire;
