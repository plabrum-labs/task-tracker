//! The payload shapes an issue's actions accept — the wire contract, in one
//! place.
//!
//! One type per action that takes arguments, each deriving both its decoder
//! (`Deserialize`) and its advertised JSON Schema (`JsonSchema`) from the same
//! definition, with the field doc comments `schemars` shows a caller sitting
//! beside the shapes. An action in `actions.rs` names `type Payload =
//! schemas::<Name>` and states nothing about how it is decoded.
//!
//! This split is symmetry with the OCaml side, not a Rust need: OCaml pulls its
//! payloads into a `schemas.ml` because the `[%action]` ppx wants the type
//! named once, and here it is just as workable to keep a struct beside its
//! action. Holding the two spikes to the same shape is the whole point of the
//! exercise, so the payloads sit here too.

use schemars::JsonSchema;
use serde::Deserialize;

use crate::domains::issue::{Priority, Status};

#[derive(Debug, Clone, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct EditTitlePayload {
    /// What to call the issue.
    pub title: String,
}

#[derive(Debug, Clone, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct EditBodyPayload {
    /// The issue's description. Blank clears it.
    pub body: String,
}

#[derive(Debug, Clone, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct EditStatusPayload {
    /// Where the issue is up to.
    pub status: Status,
    /// What to record about the move.
    pub note: Option<String>,
}

#[derive(Debug, Clone, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct EditPriorityPayload {
    /// How far up the list the issue sorts.
    pub priority: Priority,
}
