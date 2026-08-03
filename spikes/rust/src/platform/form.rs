//! The argument form a frontend builds from an action's advertised schema.
//!
//! This is the second half of the erased path. [`crate::platform::wire::available`] says
//! which actions apply; this says what each one wants typed in. Both come from
//! the schema the payload type derived, so a frontend needs no knowledge of any
//! particular action — [`crate::frontend::cli`] turns what [`of_schema`] returns into
//! options and [`crate::frontend::tui`] turns it into controls.
//!
//! Pure, and separate from the rendering for that reason: the shape of a form
//! is decided here and asserted in `tests/frontend.rs`, and the two frontends
//! are left with drawing and key handling.

use serde_json::Value;

/// What control a field needs.
///
/// [`Kind::Enum`] is the first control this derives that is not a text box, and
/// it is the reason [`of_schema`] resolves `$ref`: `schemars` gives a field
/// whose type is an enum a `{"$ref": "#/$defs/Status"}` and puts the values in
/// `$defs`, so a reader that stopped at the property would see nothing it could
/// render.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Kind {
    /// `{"type": "string"}`
    Text,
    /// `{"type": ["string", "null"]}`
    OptionalText,
    /// `{"type": "string", "enum": [ … ]}`, reached through a `$ref`.
    Enum(Vec<String>),
}

/// One field to render.
///
/// `description` is the difference finding 2 makes when a person is looking.
/// `schemars` carries the payload field's doc comment into the schema, so the
/// CLI has real `--help` text and the TUI has a real label;
/// `ppx_deriving_jsonschema` emits neither, and the OCaml frontends show the
/// property name and nothing else.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Field {
    pub name: String,
    pub required: bool,
    pub kind: Kind,
    pub description: Option<String>,
}

fn strings(values: &[Value]) -> Vec<String> {
    values
        .iter()
        .filter_map(|v| v.as_str().map(str::to_string))
        .collect()
}

/// `"#/$defs/Status"` and nothing else. A `$ref` this cannot follow fails the
/// form rather than being skipped, for the same reason a type it cannot render
/// does.
fn resolve<'a>(schema: &'a Value, spec: &'a Value) -> Option<&'a Value> {
    let target = spec.get("$ref")?.as_str()?.strip_prefix("#/$defs/")?;
    schema.get("$defs")?.get(target)
}

/// An optional field of a primitive type widens to `["string", "null"]`; an
/// optional field of any other type becomes `anyOf: [ …, {"type": "null"}]`
/// instead. Two encodings of the same idea, from one deriver, decided by
/// something the schema reader cannot see — so the null arm is stripped and the
/// rest read as if it had been required. What makes that safe is `required`,
/// which the schema states separately and [`of_schema`] reads from there.
fn kind_of(schema: &Value, spec: &Value) -> Result<Kind, String> {
    if let Some(resolved) = resolve(schema, spec) {
        return kind_of(schema, resolved);
    }
    if let Some(arms) = spec.get("anyOf").and_then(Value::as_array)
        && let [inner, null] = arms.as_slice()
        && null.get("type") == Some(&Value::String("null".into()))
    {
        return kind_of(schema, inner);
    }
    match (spec.get("type"), spec.get("enum")) {
        (Some(Value::String(t)), Some(Value::Array(names))) if t == "string" => {
            Ok(Kind::Enum(strings(names)))
        }
        (Some(Value::String(t)), None) if t == "string" => Ok(Kind::Text),
        (Some(Value::Array(types)), _) if strings(types) == ["string", "null"] => {
            Ok(Kind::OptionalText)
        }
        _ => Err(spec.to_string()),
    }
}

/// The fields to render, in the order the payload type declares them.
///
/// That order survives only because `serde_json` is built with
/// `preserve_order`: its `Map` is a `BTreeMap` otherwise, and the properties of
/// a derived schema come back alphabetically — a form and a `--help` listing
/// fields in an order the type never stated. Yojson's `Assoc` is a list, so the
/// OCaml side has no equivalent hazard to defend against.
///
/// A schema with no `properties` at all is a form with no fields, which is what
/// an action taking no arguments advertises. A property whose type has no form
/// field fails the whole form rather than being dropped: dropping it would
/// render a form that cannot express the action, and submitting it would
/// produce a payload the decoder refuses for a reason nothing on screen
/// mentions.
pub fn of_schema(schema: &Value) -> Result<Vec<Field>, String> {
    if schema.get("type") != Some(&Value::String("object".into())) {
        return Err(format!("schema is not an object: {schema}"));
    }
    let required = match schema.get("required").and_then(Value::as_array) {
        Some(names) => strings(names),
        None => Vec::new(),
    };
    let properties = match schema.get("properties") {
        None => return Ok(Vec::new()),
        Some(Value::Object(properties)) => properties,
        Some(other) => return Err(format!("properties is not an object: {other}")),
    };

    properties
        .iter()
        .map(|(name, spec)| match kind_of(schema, spec) {
            Err(what) => Err(format!("{name}: no form field for type {what}")),
            Ok(kind) => Ok(Field {
                name: name.clone(),
                required: required.iter().any(|r| r == name),
                kind,
                description: spec
                    .get("description")
                    .and_then(Value::as_str)
                    .map(str::to_string),
            }),
        })
        .collect()
}

/// What the typed-in values make, ready for [`crate::platform::wire::dispatch`].
///
/// A blank [`Kind::OptionalText`] is sent as `null`, which is exactly what the
/// schema advertises. The OCaml side has to omit it instead:
/// `[@jsonschema.option]` widens the type to `["string", "null"]` while
/// `[@yojson.option]` means absent-or-a-string, so the decoder there refuses the
/// null its own schema offers. `serde` and `schemars` read the same attribute,
/// so a frontend that believes the schema is right here — which is finding 10's
/// control, and it clears the idea.
///
/// A blank [`Kind::Text`] is sent as `""` rather than withheld. The action's
/// `execute` is the enforcement point — `editTitle` is what decides a blank
/// title is refused, and a form that second-guessed it would be a second place
/// that has to agree.
///
/// A [`Kind::Enum`] is always sent, whether or not the schema marks it
/// required. A selector over a closed list has no blank state to mean "absent"
/// without inventing one, and the schema does not say which value the column
/// defaults to — so an optional enum left alone submits the first value it
/// advertises. That is the right answer only while the first value *is* the
/// default, which is true of `priority` and is not something this function can
/// check.
pub fn payload(values: &[(Field, String)]) -> Value {
    Value::Object(
        values
            .iter()
            .map(|(field, value)| {
                let json = match field.kind {
                    Kind::OptionalText if value.is_empty() => Value::Null,
                    _ => Value::String(value.clone()),
                };
                (field.name.clone(), json)
            })
            .collect(),
    )
}
