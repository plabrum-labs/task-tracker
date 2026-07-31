//! What an action is, with no transport in sight.
//!
//! Nothing in this file mentions `serde` or `serde_json`. `Payload` carries no
//! bounds — an action is free to have a payload that is not serialisable at
//! all. The JSON edge both frontends talk to is [`crate::wire`], which adds the
//! bounds where they belong.

use std::borrow::Cow;

use crate::error::Error;

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
/// `Option` is what lets [`crate::wire::available`] return `Offered`, so a
/// caller of that function cannot be handed an absent action.
pub type Availability = Option<Offered>;

/// Proof that availability was checked. Its field is private, so no module but
/// this one can build one — which is what makes [`Action::run`] the only way to
/// reach [`Action::execute`], and [`Creator::run`] the only way to reach
/// [`Creator::create`].
pub struct Checked(());

/// The decision both traits make out of their two hooks. Extracted so that what
/// [`Action`] and [`Creator`] duplicate is two signatures rather than two
/// copies of the rule.
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

    fn execute(obj: Self::Obj, payload: Self::Payload, _: Checked) -> Result<Self::Obj, Error>;

    fn availability(obj: &Self::Obj) -> Availability {
        decide(Self::is_available(obj), Self::is_disabled(obj))
    }

    /// Availability is enforced here rather than at the edge, so a caller that
    /// already holds a payload cannot skip it. [`crate::wire::dispatch`]
    /// decodes and then comes through this.
    fn run(obj: Self::Obj, payload: Self::Payload) -> Result<Self::Obj, Error> {
        let checked = enforce(Self::KEY, Self::availability(&obj))?;
        Self::execute(obj, payload, checked)
    }
}

/// One action over a parent, producing a child that does not exist yet.
///
/// Deliberately not [`Action`] at `Obj = Parent = Child`. The store `INSERT`s
/// what one returns and `UPDATE`s what the other returns, and no field of
/// either says which — so a single type covering both would make the store's
/// dispatch unsound. Rust's traits are nominal, so keeping the two apart costs
/// nothing at the use site; the cost is the duplicated default-method bodies
/// below, which is what [`decide`] and [`enforce`] hold down to two signatures.
pub trait Creator {
    type Parent;
    type Payload;
    type Child;

    const KEY: &str;

    fn is_available(_parent: &Self::Parent) -> bool {
        true
    }

    fn is_disabled(_parent: &Self::Parent) -> Option<Cow<'static, str>> {
        None
    }

    /// The parent is borrowed rather than consumed: making a child leaves it
    /// where it was, which is the other half of what stops this being an
    /// [`Action`].
    fn create(
        parent: &Self::Parent,
        payload: Self::Payload,
        _: Checked,
    ) -> Result<Self::Child, Error>;

    fn availability(parent: &Self::Parent) -> Availability {
        decide(Self::is_available(parent), Self::is_disabled(parent))
    }

    /// The enforcement point for a creator, exactly as [`Action::run`] is for
    /// an action, and private for the same reason.
    fn run(parent: &Self::Parent, payload: Self::Payload) -> Result<Self::Child, Error> {
        let checked = enforce(Self::KEY, Self::availability(parent))?;
        Self::create(parent, payload, checked)
    }
}
