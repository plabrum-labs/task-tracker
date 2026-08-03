//! Everything an issue can be asked, in two registrations.
//!
//! One action is one `impl Action`: its object type, its key, its two hooks and
//! its `execute`, which holds the open transaction and writes for itself — an
//! edit is an `UPDATE`, a delete stamps `deleted_at`, and nothing outside the
//! body says which. The payload shapes are in [`schemas`], one type per action,
//! so an action names only `type Payload = schemas::<Name>` and states nothing
//! about how that type is decoded. An argumentless action names [`Empty`].
//!
//! There is one group per object. [`group`] is everything a live issue offers —
//! the four edits and the delete. The only split left is by the object itself:
//! [`deleted_group`] is offered against [`Deleted<Issue>`], so a deleted issue
//! cannot be edited because an edit was never registered against its type.
//!
//! None of the four edits refuses anything. Many issues may be `doing` at once,
//! there is no WIP rule, and nothing else about an issue constrains what may be
//! done to it — so both hooks are the default in every case and the refusals are
//! all `execute`'s.
//!
//! Every payload field carries a doc comment, and `schemars` puts it in the
//! schema's `description`: it becomes the `--help` text of a CLI option and the
//! label beside a TUI field, neither of which the OCaml side has anything to
//! fill.

use sea_orm::DatabaseTransaction;

use crate::domains::issue::{Issue, schemas, services};
use crate::platform::action::{Action, Checked};
use crate::platform::deleted::Deleted;
use crate::platform::error::Error;
use crate::platform::wire::{Empty, Entry, Group};

/// The editable columns, written and reported as one. `updated_at` is stamped
/// by the store, so an `execute` states only what it changed.
async fn saved(issue: Issue, tx: &DatabaseTransaction) -> Result<String, Error> {
    let subject = issue.subject();
    services::update_issue(tx, &issue).await?;
    Ok(format!("{subject}: saved"))
}

pub struct EditTitle;

impl Action for EditTitle {
    type Obj = Issue;
    type Payload = schemas::EditTitlePayload;

    const KEY: &str = "editTitle";

    async fn execute(
        issue: Issue,
        payload: schemas::EditTitlePayload,
        tx: &DatabaseTransaction,
        _: Checked,
    ) -> Result<String, Error> {
        let title = payload.title.trim();
        if title.is_empty() {
            return Err(Error::Invalid("title is required".into()));
        }
        saved(
            Issue {
                title: title.to_string(),
                ..issue
            },
            tx,
        )
        .await
    }
}

pub struct EditBody;

// The same payload shape as `editTitle` and a different rule: a blank body is
// how you clear one. Two actions the schema cannot tell apart, which is where
// an action still earns its keep over a generated form.
impl Action for EditBody {
    type Obj = Issue;
    type Payload = schemas::EditBodyPayload;

    const KEY: &str = "editBody";

    async fn execute(
        issue: Issue,
        payload: schemas::EditBodyPayload,
        tx: &DatabaseTransaction,
        _: Checked,
    ) -> Result<String, Error> {
        saved(
            Issue {
                body: payload.body,
                ..issue
            },
            tx,
        )
        .await
    }
}

pub struct EditStatus;

// The note describes the status it arrived with, so moving without one clears
// the old note rather than leaving it to describe a state the issue is no
// longer in.
impl Action for EditStatus {
    type Obj = Issue;
    type Payload = schemas::EditStatusPayload;

    const KEY: &str = "editStatus";

    async fn execute(
        issue: Issue,
        payload: schemas::EditStatusPayload,
        tx: &DatabaseTransaction,
        _: Checked,
    ) -> Result<String, Error> {
        saved(
            Issue {
                status: payload.status,
                status_note: payload.note,
                ..issue
            },
            tx,
        )
        .await
    }
}

pub struct EditPriority;

impl Action for EditPriority {
    type Obj = Issue;
    type Payload = schemas::EditPriorityPayload;

    const KEY: &str = "editPriority";

    async fn execute(
        issue: Issue,
        payload: schemas::EditPriorityPayload,
        tx: &DatabaseTransaction,
        _: Checked,
    ) -> Result<String, Error> {
        saved(
            Issue {
                priority: payload.priority,
                ..issue
            },
            tx,
        )
        .await
    }
}

pub struct Delete;

// The soft delete, which is an update like any other — the column it sets is
// not on `Issue` at all, so the whole of the write is the store call.
impl Action for Delete {
    type Obj = Issue;
    type Payload = Empty;

    const KEY: &str = "delete";

    async fn execute(
        issue: Issue,
        _: Empty,
        tx: &DatabaseTransaction,
        _: Checked,
    ) -> Result<String, Error> {
        let subject = issue.subject();
        services::delete_issue(tx, &issue).await?;
        Ok(format!("{subject}: deleted"))
    }
}

pub struct Restore;

impl Action for Restore {
    type Obj = Deleted<Issue>;
    type Payload = Empty;

    const KEY: &str = "restore";

    async fn execute(
        deleted: Deleted<Issue>,
        _: Empty,
        tx: &DatabaseTransaction,
        _: Checked,
    ) -> Result<String, Error> {
        services::restore_issue(tx, &deleted).await?;
        Ok(format!("{}: restored", deleted.inner.subject()))
    }
}

/// Everything a live issue offers, in the order it is offered.
pub fn group() -> Group<Issue> {
    vec![
        Entry::of::<EditTitle>(),
        Entry::of::<EditBody>(),
        Entry::of::<EditStatus>(),
        Entry::of::<EditPriority>(),
        Entry::of::<Delete>(),
    ]
}

/// What a row in the trash offers, which is coming back and nothing else.
///
/// `Deleted<Issue>` rather than `Issue` is what makes "a deleted issue cannot
/// be edited" a fact about what compiles: [`group`] does not typecheck against
/// one, and there is no conversion that would let it.
pub fn deleted_group() -> Group<Deleted<Issue>> {
    vec![Entry::of::<Restore>()]
}
