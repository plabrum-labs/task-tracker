//! A project, and the issues under it counted.
//!
//! The counts are part of the projection rather than something a caller fetches
//! when it needs them, because `editStatus` and `delete` both refuse on them.
//! An availability hook is a pure function of the object, so anything a hook
//! reads has to be in the object — computing the counts per row is what stops a
//! list from offering a menu it cannot justify.

use schemars::JsonSchema;
use sea_orm::entity::prelude::{DeriveActiveEnum, EnumIter};
use serde::{Deserialize, Serialize};

use crate::platform::deleted::Deleted;

pub mod actions;
pub mod schemas;
pub mod services;

// The same one attribute, and the same second table beside it. See
// `issue.rs` for what the pair costs, and for why this comment is not a doc
// comment.

/// Whether a project is still being worked on.
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
    #[sea_orm(string_value = "active")]
    Active,
    #[sea_orm(string_value = "archived")]
    Archived,
}

/// `done` is not a Rust keyword, so the field is `done` where the OCaml record
/// has to write `done_`. Nothing follows from it beyond a line in the size
/// table.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Project {
    pub id: i64,
    pub slug: String,
    pub title: String,
    pub body: String,
    pub status: Status,
    pub todo: i64,
    pub doing: i64,
    pub done: i64,
    pub created_at: String,
    pub updated_at: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Draft {
    pub slug: String,
    pub title: String,
    pub body: String,
}

/// What `restore` is offered against.
///
/// The trash row alone is not enough: the partial unique index covers live rows
/// only, so a slug freed by a delete can be taken again and bringing the old
/// project back would then collide. An availability hook is a pure function of
/// its object, so what the hook reads has to be in the object — the same reason
/// [`Project`] carries its counts, and the same reason `createProject`'s parent
/// is the list rather than nothing. Without this the refusal is a UNIQUE
/// constraint violation surfacing as a database error with no sentence in it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Restorable {
    pub deleted: Deleted<Project>,
    pub live: Vec<Project>,
}

impl Project {
    pub fn subject(&self) -> String {
        format!("project {}", self.slug)
    }

    pub fn issue_count(&self) -> i64 {
        self.todo + self.doing + self.done
    }
}
