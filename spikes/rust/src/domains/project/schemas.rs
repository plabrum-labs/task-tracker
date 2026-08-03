//! The payload shapes a project's actions accept — the wire contract, in one
//! place.
//!
//! See `../issue/schemas.rs`: one type per action that takes arguments, each
//! deriving its decoder and its schema together, with the field descriptions a
//! frontend shows sitting beside the shapes. `addIssue` carries an issue's
//! priority, so this refers across to [`issue::Priority`] — the same direction
//! `services.rs` already depends in. As on the issue side, extracting these is
//! symmetry with the OCaml `schemas.ml`, not something Rust forces.

use schemars::JsonSchema;
use serde::Deserialize;

use crate::domains::issue;
use crate::domains::project::Status;

#[derive(Debug, Clone, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct EditTitlePayload {
    /// What to call the project.
    pub title: String,
}

#[derive(Debug, Clone, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct EditBodyPayload {
    /// The project's description. Blank clears it.
    pub body: String,
}

#[derive(Debug, Clone, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct EditStatusPayload {
    /// Whether the project is still being worked on.
    pub status: Status,
}

#[derive(Debug, Clone, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct AddIssuePayload {
    /// What to call the issue.
    pub title: String,
    /// What the issue is about.
    pub body: Option<String>,
    /// How far up the list it sorts. Defaults to normal.
    pub priority: Option<issue::Priority>,
}

#[derive(Debug, Clone, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct CreateProjectPayload {
    /// The short name the project is addressed by.
    pub slug: String,
    /// What to call the project.
    pub title: Option<String>,
    /// What the project is about.
    pub body: Option<String>,
}
