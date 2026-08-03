//! The issues table, and the only file that names it.
//!
//! The split is the same one [`crate::platform::wire`] makes for JSON:
//! [`Issue`] names no column and no query. The row struct, the projections and
//! the writes live here; the entities they run against are
//! [`crate::domains::schema`]'s, reachable from a domain's services and from
//! nowhere else. Every read below carries its liveness predicate by
//! construction, because a frontend cannot write a query at all.
//!
//! It names the projects entity too, in the join and in the liveness predicate.
//! A join is a join; what the directory split buys is that no *project* query
//! lives here, not that the word never appears.
//!
//! Every read is a fresh query. Nothing is cached, so what a frontend rendered
//! is a snapshot and a write is checked against the row as it is now.

use std::collections::HashMap;

use sea_orm::ActiveValue::{Set, Unchanged};
use sea_orm::sea_query::Expr;
use sea_orm::{
    ActiveModelTrait, ColumnTrait, ConnectionTrait, EntityTrait, FromQueryResult, JoinType,
    QueryFilter, QueryOrder, QuerySelect, RelationTrait,
};

use crate::domains::issue::{self, Issue};
use crate::domains::schema::{issues, projects};
use crate::platform::clock;
use crate::platform::db::StoreError;
use crate::platform::deleted::Deleted;

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
/// It reads the issues table, so it is the issues' to answer even though a
/// project is what displays it — see [`crate::domains::project::services`] for
/// the caller. `COUNT(…) FILTER (…)` is reachable only through `Expr::cust`,
/// which is dropping to raw SQL — the same position petrol leaves OCaml in, so
/// the fold is kept and the equivalence recorded.
pub(in crate::domains) async fn counts<C: ConnectionTrait>(
    db: &C,
) -> Result<HashMap<i64, (i64, i64, i64)>, StoreError> {
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

async fn load_issues<C: ConnectionTrait>(
    db: &C,
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

/// Live issues of a live project.
///
/// Liveness is derived rather than stored: this requires both the issue's and
/// the project's `deleted_at` to be null, so soft-deleting a project hides its
/// issues with one row written and restoring it brings back exactly the issues
/// that were not deleted in their own right.
pub async fn issues<C: ConnectionTrait>(
    db: &C,
    project_slug: &str,
) -> Result<Vec<Issue>, StoreError> {
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
pub async fn issue<C: ConnectionTrait>(db: &C, id: i64) -> Result<Option<Issue>, StoreError> {
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

async fn issue_by_id<C: ConnectionTrait>(db: &C, id: i64) -> Result<Option<Issue>, StoreError> {
    let rows = load_issues(
        db,
        ordered_issues()
            .filter(issues::Column::DeletedAt.is_null())
            .filter(issues::Column::Id.eq(id)),
    )
    .await?;
    Ok(rows.into_iter().next().map(|(issue, _)| issue))
}

/// An issue in the trash whose project also went is still worth printing, so
/// this is the one read that asks nothing of the project but its slug.
pub async fn trashed_issues<C: ConnectionTrait>(db: &C) -> Result<Vec<Deleted<Issue>>, StoreError> {
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

// --- writing --------------------------------------------------------------

/// `project_id` is not written: an issue does not move between projects.
///
/// `updated_at` is stamped here rather than by the action, so an `execute`
/// states only the columns it changed and the clock stays in one place.
pub async fn update_issue<C: ConnectionTrait>(db: &C, issue: &Issue) -> Result<(), StoreError> {
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
pub async fn delete_issue<C: ConnectionTrait>(db: &C, issue: &Issue) -> Result<(), StoreError> {
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
pub async fn restore_issue<C: ConnectionTrait>(
    db: &C,
    deleted: &Deleted<Issue>,
) -> Result<(), StoreError> {
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
/// The load that follows is for the projection rather than for the id: an
/// [`Issue`] carries its project's slug, and that is not on the row that was
/// just written. Reading it back through the same projection every other read
/// uses is what makes the returned value the stored object rather than a hopeful
/// copy of the draft.
pub async fn create_issue<C: ConnectionTrait>(
    db: &C,
    draft: issue::Draft,
) -> Result<Issue, StoreError> {
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
