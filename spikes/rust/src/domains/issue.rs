//! An issue, always read through the project it belongs to.
//!
//! `project_slug` comes from the read rather than from a column — it is never
//! written, and it is what makes a list row printable without a second query
//! per row. It is on the domain type because the join puts it there:
//! [`services::issues`] selects it alongside the issue's own columns, so unlike
//! the OCaml side it is not a pairing done afterwards in the host language.
//!
//! The file is named after its own directory, so it is the domain's interface:
//! the object lives here, and `services` and `actions` hang off it as
//! submodules — the queries that name the table, and the actions offered over
//! the object.

use schemars::JsonSchema;
use sea_orm::entity::prelude::{DeriveActiveEnum, EnumIter};
use serde::{Deserialize, Serialize};

pub mod actions;
pub mod schemas;
pub mod services;

// One `#[serde(rename_all)]` gives the decoder, the encoder and the schema,
// because `serde` and `schemars` share `serde_derive_internals` and read the
// same attribute. That is finding 1 at its sharpest, and it is why nothing here
// corresponds to the OCaml spike's `Enum` functor.
//
// What it does not give is the SQL representation. `DeriveActiveEnum`'s
// `string_value` is a second table of strings, unrelated by the type system to
// serde's: write `#[sea_orm(string_value = "Todo")]` beside
// `rename_all = "snake_case"` and both derives are satisfied while the column
// and the wire disagree. The two tables must agree, and the only thing that
// says so is the round-trip assertion in `tests/store.rs`.
//
// The variants carry no doc comments, deliberately. `schemars` emits a
// documented variant as a `{"const": …, "description": …}` arm and undocumented
// ones as one `enum` array, so a single doc comment splits the enum into a
// `oneOf` of two shapes and `form::of_schema` stops seeing a selector.
//
// The prose is `//` rather than `///` for the same reason: `schemars` puts a
// type's doc comment in the schema's `description`, where an agent reads it, so
// a note written for whoever maintains this file would be shipped as if it were
// written for the caller.

/// Where an issue is up to.
#[derive(
    Debug,
    Clone,
    Copy,
    PartialEq,
    Eq,
    Serialize,
    Deserialize,
    JsonSchema,
    EnumIter,
    DeriveActiveEnum,
)]
#[serde(rename_all = "snake_case")]
#[sea_orm(rs_type = "String", db_type = "Text")]
pub enum Status {
    #[sea_orm(string_value = "todo")]
    Todo,
    #[sea_orm(string_value = "doing")]
    Doing,
    #[sea_orm(string_value = "done")]
    Done,
}

// The column is an INTEGER, so one type has three representations: `"high"` on
// the wire, `1` in SQL. The integer is what `ORDER BY priority DESC` sorts by,
// which is why the column's type is not only a storage decision.

/// How far up the list an issue sorts.
#[derive(
    Debug,
    Clone,
    Copy,
    PartialEq,
    Eq,
    Serialize,
    Deserialize,
    JsonSchema,
    EnumIter,
    DeriveActiveEnum,
)]
#[serde(rename_all = "snake_case")]
#[sea_orm(rs_type = "i32", db_type = "Integer")]
pub enum Priority {
    #[sea_orm(num_value = 0)]
    Normal,
    #[sea_orm(num_value = 1)]
    High,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Issue {
    pub id: i64,
    pub project_id: i64,
    /// From the join. No action writes it.
    pub project_slug: String,
    pub title: String,
    pub body: String,
    pub status: Status,
    pub priority: Priority,
    pub status_note: Option<String>,
    pub created_at: String,
    pub updated_at: String,
}

/// What a creator returns and the store turns into a row. No `id` and no
/// stamps: those are the store's to assign, and a type that could carry them
/// would let a caller propose one.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Draft {
    pub project_id: i64,
    pub title: String,
    pub body: String,
    pub priority: Priority,
}

impl Issue {
    pub fn subject(&self) -> String {
        format!("issue {}", self.id)
    }
}
