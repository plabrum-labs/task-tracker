//! Everything a project can be asked, and the two things that make one.
//!
//! This is the half of the domain that has preconditions. Every issue action is
//! always `Runnable`; a project refuses three things, and all three refusals
//! read a count that is on the object only because the store put it there.
//! Availability earns its keep where actions are verbs with preconditions, and
//! a CRUD-shaped edit is not one of those. Creating something still is.

use std::borrow::Cow;

use schemars::JsonSchema;
use serde::Deserialize;

use crate::domains::issue;
use crate::domains::project::{Draft, Project, Restorable, Status};
use crate::platform::action::{Action, Checked, Creator};
use crate::platform::error::Error;
use crate::platform::wire::{CreatorEntry, CreatorGroup, Empty, Entry, Group};

#[derive(Debug, Clone, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct EditTitlePayload {
    /// What to call the project.
    pub title: String,
}

pub struct EditTitle;

impl Action for EditTitle {
    type Obj = Project;
    type Payload = EditTitlePayload;

    const KEY: &str = "editTitle";

    fn execute(project: Project, payload: EditTitlePayload, _: Checked) -> Result<Project, Error> {
        let title = payload.title.trim();
        if title.is_empty() {
            return Err(Error::Invalid("title is required".into()));
        }
        Ok(Project {
            title: title.to_string(),
            ..project
        })
    }
}

#[derive(Debug, Clone, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct EditBodyPayload {
    /// The project's description. Blank clears it.
    pub body: String,
}

pub struct EditBody;

impl Action for EditBody {
    type Obj = Project;
    type Payload = EditBodyPayload;

    const KEY: &str = "editBody";

    fn execute(project: Project, payload: EditBodyPayload, _: Checked) -> Result<Project, Error> {
        Ok(Project {
            body: payload.body,
            ..project
        })
    }
}

#[derive(Debug, Clone, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct EditStatusPayload {
    /// Whether the project is still being worked on.
    pub status: Status,
}

pub struct EditStatus;

fn issues(n: i64) -> String {
    if n == 1 {
        "1 issue".to_string()
    } else {
        format!("{n} issues")
    }
}

// The refusal is stated against the object rather than against the payload, so
// it holds whichever status was asked for — archiving is the only move that
// could break it, and asking to stay active is refused too. That is what a hook
// seeing only the object costs, and the alternative is a rule split between
// `is_disabled` and `execute`.
impl Action for EditStatus {
    type Obj = Project;
    type Payload = EditStatusPayload;

    const KEY: &str = "editStatus";

    fn is_disabled(project: &Project) -> Option<Cow<'static, str>> {
        (project.doing > 0)
            .then(|| format!("finish or drop {} first", issues(project.doing)).into())
    }

    fn execute(project: Project, payload: EditStatusPayload, _: Checked) -> Result<Project, Error> {
        Ok(Project {
            status: payload.status,
            ..project
        })
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

    fn execute(project: Project, _: Empty, _: Checked) -> Result<Project, Error> {
        Ok(project)
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

    fn execute(restorable: Restorable, _: Empty, _: Checked) -> Result<Restorable, Error> {
        Ok(restorable)
    }
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

pub struct AddIssue;

impl Creator for AddIssue {
    type Parent = Project;
    type Payload = AddIssuePayload;
    type Child = issue::Draft;

    const KEY: &str = "addIssue";

    fn is_disabled(project: &Project) -> Option<Cow<'static, str>> {
        match project.status {
            Status::Archived => Some("project is archived".into()),
            Status::Active => None,
        }
    }

    fn create(
        project: &Project,
        payload: AddIssuePayload,
        _: Checked,
    ) -> Result<issue::Draft, Error> {
        let title = payload.title.trim();
        if title.is_empty() {
            return Err(Error::Invalid("title is required".into()));
        }
        Ok(issue::Draft {
            project_id: project.id,
            title: title.to_string(),
            body: payload.body.unwrap_or_default(),
            priority: payload.priority.unwrap_or(issue::Priority::Normal),
        })
    }
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

pub struct CreateProject;

// The duplicate check cannot be an availability hook, because a hook is given
// the parent and not the payload — it can be told there are projects and not
// which slug is being asked for. So the loaded list is the parent, and the
// refusal comes from `create`. The partial unique index is still what
// guarantees it; this is only what turns a constraint violation into a
// sentence, and `tests/store.rs` asserts both halves.
impl Creator for CreateProject {
    type Parent = Vec<Project>;
    type Payload = CreateProjectPayload;
    type Child = Draft;

    const KEY: &str = "createProject";

    fn create(
        projects: &Vec<Project>,
        payload: CreateProjectPayload,
        _: Checked,
    ) -> Result<Draft, Error> {
        let slug = payload.slug.trim();
        if slug.is_empty() {
            return Err(Error::Invalid("slug is required".into()));
        }
        if projects.iter().any(|project| project.slug == slug) {
            return Err(Error::Conflict(format!("project {slug:?} already exists")));
        }
        Ok(Draft {
            slug: slug.to_string(),
            title: payload.title.unwrap_or_default(),
            body: payload.body.unwrap_or_default(),
        })
    }
}

/// The edits, in the order they are offered.
pub fn group() -> Group<Project> {
    vec![
        Entry::of::<EditTitle>(),
        Entry::of::<EditBody>(),
        Entry::of::<EditStatus>(),
    ]
}

/// Leaving. Separate from [`group`] only so the store call that follows can be.
pub fn trash() -> Group<Project> {
    vec![Entry::of::<Delete>()]
}

/// What a row in the trash offers, which is coming back and nothing else.
pub fn deleted_group() -> Group<Restorable> {
    vec![Entry::of::<Restore>()]
}

/// What a project can make. The child is a draft rather than an issue: an
/// action produces a value and the store assigns the id, so the type of what
/// `create` returns is the type of what has not been written yet.
pub fn creators() -> CreatorGroup<Project, issue::Draft> {
    vec![CreatorEntry::of::<AddIssue>()]
}

/// The one action with no object to address. Its parent is the list of live
/// projects, which is what a uniqueness refusal has to read.
pub fn root() -> CreatorGroup<Vec<Project>, Draft> {
    vec![CreatorEntry::of::<CreateProject>()]
}
