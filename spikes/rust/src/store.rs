//! The SQL edge. Everything that knows about a database is here.
//!
//! The split is the same one [`crate::wire`] makes for JSON: [`Issue`] and
//! [`Project`] name no column and no query. What keeps the base tables out of
//! reach is `mod entities;` — private, so the entity types, the raw DDL and the
//! row structs cannot be named from outside this file and every read below is
//! guaranteed to carry its liveness predicate by construction. A caller cannot
//! write the query that forgets the soft-delete filter, because it cannot write
//! a query at all.
//!
//! That is what `store.mli` buys the OCaml spike, bought with one keyword
//! instead of a second copy of every signature. It is also what stands in for
//! the SQL views neither side declares: [`issues`] is "live issues of live
//! projects" as a function, and it is the only way to ask for issues.
//!
//! Every read is a fresh query. Nothing is cached, so what a frontend rendered
//! is a snapshot and a write is checked against the row as it is now.

use std::collections::HashMap;
use std::fmt;

use sea_orm::ActiveValue::{Set, Unchanged};
use sea_orm::sea_query::Expr;
use sea_orm::{
    ActiveModelTrait, ColumnTrait, ConnectionTrait, Database, DatabaseConnection, DbBackend, DbErr,
    EntityTrait, FromQueryResult, IdenStatic, Iterable, JoinType, QueryFilter, QueryOrder,
    QuerySelect, RelationTrait, Statement,
};

use crate::clock;
use crate::deleted::Deleted;
use crate::issue::{self, Issue};
use crate::project::{self, Project};

pub type Db = DatabaseConnection;

/// One error type for the whole edge, separate from [`crate::Error`].
///
/// [`crate::Error`] is the domain's refusals and stays free of anything
/// transport- or storage-shaped; nothing a caller can do about a `DbErr` is
/// anything an action refused.
///
/// `MissingAfterInsert` is the case with no row to blame: a create writes, then
/// loads the row back through the same projection every other read uses, and
/// that load can only come up empty if something removed the row in between.
#[derive(Debug)]
pub enum StoreError {
    Db(DbErr),
    MissingAfterInsert(&'static str),
}

impl fmt::Display for StoreError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            StoreError::Db(e) => write!(f, "{e}"),
            StoreError::MissingAfterInsert(what) => {
                write!(f, "{what} vanished between the insert and the read")
            }
        }
    }
}

impl std::error::Error for StoreError {}

impl From<DbErr> for StoreError {
    fn from(e: DbErr) -> Self {
        StoreError::Db(e)
    }
}

/// The tables, as sea-orm entities and as the DDL that actually creates them.
///
/// Private, which is the whole point of the module — see this file's header.
mod entities {
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
            pub status: crate::project::Status,
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
            pub status: crate::issue::Status,
            pub priority: crate::issue::Priority,
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

use entities::{issues, projects};

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

/// `sqlite::memory:` is opened shared-cache by sqlx and sea-orm caps a SQLite
/// pool at one connection, so a pooled in-memory database is one database and a
/// test fixture is one `connect`.
pub async fn connect(url: &str) -> Result<Db, StoreError> {
    Ok(Database::connect(url).await?)
}

/// Safe to run on a database that already has the tables.
pub async fn initialise(db: &Db) -> Result<(), StoreError> {
    for sql in DDL {
        db.execute_unprepared(sql).await?;
    }
    Ok(())
}

// --- reading --------------------------------------------------------------

/// An issue and the slug of the project it belongs to, in one row.
///
/// The join is what puts `project_slug` on [`Issue`]. Unlike the OCaml side
/// there is no second query and no pairing done afterwards in the host
/// language: `column_as` names the borrowed column and `FromQueryResult` reads
/// a row naming columns of both tables.
#[derive(Debug, FromQueryResult)]
struct IssueRow {
    id: i64,
    project_id: i64,
    project_slug: String,
    title: String,
    body: String,
    status: issue::Status,
    priority: issue::Priority,
    status_note: Option<String>,
    created_at: String,
    updated_at: String,
    deleted_at: Option<String>,
}

impl IssueRow {
    fn split(self) -> (Issue, Option<String>) {
        (
            Issue {
                id: self.id,
                project_id: self.project_id,
                project_slug: self.project_slug,
                title: self.title,
                body: self.body,
                status: self.status,
                priority: self.priority,
                status_note: self.status_note,
                created_at: self.created_at,
                updated_at: self.updated_at,
            },
            self.deleted_at,
        )
    }
}

#[derive(Debug, FromQueryResult)]
struct CountRow {
    project_id: i64,
    status: issue::Status,
    n: i64,
}

/// The counts, as one grouped query folded in Rust.
///
/// `COUNT(…) FILTER (…)` is reachable only through `Expr::cust`, which is
/// dropping to raw SQL — the same position petrol leaves OCaml in, so the fold
/// is kept and the equivalence recorded.
async fn counts(db: &Db) -> Result<HashMap<i64, (i64, i64, i64)>, StoreError> {
    let rows = issues::Entity::find()
        .select_only()
        .column(issues::Column::ProjectId)
        .column(issues::Column::Status)
        .column_as(Expr::col(issues::Column::Id).count(), "n")
        .filter(issues::Column::DeletedAt.is_null())
        .group_by(issues::Column::ProjectId)
        .group_by(issues::Column::Status)
        .into_model::<CountRow>()
        .all(db)
        .await?;

    let mut tally: HashMap<i64, (i64, i64, i64)> = HashMap::new();
    for row in rows {
        let entry = tally.entry(row.project_id).or_default();
        match row.status {
            issue::Status::Todo => entry.0 += row.n,
            issue::Status::Doing => entry.1 += row.n,
            issue::Status::Done => entry.2 += row.n,
        }
    }
    Ok(tally)
}

fn to_project(model: projects::Model, counts: &HashMap<i64, (i64, i64, i64)>) -> Project {
    let (todo, doing, done) = counts.get(&model.id).copied().unwrap_or_default();
    Project {
        id: model.id,
        slug: model.slug,
        title: model.title,
        body: model.body,
        status: model.status,
        todo,
        doing,
        done,
        created_at: model.created_at,
        updated_at: model.updated_at,
    }
}

async fn load_projects(
    db: &Db,
    query: sea_orm::Select<projects::Entity>,
) -> Result<Vec<(Project, Option<String>)>, StoreError> {
    let models = query
        .order_by_asc(projects::Column::CreatedAt)
        .all(db)
        .await?;
    let counts = counts(db).await?;
    Ok(models
        .into_iter()
        .map(|model| {
            let deleted_at = model.deleted_at.clone();
            (to_project(model, &counts), deleted_at)
        })
        .collect())
}

/// High priority first, then oldest first — `ORDER BY priority DESC, created_at
/// ASC`, in SQL.
///
/// This is the one place the Rust spike deliberately does not mirror the OCaml
/// one. Petrol's `order_by` takes a direction for the whole clause and a second
/// call overwrites the first, so the OCaml side moves the rest of the order into
/// a pure `Issue.pick_order`. sea-orm composes the two calls, so there is
/// nothing here for a `pick_order` to do.
fn ordered_issues() -> sea_orm::Select<issues::Entity> {
    issues::Entity::find()
        .join(JoinType::InnerJoin, issues::Relation::Project.def())
        .order_by_desc(issues::Column::Priority)
        .order_by_asc(issues::Column::CreatedAt)
}

async fn load_issues(
    db: &Db,
    query: sea_orm::Select<issues::Entity>,
) -> Result<Vec<(Issue, Option<String>)>, StoreError> {
    Ok(query
        .column_as(projects::Column::Slug, "project_slug")
        .into_model::<IssueRow>()
        .all(db)
        .await?
        .into_iter()
        .map(IssueRow::split)
        .collect())
}

/// Live projects in creation order, each carrying its issue counts. The counts
/// are what [`crate::project_actions`]' refusals read, so a project loaded any
/// other way would offer a menu it could not justify.
pub async fn projects(db: &Db) -> Result<Vec<Project>, StoreError> {
    let rows = load_projects(
        db,
        projects::Entity::find().filter(projects::Column::DeletedAt.is_null()),
    )
    .await?;
    Ok(rows.into_iter().map(|(project, _)| project).collect())
}

pub async fn project(db: &Db, slug: &str) -> Result<Option<Project>, StoreError> {
    let rows = load_projects(
        db,
        projects::Entity::find()
            .filter(projects::Column::DeletedAt.is_null())
            .filter(projects::Column::Slug.eq(slug)),
    )
    .await?;
    Ok(rows.into_iter().next().map(|(project, _)| project))
}

async fn project_by_id(db: &Db, id: i64) -> Result<Option<Project>, StoreError> {
    let rows = load_projects(
        db,
        projects::Entity::find()
            .filter(projects::Column::DeletedAt.is_null())
            .filter(projects::Column::Id.eq(id)),
    )
    .await?;
    Ok(rows.into_iter().next().map(|(project, _)| project))
}

/// Live issues of a live project.
///
/// Liveness is derived rather than stored: this requires both the issue's and
/// the project's `deleted_at` to be null, so soft-deleting a project hides its
/// issues with one row written and restoring it brings back exactly the issues
/// that were not deleted in their own right.
pub async fn issues(db: &Db, project_slug: &str) -> Result<Vec<Issue>, StoreError> {
    let rows = load_issues(
        db,
        ordered_issues()
            .filter(issues::Column::DeletedAt.is_null())
            .filter(projects::Column::DeletedAt.is_null())
            .filter(projects::Column::Slug.eq(project_slug)),
    )
    .await?;
    Ok(rows.into_iter().map(|(issue, _)| issue).collect())
}

/// The same liveness rule as [`issues`]: an issue of a deleted project is not
/// found here either.
pub async fn issue(db: &Db, id: i64) -> Result<Option<Issue>, StoreError> {
    let rows = load_issues(
        db,
        ordered_issues()
            .filter(issues::Column::DeletedAt.is_null())
            .filter(projects::Column::DeletedAt.is_null())
            .filter(issues::Column::Id.eq(id)),
    )
    .await?;
    Ok(rows.into_iter().next().map(|(issue, _)| issue))
}

async fn issue_by_id(db: &Db, id: i64) -> Result<Option<Issue>, StoreError> {
    let rows = load_issues(
        db,
        ordered_issues()
            .filter(issues::Column::DeletedAt.is_null())
            .filter(issues::Column::Id.eq(id)),
    )
    .await?;
    Ok(rows.into_iter().next().map(|(issue, _)| issue))
}

/// The trash is by the row's own `deleted_at` and nothing else.
///
/// A project going does not put its issues here — it hides them, because
/// [`issues`] requires a live project. That is what makes restoring a project
/// bring back exactly the issues that were not deleted in their own right: one
/// row was written on the way out, so one row is cleared on the way back.
pub async fn trashed_projects(db: &Db) -> Result<Vec<Deleted<Project>>, StoreError> {
    let rows = load_projects(
        db,
        projects::Entity::find().filter(projects::Column::DeletedAt.is_not_null()),
    )
    .await?;
    Ok(rows
        .into_iter()
        .filter_map(|(project, deleted_at)| {
            Some(Deleted {
                inner: project,
                deleted_at: deleted_at?,
            })
        })
        .collect())
}

/// An issue in the trash whose project also went is still worth printing, so
/// this is the one read that asks nothing of the project but its slug.
pub async fn trashed_issues(db: &Db) -> Result<Vec<Deleted<Issue>>, StoreError> {
    let rows = load_issues(
        db,
        ordered_issues().filter(issues::Column::DeletedAt.is_not_null()),
    )
    .await?;
    Ok(rows
        .into_iter()
        .filter_map(|(issue, deleted_at)| {
            Some(Deleted {
                inner: issue,
                deleted_at: deleted_at?,
            })
        })
        .collect())
}

/// What the database actually holds, so a test can hold the emitted schema
/// against the columns the entities declare.
pub async fn emitted_ddl(db: &Db) -> Result<Vec<String>, StoreError> {
    Ok(db
        .query_all(Statement::from_string(
            DbBackend::Sqlite,
            "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name",
        ))
        .await?
        .iter()
        .filter_map(|row| row.try_get_by_index::<String>(0).ok())
        .collect())
}

/// The columns each entity declares, in declaration order. Paired with
/// [`emitted_ddl`] this is the assertion that the raw DDL and the entity
/// structs describe one table.
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

// --- writing --------------------------------------------------------------

/// Persisting what an action returned. `Action::run` produced the new value;
/// this only writes it, so the enforcement point stays where `action.rs` put
/// it.
///
/// `updated_at` is stamped here rather than by the action, which keeps every
/// `execute` a pure function of the object and its payload — the thing that
/// lets the whole action layer be tested with no clock and no database.
///
/// `slug` is not written by anything: no action changes it, and the partial
/// unique index it sits under is the last thing an edit should be able to trip.
pub async fn update_project(db: &Db, project: &Project) -> Result<(), StoreError> {
    projects::ActiveModel {
        id: Unchanged(project.id),
        title: Set(project.title.clone()),
        body: Set(project.body.clone()),
        status: Set(project.status),
        updated_at: Set(clock::now()),
        ..Default::default()
    }
    .update(db)
    .await?;
    Ok(())
}

/// `project_id` is not written either: an issue does not move between projects.
pub async fn update_issue(db: &Db, issue: &Issue) -> Result<(), StoreError> {
    issues::ActiveModel {
        id: Unchanged(issue.id),
        title: Set(issue.title.clone()),
        body: Set(issue.body.clone()),
        status: Set(issue.status),
        priority: Set(issue.priority),
        status_note: Set(issue.status_note.clone()),
        updated_at: Set(clock::now()),
        ..Default::default()
    }
    .update(db)
    .await?;
    Ok(())
}

/// The soft delete, which is an update like any other — which is exactly why
/// the type of what an action returned cannot say which of these to call.
pub async fn delete_project(db: &Db, project: &Project) -> Result<(), StoreError> {
    let stamp = clock::now();
    projects::ActiveModel {
        id: Unchanged(project.id),
        deleted_at: Set(Some(stamp.clone())),
        updated_at: Set(stamp),
        ..Default::default()
    }
    .update(db)
    .await?;
    Ok(())
}

pub async fn delete_issue(db: &Db, issue: &Issue) -> Result<(), StoreError> {
    let stamp = clock::now();
    issues::ActiveModel {
        id: Unchanged(issue.id),
        deleted_at: Set(Some(stamp.clone())),
        updated_at: Set(stamp),
        ..Default::default()
    }
    .update(db)
    .await?;
    Ok(())
}

/// Writing NULL is `Set(None)` — the same constructor an assignment uses, so a
/// nullable column has one write path rather than petrol's two.
pub async fn restore_project(db: &Db, deleted: &Deleted<Project>) -> Result<(), StoreError> {
    projects::ActiveModel {
        id: Unchanged(deleted.inner.id),
        deleted_at: Set(None),
        updated_at: Set(clock::now()),
        ..Default::default()
    }
    .update(db)
    .await?;
    Ok(())
}

pub async fn restore_issue(db: &Db, deleted: &Deleted<Issue>) -> Result<(), StoreError> {
    issues::ActiveModel {
        id: Unchanged(deleted.inner.id),
        deleted_at: Set(None),
        updated_at: Set(clock::now()),
        ..Default::default()
    }
    .update(db)
    .await?;
    Ok(())
}

/// Creation. `insert` returns the `Model` carrying the assigned id, with no
/// second query and no `last_insert_rowid` — one of the two petrol gaps that
/// simply are not here.
///
/// The load that follows is for the projection rather than for the id: a
/// [`Project`] carries counts and an [`Issue`] carries its project's slug, and
/// neither is on the row that was just written. Reading it back through the
/// same projection every other read uses is what makes the returned value the
/// stored object rather than a hopeful copy of the draft.
pub async fn create_project(db: &Db, draft: project::Draft) -> Result<Project, StoreError> {
    let stamp = clock::now();
    let model = projects::ActiveModel {
        slug: Set(draft.slug),
        title: Set(draft.title),
        body: Set(draft.body),
        status: Set(project::Status::Active),
        created_at: Set(stamp.clone()),
        updated_at: Set(stamp),
        deleted_at: Set(None),
        ..Default::default()
    }
    .insert(db)
    .await?;

    project_by_id(db, model.id)
        .await?
        .ok_or(StoreError::MissingAfterInsert("the project"))
}

pub async fn create_issue(db: &Db, draft: issue::Draft) -> Result<Issue, StoreError> {
    let stamp = clock::now();
    let model = issues::ActiveModel {
        project_id: Set(draft.project_id),
        title: Set(draft.title),
        body: Set(draft.body),
        status: Set(issue::Status::Todo),
        priority: Set(draft.priority),
        status_note: Set(None),
        created_at: Set(stamp.clone()),
        updated_at: Set(stamp),
        deleted_at: Set(None),
        ..Default::default()
    }
    .insert(db)
    .await?;

    issue_by_id(db, model.id)
        .await?
        .ok_or(StoreError::MissingAfterInsert("the issue"))
}
