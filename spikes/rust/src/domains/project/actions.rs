//! Everything a project can be asked, in three registrations.
//!
//! This is the half of the domain that has preconditions. Every issue action is
//! always `Runnable`; a project refuses three things, and all three refusals
//! read something that is on the object only because the store put it there.
//! Availability earns its keep where actions are verbs with preconditions, and
//! a CRUD-shaped edit is not one of those. Creating something still is.
//!
//! A creator is an ordinary [`Action`] whose `execute` writes a different table
//! — `addIssue` inserts an issue, `createProject` a project — and nothing about
//! the declaration marks either as special, because `execute` holds the
//! transaction and what it writes is its own business.
//!
//! There is one group per object, save for the two objects that are not a live
//! project: [`deleted_group`] is offered against a [`Restorable`], and [`root`]
//! against the list of live projects a `createProject` is checked against. See
//! `../issue/actions.rs` for the shape of an action declaration.

use std::borrow::Cow;

use sea_orm::DatabaseTransaction;

use crate::domains::issue;
use crate::domains::project::{Draft, Project, Restorable, Status, schemas, services};
use crate::platform::action::{Action, Checked};
use crate::platform::error::Error;
use crate::platform::wire::{Empty, Entry, Group};

/// The editable columns, written and reported as one. `updated_at` is the
/// store's to stamp.
async fn saved(project: Project, tx: &DatabaseTransaction) -> Result<String, Error> {
    let subject = project.subject();
    services::update_project(tx, &project).await?;
    Ok(format!("{subject}: saved"))
}

fn issues(n: i64) -> String {
    if n == 1 {
        "1 issue".to_string()
    } else {
        format!("{n} issues")
    }
}

pub struct EditTitle;

impl Action for EditTitle {
    type Obj = Project;
    type Payload = schemas::EditTitlePayload;

    const KEY: &str = "editTitle";

    async fn execute(
        project: Project,
        payload: schemas::EditTitlePayload,
        tx: &DatabaseTransaction,
        _: Checked,
    ) -> Result<String, Error> {
        let title = payload.title.trim();
        if title.is_empty() {
            return Err(Error::Invalid("title is required".into()));
        }
        saved(
            Project {
                title: title.to_string(),
                ..project
            },
            tx,
        )
        .await
    }
}

pub struct EditBody;

impl Action for EditBody {
    type Obj = Project;
    type Payload = schemas::EditBodyPayload;

    const KEY: &str = "editBody";

    async fn execute(
        project: Project,
        payload: schemas::EditBodyPayload,
        tx: &DatabaseTransaction,
        _: Checked,
    ) -> Result<String, Error> {
        saved(
            Project {
                body: payload.body,
                ..project
            },
            tx,
        )
        .await
    }
}

pub struct EditStatus;

// The refusal is stated against the object rather than against the payload, so
// it holds whichever status was asked for — archiving is the only move that
// could break it, and asking to stay active is refused too. That is what a hook
// seeing only the object costs, and the alternative is a rule split between
// `is_disabled` and `execute`.
impl Action for EditStatus {
    type Obj = Project;
    type Payload = schemas::EditStatusPayload;

    const KEY: &str = "editStatus";

    fn is_disabled(project: &Project) -> Option<Cow<'static, str>> {
        (project.doing > 0)
            .then(|| format!("finish or drop {} first", issues(project.doing)).into())
    }

    async fn execute(
        project: Project,
        payload: schemas::EditStatusPayload,
        tx: &DatabaseTransaction,
        _: Checked,
    ) -> Result<String, Error> {
        saved(
            Project {
                status: payload.status,
                ..project
            },
            tx,
        )
        .await
    }
}

pub struct Delete;

impl Action for Delete {
    type Obj = Project;
    type Payload = Empty;

    const KEY: &str = "delete";

    fn is_disabled(project: &Project) -> Option<Cow<'static, str>> {
        match project.status {
            Status::Active => Some("archive it first".into()),
            Status::Archived => None,
        }
    }

    async fn execute(
        project: Project,
        _: Empty,
        tx: &DatabaseTransaction,
        _: Checked,
    ) -> Result<String, Error> {
        let subject = project.subject();
        services::delete_project(tx, &project).await?;
        Ok(format!("{subject}: deleted"))
    }
}

pub struct Restore;

// The one refusal a hook can state only because its object was widened to carry
// the answer. The partial unique index is still what guarantees it; this is
// what turns a constraint violation into a sentence.
impl Action for Restore {
    type Obj = Restorable;
    type Payload = Empty;

    const KEY: &str = "restore";

    fn is_disabled(restorable: &Restorable) -> Option<Cow<'static, str>> {
        let slug = &restorable.deleted.inner.slug;
        restorable
            .live
            .iter()
            .any(|project| &project.slug == slug)
            .then(|| format!("project {slug:?} exists again").into())
    }

    async fn execute(
        restorable: Restorable,
        _: Empty,
        tx: &DatabaseTransaction,
        _: Checked,
    ) -> Result<String, Error> {
        services::restore_project(tx, &restorable.deleted).await?;
        Ok(format!("{}: restored", restorable.deleted.inner.subject()))
    }
}

pub struct AddIssue;

// An action on a project writing to the issues table. The store assigns the id,
// so the message reads the row it wrote rather than the draft it built.
impl Action for AddIssue {
    type Obj = Project;
    type Payload = schemas::AddIssuePayload;

    const KEY: &str = "addIssue";

    fn is_disabled(project: &Project) -> Option<Cow<'static, str>> {
        match project.status {
            Status::Archived => Some("project is archived".into()),
            Status::Active => None,
        }
    }

    async fn execute(
        project: Project,
        payload: schemas::AddIssuePayload,
        tx: &DatabaseTransaction,
        _: Checked,
    ) -> Result<String, Error> {
        let title = payload.title.trim();
        if title.is_empty() {
            return Err(Error::Invalid("title is required".into()));
        }
        let draft = issue::Draft {
            project_id: project.id,
            title: title.to_string(),
            body: payload.body.unwrap_or_default(),
            priority: payload.priority.unwrap_or(issue::Priority::Normal),
        };
        let created = issue::services::create_issue(tx, draft).await?;
        Ok(format!("{}: created", created.subject()))
    }
}

pub struct CreateProject;

// The duplicate check cannot be an availability hook, because a hook is given
// the parent and not the payload — it can be told there are projects and not
// which slug is being asked for. So the loaded list is the object, and the
// refusal comes from `execute`. The partial unique index is still what
// guarantees it; this is only what turns a constraint violation into a
// sentence, and `tests/store.rs` asserts both halves.
impl Action for CreateProject {
    type Obj = Vec<Project>;
    type Payload = schemas::CreateProjectPayload;

    const KEY: &str = "createProject";

    async fn execute(
        projects: Vec<Project>,
        payload: schemas::CreateProjectPayload,
        tx: &DatabaseTransaction,
        _: Checked,
    ) -> Result<String, Error> {
        let slug = payload.slug.trim();
        if slug.is_empty() {
            return Err(Error::Invalid("slug is required".into()));
        }
        if projects.iter().any(|project| project.slug == slug) {
            return Err(Error::Conflict(format!("project {slug:?} already exists")));
        }
        let created = services::create_project(
            tx,
            Draft {
                slug: slug.to_string(),
                title: payload.title.unwrap_or_default(),
                body: payload.body.unwrap_or_default(),
            },
        )
        .await?;
        Ok(format!("{}: created", created.subject()))
    }
}

/// Everything a live project offers, in the order it is offered: the edits,
/// then leaving, then the one thing it makes. `addIssue`'s `execute` writes a
/// different table, and nothing about its place in this list says so.
pub fn group() -> Group<Project> {
    vec![
        Entry::of::<EditTitle>(),
        Entry::of::<EditBody>(),
        Entry::of::<EditStatus>(),
        Entry::of::<Delete>(),
        Entry::of::<AddIssue>(),
    ]
}

/// What a row in the trash offers, which is coming back and nothing else.
/// Offered against [`Restorable`], so an edit cannot reach it.
pub fn deleted_group() -> Group<Restorable> {
    vec![Entry::of::<Restore>()]
}

/// The one action with no object to address. Its object is the list of live
/// projects, which is what a uniqueness refusal has to read — the one split that
/// is about what availability is checked against rather than about what gets
/// written.
pub fn root() -> Group<Vec<Project>> {
    vec![Entry::of::<CreateProject>()]
}
