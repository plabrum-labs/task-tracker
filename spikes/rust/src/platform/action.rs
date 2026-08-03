//! What an action is, with no transport in sight.
//!
//! Nothing in this file mentions `serde` or `serde_json`. `Payload` carries no
//! bounds — an action is free to have a payload that is not serialisable at
//! all. The JSON edge both frontends talk to is [`crate::platform::wire`],
//! which adds the bounds where they belong.
//!
//! `execute` is handed the open transaction and writes for itself. There is no
//! second trait for creation: an `INSERT` and an `UPDATE` are both just things
//! an `execute` body does with the connection it holds, so what tells them
//! apart is the write each one issues, not the type each one returns.

use std::borrow::Cow;

use sea_orm::DatabaseTransaction;

use crate::platform::error::Error;

/// An action that applies to the object. `Runnable` cannot carry a reason and
/// `Refused` cannot lack one.
///
/// The reason is a [`Cow`] rather than a `&'static str` because a refusal is
/// allowed to quote the object it read: `editStatus` says "finish or drop 3
/// issues first", and the 3 is per-project. Every refusal that does not need
/// that stays a borrowed literal.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Offered {
    /// Applies, but is refused right now.
    Refused(Cow<'static, str>),
    /// Can run now.
    Runnable,
}

/// Whether one action applies to one object, and if so whether it can run.
///
/// `None` is "does not apply to this object at all — not offered". Naming it
/// `Option` is what lets [`crate::platform::wire::available`] return `Offered`,
/// so a caller of that function cannot be handed an absent action.
pub type Availability = Option<Offered>;

/// Proof that availability was checked. Its field is private, so no module but
/// this one can build one — which is what makes [`Action::run`] the only way to
/// reach [`Action::execute`].
///
/// It is the Rust half of the enforcement guarantee: a token that only
/// [`enforce`] mints, threaded into a body that cannot be reached without it.
pub struct Checked(());

/// The decision an action makes out of its two hooks.
fn decide(available: bool, disabled: Option<Cow<'static, str>>) -> Availability {
    if !available {
        None
    } else {
        Some(match disabled {
            Some(reason) => Offered::Refused(reason),
            None => Offered::Runnable,
        })
    }
}

/// The enforcement point, as a value: the token comes back only when
/// availability allows the write.
fn enforce(key: &str, availability: Availability) -> Result<Checked, Error> {
    match availability {
        None => Err(Error::Conflict(format!("{key} does not apply"))),
        Some(Offered::Refused(reason)) => Err(Error::Conflict(reason.into_owned())),
        Some(Offered::Runnable) => Ok(Checked(())),
    }
}

/// One action, declared in one place.
pub trait Action {
    type Obj;
    type Payload;

    const KEY: &str;

    /// False when the action does not apply to this object at all.
    fn is_available(_obj: &Self::Obj) -> bool {
        true
    }

    /// The reason it applies but cannot run right now.
    fn is_disabled(_obj: &Self::Obj) -> Option<Cow<'static, str>> {
        None
    }

    /// Do the thing, on the open transaction, and return the message a frontend
    /// reports. An edit is an `UPDATE`, a delete stamps `deleted_at`, a creator
    /// `INSERT`s a different table — and nothing outside the body says which.
    ///
    /// This is the one place a concrete [`DatabaseTransaction`] is named rather
    /// than a connection type parameter. OCaml keeps the connection opaque and
    /// threads it through, but Rust's boxed-future erasure (see
    /// [`crate::platform::wire`]) makes the generic form costly for no gain
    /// here, so the transaction is spelled out. A service query stays generic
    /// over the connection, so the body still runs the same read against the
    /// live database or against this transaction.
    fn execute(
        obj: Self::Obj,
        payload: Self::Payload,
        tx: &DatabaseTransaction,
        _: Checked,
    ) -> impl std::future::Future<Output = Result<String, Error>>;

    fn availability(obj: &Self::Obj) -> Availability {
        decide(Self::is_available(obj), Self::is_disabled(obj))
    }

    /// Availability is enforced here rather than at the edge, so a caller that
    /// already holds a payload cannot skip it. [`crate::platform::wire::dispatch`]
    /// decodes and then comes through this.
    fn run(
        obj: Self::Obj,
        payload: Self::Payload,
        tx: &DatabaseTransaction,
    ) -> impl std::future::Future<Output = Result<String, Error>> {
        async move {
            let checked = enforce(Self::KEY, Self::availability(&obj))?;
            Self::execute(obj, payload, tx, checked).await
        }
    }
}
