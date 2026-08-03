//! The projects table, and the only file that names it.
//!
//! The split is the same one [`crate::platform::wire`] makes for JSON:
//! [`Project`] names no column and no query. The projections and the writes
//! live here; the entities they run against are [`crate::domains::schema`]'s,
//! reachable from a domain's services and from nowhere else.
//!
//! The counts come from [`crate::domains::issue::services::counts`] rather than
//! from a query here. They read the issues table, and the only rule that makes
//! this split mean anything is that a domain's services are the sole namer of
//! its own table.

use std::collections::HashMap;

use sea_orm::ActiveValue::{Set, Unchanged};
use sea_orm::{ActiveModelTrait, ColumnTrait, EntityTrait, QueryFilter, QueryOrder};

use crate::domains::issue::services::counts;
use crate::domains::project::{self, Project};
use crate::domains::schema::projects;
use crate::platform::clock;
use crate::platform::db::{Db, StoreError};
use crate::platform::deleted::Deleted;

// --- reading --------------------------------------------------------------

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

/// Live projects in creation order, each carrying its issue counts. The counts
/// are what [`crate::domains::project::actions`]' refusals read, so a project
/// loaded any other way would offer a menu it could not justify.
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

/// The trash is by the row's own `deleted_at` and nothing else.
///
/// A project going does not put its issues here — it hides them, because
/// [`issues`](crate::domains::issue::services::issues) requires a live project.
/// That is what makes restoring a project bring back exactly the issues that
/// were not deleted in their own right: one row was written on the way out, so
/// one row is cleared on the way back.
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

// --- writing --------------------------------------------------------------

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

/// Creation. `insert` returns the `Model` carrying the assigned id, with no
/// second query and no `last_insert_rowid` — one of the two petrol gaps that
/// simply are not here.
///
/// The load that follows is for the projection rather than for the id: a
/// [`Project`] carries counts that are not on the row that was just written.
/// Reading it back through the same projection every other read uses is what
/// makes the returned value the stored object rather than a hopeful copy of the
/// draft.
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
