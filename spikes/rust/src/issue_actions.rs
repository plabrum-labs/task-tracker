//! Everything an issue can be asked, in three registrations.
//!
//! The split is by what the store does with the result, not by what the action
//! means. [`group`] is persisted by an `UPDATE` of the editable columns,
//! [`trash`] by an `UPDATE` that stamps `deleted_at`, [`deleted_group`] by one
//! that clears it — and all three have the same shape, because a soft delete is
//! an update. [`Creator`](crate::action::Creator) separates an `INSERT` from an
//! `UPDATE` and nothing separates these, so pairing the wrong group with the
//! wrong store call compiles. Pairing them once, in one list, is all that
//! stands in the way.
//!
//! None of the four edits refuses anything. Many issues may be `doing` at once,
//! there is no WIP rule, and nothing else about an issue constrains what may be
//! done to it — so availability is the default in every case and the refusals
//! are all `execute`'s.
//!
//! Every payload field carries a doc comment, and `schemars` puts it in the
//! schema's `description`. That is finding 2 turning into something a person
//! sees: it becomes the `--help` text of a CLI option and the label beside a
//! TUI field, neither of which the OCaml side has anything to fill.

use schemars::JsonSchema;
use serde::Deserialize;

use crate::action::{Action, Checked};
use crate::deleted::Deleted;
use crate::error::Error;
use crate::issue::{Issue, Priority, Status};
use crate::wire::{Empty, Entry, Group};

#[derive(Debug, Clone, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct EditTitlePayload {
    /// What to call the issue.
    pub title: String,
}

pub struct EditTitle;

impl Action for EditTitle {
    type Obj = Issue;
    type Payload = EditTitlePayload;

    const KEY: &str = "editTitle";

    fn execute(issue: Issue, payload: EditTitlePayload, _: Checked) -> Result<Issue, Error> {
        let title = payload.title.trim();
        if title.is_empty() {
            return Err(Error::Invalid("title is required".into()));
        }
        Ok(Issue {
            title: title.to_string(),
            ..issue
        })
    }
}

#[derive(Debug, Clone, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct EditBodyPayload {
    /// The issue's description. Blank clears it.
    pub body: String,
}

pub struct EditBody;

// The same payload shape as `editTitle` and a different rule: a blank body is
// how you clear one. Two actions the schema cannot tell apart, which is where
// an action still earns its keep over a generated form.
impl Action for EditBody {
    type Obj = Issue;
    type Payload = EditBodyPayload;

    const KEY: &str = "editBody";

    fn execute(issue: Issue, payload: EditBodyPayload, _: Checked) -> Result<Issue, Error> {
        Ok(Issue {
            body: payload.body,
            ..issue
        })
    }
}

#[derive(Debug, Clone, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct EditStatusPayload {
    /// Where the issue is up to.
    pub status: Status,
    /// What to record about the move.
    pub note: Option<String>,
}

pub struct EditStatus;

// The note describes the status it arrived with, so moving without one clears
// the old note rather than leaving it to describe a state the issue is no
// longer in.
impl Action for EditStatus {
    type Obj = Issue;
    type Payload = EditStatusPayload;

    const KEY: &str = "editStatus";

    fn execute(issue: Issue, payload: EditStatusPayload, _: Checked) -> Result<Issue, Error> {
        Ok(Issue {
            status: payload.status,
            status_note: payload.note,
            ..issue
        })
    }
}

#[derive(Debug, Clone, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct EditPriorityPayload {
    /// How far up the list the issue sorts.
    pub priority: Priority,
}

pub struct EditPriority;

impl Action for EditPriority {
    type Obj = Issue;
    type Payload = EditPriorityPayload;

    const KEY: &str = "editPriority";

    fn execute(issue: Issue, payload: EditPriorityPayload, _: Checked) -> Result<Issue, Error> {
        Ok(Issue {
            priority: payload.priority,
            ..issue
        })
    }
}

pub struct Delete;

// `execute` is the identity. What the write is lives entirely in the store call
// this group is paired with, because the column it sets is not on `Issue` at
// all — a live issue has no `deleted_at` to assign.
impl Action for Delete {
    type Obj = Issue;
    type Payload = Empty;

    const KEY: &str = "delete";

    fn execute(issue: Issue, _: Empty, _: Checked) -> Result<Issue, Error> {
        Ok(issue)
    }
}

pub struct Restore;

impl Action for Restore {
    type Obj = Deleted<Issue>;
    type Payload = Empty;

    const KEY: &str = "restore";

    fn execute(deleted: Deleted<Issue>, _: Empty, _: Checked) -> Result<Deleted<Issue>, Error> {
        Ok(deleted)
    }
}

/// The edits, in the order they are offered.
pub fn group() -> Group<Issue> {
    vec![
        Entry::of::<EditTitle>(),
        Entry::of::<EditBody>(),
        Entry::of::<EditStatus>(),
        Entry::of::<EditPriority>(),
    ]
}

/// Leaving. Separate from [`group`] only so the store call that follows can be.
pub fn trash() -> Group<Issue> {
    vec![Entry::of::<Delete>()]
}

/// What a row in the trash offers, which is coming back and nothing else.
///
/// `Deleted<Issue>` rather than `Issue` is what makes "a deleted issue cannot
/// be edited" a fact about what compiles: [`group`] does not typecheck against
/// one, and there is no conversion that would let it.
pub fn deleted_group() -> Group<Deleted<Issue>> {
    vec![Entry::of::<Restore>()]
}
