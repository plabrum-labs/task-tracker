//! The JSON edge. Everything that knows about a transport format is here.
//!
//! An [`Action`] is typed and knows nothing about JSON. [`Entry::of`] pairs one
//! with the derived decoder and schema its payload type carries, and erases the
//! payload into two function pointers — which is what lets actions with
//! different payloads sit in one group, and is the whole of what a TUI user
//! picking an action and an agent driving the CLI both need.
//!
//! The key-to-payload-type mapping stays here, next to the action. It cannot
//! move out to a frontend: a caller holding the string `"editTitle"` and a blob
//! has to pick the decoder by key, and doing that anywhere else would let a
//! frontend's idea of an action's arguments drift from the action's own.

use std::future::Future;
use std::pin::Pin;

use schemars::JsonSchema;
use sea_orm::DatabaseTransaction;
use serde::de::DeserializeOwned;
use serde::{Deserialize, Serialize};

use crate::platform::action::{Action, Availability, Offered};
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

/// A boxed future borrowing the payload and the transaction for as long as it
/// runs. This is the cost of an `async` `execute` that OCaml dodges: there the
/// `run` closure returns a value, because a blocking `Db.conn` needs no future
/// and Lwt would erase into an ambient monad rather than a heap allocation.
/// Rust has neither, so an erased async call is a `Pin<Box<dyn Future>>` — one
/// allocation per dispatch, which is nothing beside the write it wraps.
type BoxFuture<'a, T> = Pin<Box<dyn Future<Output = T> + 'a>>;

/// One action with its payload type erased.
///
/// `A::availability` and the monomorphised `run_boxed::<A>` coerce to `fn` once
/// `A` is known, so there is no `Box<dyn …>` behind the entry itself, no object
/// safety to work around, no `PhantomData`, and nothing to downcast. The only
/// box is the future each call returns.
pub struct Entry<O> {
    pub key: &'static str,
    /// The arguments, as the JSON Schema an agent consumes.
    pub schema: serde_json::Value,
    availability: fn(&O) -> Availability,
    run: for<'a> fn(
        O,
        &'a serde_json::Value,
        &'a DatabaseTransaction,
    ) -> BoxFuture<'a, Result<String, Error>>,
}

/// The one place `serde_json`'s error becomes an [`Error::Invalid`].
fn decode<P: DeserializeOwned>(raw: &serde_json::Value) -> Result<P, Error> {
    serde_json::from_value(raw.clone()).map_err(|e| Error::Invalid(e.to_string()))
}

/// Decode against `A`'s payload type, then run it on the transaction, boxed so
/// the erased entry can hold one `fn` pointer regardless of the payload.
fn run_boxed<'a, A>(
    obj: A::Obj,
    raw: &'a serde_json::Value,
    tx: &'a DatabaseTransaction,
) -> BoxFuture<'a, Result<String, Error>>
where
    A: Action,
    // `'static` rather than `A::Obj: 'a`: a bound naming `'a` would make it
    // early-bound and `run_boxed::<A>` would stop being a `for<'a> fn`, which is
    // the type the erased entry holds. Every object this dispatches on is owned
    // and `'static`, so the stronger bound costs nothing.
    A::Obj: 'static,
    A::Payload: DeserializeOwned,
{
    Box::pin(async move {
        let payload = decode::<A::Payload>(raw)?;
        A::run(obj, payload, tx).await
    })
}

impl<O> Entry<O> {
    /// The bounds live here, not on [`Action`]: being JSON is a fact about the
    /// edge, not about the action. Both come from `A::Payload`, so the schema
    /// an action advertises cannot belong to a different type than the one it
    /// decodes.
    pub fn of<A>() -> Self
    where
        A: Action<Obj = O>,
        O: 'static,
        A::Payload: DeserializeOwned + JsonSchema,
    {
        Entry {
            key: A::KEY,
            schema: schemars::schema_for!(A::Payload).to_value(),
            availability: A::availability,
            run: run_boxed::<A>,
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
/// the live object, so what a frontend rendered stays a snapshot. The
/// transaction is the frontend's open one, so a refusal rolls back anything an
/// earlier `execute` already wrote.
pub async fn dispatch<O>(
    group: &[Entry<O>],
    obj: O,
    key: &str,
    payload: &serde_json::Value,
    tx: &DatabaseTransaction,
) -> Result<String, Error> {
    match group.iter().find(|entry| entry.key == key) {
        Some(entry) => (entry.run)(obj, payload, tx).await,
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
