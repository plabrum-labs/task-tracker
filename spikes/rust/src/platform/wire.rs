//! The JSON edge. Everything that knows about a transport format is here.
//!
//! An [`Action`] is typed and knows nothing about JSON. [`Entry::of`] pairs one
//! with the derived decoder and schema its payload type carries, and erases the
//! payload into two function pointers — which is what lets actions with
//! different payloads sit in one group, and is the whole of what a TUI user
//! picking an action and an agent driving the CLI both need.
//!
//! The key-to-payload-type mapping stays here, next to the action. It cannot
//! move out to the CLI: a caller holding the string `"editTitle"` and a blob has
//! to pick the decoder by key, and doing that anywhere else would let the CLI's
//! idea of an action's arguments drift from the action's own.

use schemars::JsonSchema;
use serde::de::DeserializeOwned;
use serde::{Deserialize, Serialize};

use crate::platform::action::{Action, Availability, Creator, Offered};
use crate::platform::error::Error;

// One declaration, and both halves agree: the decoder accepts `{}` and refuses
// `null`, the schema says `{"type": "object", "additionalProperties": false}`.
// The OCaml spike writes this pair out by hand, because
// `type t = unit [@@deriving yojson, jsonschema]` agrees with itself on the
// wrong thing — it advertises `{"type": "null"}` and accepts `null`, which is
// the opposite of what an action with no arguments is sent.
//
// The schema has no `properties` key at all, which is what `form::of_schema`
// reads as a form with no fields.

/// The arguments of an action that has none.
#[derive(Debug, Clone, Default, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct Empty {}

/// One action with its payload type erased.
///
/// `A::availability` and the monomorphised `decode_and_run::<A>` coerce to `fn`
/// once `A` is known, so there is no `Box<dyn …>`, no object safety to work
/// around, no `PhantomData`, and nothing to downcast.
pub struct Entry<O> {
    pub key: &'static str,
    /// The arguments, as the JSON Schema an agent consumes.
    pub schema: serde_json::Value,
    availability: fn(&O) -> Availability,
    run: fn(O, &serde_json::Value) -> Result<O, Error>,
}

/// Both erased edges decode the same way, so the one place `serde_json`'s error
/// becomes an [`Error::Invalid`] is here.
fn decode<P: DeserializeOwned>(raw: &serde_json::Value) -> Result<P, Error> {
    serde_json::from_value(raw.clone()).map_err(|e| Error::Invalid(e.to_string()))
}

fn decode_and_run<A>(obj: A::Obj, raw: &serde_json::Value) -> Result<A::Obj, Error>
where
    A: Action,
    A::Payload: DeserializeOwned,
{
    A::run(obj, decode::<A::Payload>(raw)?)
}

impl<O> Entry<O> {
    /// The bounds live here, not on [`Action`]: being JSON is a fact about the
    /// edge, not about the action. Both come from `A::Payload`, so the schema
    /// an action advertises cannot belong to a different type than the one it
    /// decodes.
    pub fn of<A>() -> Self
    where
        A: Action<Obj = O>,
        A::Payload: DeserializeOwned + JsonSchema,
    {
        Entry {
            key: A::KEY,
            schema: schemars::schema_for!(A::Payload).to_value(),
            availability: A::availability,
            run: decode_and_run::<A>,
        }
    }
}

/// The actions one kind of object offers. Registration is a value of this type,
/// and there is one per domain object.
pub type Group<O> = Vec<Entry<O>>;

/// What the object offers, in registration order.
///
/// Refused actions are kept and absent ones dropped, so a caller is told both
/// what it can do and what it could do but for a reason.
pub fn available<'a, O>(group: &'a [Entry<O>], obj: &O) -> Vec<(&'a Entry<O>, Offered)> {
    group
        .iter()
        .filter_map(|entry| Some((entry, (entry.availability)(obj)?)))
        .collect()
}

/// The one entry point for a caller holding a key and a blob. Availability is
/// not checked here — `A::run`, which [`Entry::of`] wired in, does it against
/// the live object, so what a frontend rendered stays a snapshot.
pub fn dispatch<O>(
    group: &[Entry<O>],
    obj: O,
    key: &str,
    payload: &serde_json::Value,
) -> Result<O, Error> {
    match group.iter().find(|entry| entry.key == key) {
        Some(entry) => (entry.run)(obj, payload),
        None => Err(Error::Invalid(format!("no action {key:?}"))),
    }
}

/// The same edge for [`Creator`]: two objects rather than one, because what a
/// creator is offered against is not what it returns.
///
/// Everything below is [`Entry`], [`available`] and [`dispatch`] with `O` split
/// into `P` and `C`. The erasure is the same pair of `fn` pointers and the
/// bounds are the same bounds, so a creator's schema and its decoder come from
/// one type here too.
pub struct CreatorEntry<P, C> {
    pub key: &'static str,
    pub schema: serde_json::Value,
    availability: fn(&P) -> Availability,
    run: fn(&P, &serde_json::Value) -> Result<C, Error>,
}

fn decode_and_create<K>(parent: &K::Parent, raw: &serde_json::Value) -> Result<K::Child, Error>
where
    K: Creator,
    K::Payload: DeserializeOwned,
{
    K::run(parent, decode::<K::Payload>(raw)?)
}

impl<P, C> CreatorEntry<P, C> {
    pub fn of<K>() -> Self
    where
        K: Creator<Parent = P, Child = C>,
        K::Payload: DeserializeOwned + JsonSchema,
    {
        CreatorEntry {
            key: K::KEY,
            schema: schemars::schema_for!(K::Payload).to_value(),
            availability: K::availability,
            run: decode_and_create::<K>,
        }
    }
}

pub type CreatorGroup<P, C> = Vec<CreatorEntry<P, C>>;

pub fn creators_available<'a, P, C>(
    group: &'a [CreatorEntry<P, C>],
    parent: &P,
) -> Vec<(&'a CreatorEntry<P, C>, Offered)> {
    group
        .iter()
        .filter_map(|entry| Some((entry, (entry.availability)(parent)?)))
        .collect()
}

pub fn create<P, C>(
    group: &[CreatorEntry<P, C>],
    parent: &P,
    key: &str,
    payload: &serde_json::Value,
) -> Result<C, Error> {
    match group.iter().find(|entry| entry.key == key) {
        Some(entry) => (entry.run)(parent, payload),
        None => Err(Error::Invalid(format!("no action {key:?}"))),
    }
}

/// The wire name of a value, taken from the same `serde` attribute the decoder
/// and the schema read.
///
/// A frontend printing `doing` needs the string the enum goes over the wire as,
/// and writing a `Display` impl by hand would be a third table of strings
/// beside `rename_all` and `string_value`. This is one line and no table, and
/// it is here because turning a value into its wire form is what this file is.
///
/// The fallback is unreachable for the fieldless enums this spike has — they
/// serialise to a string and nothing else — and it is a fallback rather than an
/// `unwrap` because a panic in a renderer is worse than a mis-printed cell.
pub fn name_of<T: Serialize>(value: &T) -> String {
    match serde_json::to_value(value) {
        Ok(serde_json::Value::String(name)) => name,
        Ok(other) => other.to_string(),
        Err(e) => e.to_string(),
    }
}
