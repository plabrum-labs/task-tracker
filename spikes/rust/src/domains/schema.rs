//! The tables, and the only file that names them into being.
//!
//! Two things live here that cannot be split by domain. The sea-orm entities
//! are coupled: an issue `belongs_to` a project, so the typed relation names
//! both `Entity` types and neither can be private to its own directory the way
//! SQLAlchemy's registry lets the OCaml-adjacent design keep them. And the raw
//! DDL declares both tables in one text, because the issues table's foreign key
//! into projects means they cannot be stated apart.
//!
//! So they sit together, but `pub(in crate::domains)`: a domain's `services.rs`
//! can name its own table through these, and `frontend/` cannot name any table
//! at all. That is the seal [`crate::platform::db`] describes — the entity
//! types, the raw DDL and the row structs are reachable from the services and
//! from nowhere else.

use sea_orm::{IdenStatic, Iterable};

use crate::platform::db::{self, Db, StoreError};

/// The tables, as sea-orm entities and as the DDL that actually creates them.
pub(in crate::domains) mod entities {
    pub mod projects {
        use sea_orm::entity::prelude::*;

        #[derive(Clone, Debug, PartialEq, Eq, DeriveEntityModel)]
        #[sea_orm(table_name = "projects")]
        pub struct Model {
            #[sea_orm(primary_key)]
            pub id: i64,
            pub slug: String,
            pub title: String,
            pub body: String,
            pub status: crate::domains::project::Status,
            pub created_at: String,
            pub updated_at: String,
            pub deleted_at: Option<String>,
        }

        #[derive(Copy, Clone, Debug, EnumIter, DeriveRelation)]
        pub enum Relation {
            #[sea_orm(has_many = "super::issues::Entity")]
            Issues,
        }

        impl Related<super::issues::Entity> for Entity {
            fn to() -> RelationDef {
                Relation::Issues.def()
            }
        }

        impl ActiveModelBehavior for ActiveModel {}
    }

    pub mod issues {
        use sea_orm::entity::prelude::*;

        #[derive(Clone, Debug, PartialEq, Eq, DeriveEntityModel)]
        #[sea_orm(table_name = "issues")]
        pub struct Model {
            #[sea_orm(primary_key)]
            pub id: i64,
            pub project_id: i64,
            pub title: String,
            pub body: String,
            pub status: crate::domains::issue::Status,
            pub priority: crate::domains::issue::Priority,
            pub status_note: Option<String>,
            pub created_at: String,
            pub updated_at: String,
            pub deleted_at: Option<String>,
        }

        #[derive(Copy, Clone, Debug, EnumIter, DeriveRelation)]
        pub enum Relation {
            #[sea_orm(
                belongs_to = "super::projects::Entity",
                from = "Column::ProjectId",
                to = "super::projects::Column::Id",
                on_delete = "Cascade"
            )]
            Project,
        }

        impl Related<super::projects::Entity> for Entity {
            fn to() -> RelationDef {
                Relation::Project.def()
            }
        }

        impl ActiveModelBehavior for ActiveModel {}
    }
}

pub(in crate::domains) use entities::{issues, projects};

/// The schema, as raw DDL, and the whole of it.
///
/// `Schema::create_table_from_entity` is deliberately not used. It emits
/// `varchar` for a `String`, drops `ON DELETE CASCADE`, and has no way to say
/// `STRICT`, `CHECK` or `CREATE INDEX` at all — the same gaps petrol leaves on
/// the OCaml side. A schema half-built by the entity layer and half by raw SQL
/// would be worse than one honest source, so the entity structs declare the
/// columns and this declares the table. `tests/store.rs` asserts that the two
/// agree by reading `sqlite_master`.
///
/// The partial unique index is what makes a slug reusable once its project is
/// deleted, and it is the guarantee behind `createProject`'s refusal rather
/// than a duplicate of it.
const DDL: &[&str] = &[
    "CREATE TABLE IF NOT EXISTS projects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        slug TEXT NOT NULL,
        title TEXT NOT NULL,
        body TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('active', 'archived')),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        deleted_at TEXT
    ) STRICT",
    "CREATE TABLE IF NOT EXISTS issues (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
        title TEXT NOT NULL,
        body TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('todo', 'doing', 'done')),
        priority INTEGER NOT NULL CHECK (priority IN (0, 1)),
        status_note TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        deleted_at TEXT
    ) STRICT",
    "CREATE UNIQUE INDEX IF NOT EXISTS projects_slug_live
        ON projects (slug) WHERE deleted_at IS NULL",
    "CREATE INDEX IF NOT EXISTS issues_by_project
        ON issues (project_id, status) WHERE deleted_at IS NULL",
    "PRAGMA foreign_keys = ON",
];

/// Safe to run on a database that already has the tables. Delegates to
/// [`crate::platform::db::apply_ddl`], which knows how to run a DDL text and
/// nothing about what is in this one.
pub async fn initialise(db: &Db) -> Result<(), StoreError> {
    db::apply_ddl(db, DDL).await
}

/// The columns each entity declares, in declaration order. Paired with
/// [`crate::platform::db::emitted_ddl`] this is the assertion that the raw DDL
/// and the entity structs describe one table.
pub fn declared_columns() -> Vec<(&'static str, Vec<String>)> {
    vec![
        (
            "projects",
            projects::Column::iter()
                .map(|c| c.as_str().to_string())
                .collect(),
        ),
        (
            "issues",
            issues::Column::iter()
                .map(|c| c.as_str().to_string())
                .collect(),
        ),
    ]
}
