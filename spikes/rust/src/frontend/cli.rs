//! The erased path with a shell or an agent on the end of it.
//!
//! Same three calls as [`crate::frontend::tui`]: [`wire::available`] for what is
//! on offer, [`form::of_schema`] for the arguments, [`wire::dispatch`] for the
//! write. Nothing here names an action key — an action gets a subcommand
//! because it is registered, and that subcommand's options are the fields its
//! payload type derived. Adding a fifth issue action changes no line of this
//! file.
//!
//! Two ways in, over the same groups. `action KEY JSON` is the erased path
//! itself: one command carrying any action's payload as a blob, which is what
//! an agent holding the output of `show` already has. The per-action
//! subcommands are the same dispatch with the blob spelled out as options, for
//! a person at a prompt. Both end at [`wire::dispatch`], so neither can reach
//! an action the other cannot.
//!
//! What this file still states is how an address becomes an object. An action
//! writes for itself; it does not know that a project is found by slug and a
//! trash row by slug among the deleted. That pairing — a live object loaded one
//! way, a deleted one the other — is the two arms each of [`project_write`] and
//! [`issue_write`], inside one transaction so a refusal rolls the whole call
//! back.
//!
//! `clap`'s **builder** API, not `derive`. The subcommands are generated from
//! the registry at run time; a derived `enum Command` would name every action
//! key in a second place and reintroduce exactly the drift this design exists
//! to remove.
//!
//! A library rather than the executable, so a test can evaluate the tree
//! against an argv with no terminal involved. What is built here is a
//! [`clap::Command`] and a function from its matches to either what to print or
//! the refusal to report; `src/bin/cli.rs` is left with the runtime, the
//! printing and the exit code.

use clap::{Arg, ArgMatches, Command, builder::PossibleValuesParser};
use sea_orm::ConnectionTrait;
use serde_json::{Value, json};

use crate::domains::issue::Issue;
use crate::domains::issue::actions as issue_actions;
use crate::domains::issue::services as issue_services;
use crate::domains::project::actions as project_actions;
use crate::domains::project::services as project_services;
use crate::domains::project::{Project, Restorable};
use crate::platform::action::Offered;
use crate::platform::db::{self, Db};
use crate::platform::deleted::Deleted;
use crate::platform::error::Error;
use crate::platform::form::{self, Field, Kind};
use crate::platform::wire::{self, Entry};

pub type Outcome = Result<String, Error>;

// --- rendering ------------------------------------------------------------

fn project_line(p: &Project) -> String {
    format!(
        "{:<12} {:<24} {:<8} {} todo, {} doing, {} done",
        p.slug,
        p.title,
        wire::name_of(&p.status),
        p.todo,
        p.doing,
        p.done
    )
}

fn issue_line(i: &Issue) -> String {
    format!(
        "{:<4} {:<12} {:<8} {:<6} {}",
        i.id,
        i.project_slug,
        wire::name_of(&i.status),
        wire::name_of(&i.priority),
        i.title
    )
}

/// The domain types carry no `Serialize` — deriving it would make the wire
/// format a property of the domain rather than of this edge — so the one place
/// that spells their fields out is here.
fn project_json(p: &Project) -> Value {
    json!({
        "slug": p.slug,
        "title": p.title,
        "body": p.body,
        "status": wire::name_of(&p.status),
        "todo": p.todo,
        "doing": p.doing,
        "done": p.done,
        "created_at": p.created_at,
        "updated_at": p.updated_at,
    })
}

fn issue_json(i: &Issue) -> Value {
    json!({
        "id": i.id,
        "project": i.project_slug,
        "title": i.title,
        "body": i.body,
        "status": wire::name_of(&i.status),
        "priority": wire::name_of(&i.priority),
        "status_note": i.status_note,
        "created_at": i.created_at,
        "updated_at": i.updated_at,
    })
}

/// One offered action, as the JSON an agent reads before it picks anything.
///
/// The schema is passed through exactly as it was derived. Rendering it — as a
/// form, as a set of options, as an agent's tool definition — is the caller's
/// business.
fn offer(key: &str, schema: &Value, offered: &Offered) -> Value {
    let mut fields = serde_json::Map::new();
    fields.insert("key".into(), json!(key));
    match offered {
        Offered::Runnable => {
            fields.insert("state".into(), json!("runnable"));
        }
        Offered::Refused(reason) => {
            fields.insert("state".into(), json!("refused"));
            fields.insert("reason".into(), json!(reason));
        }
    }
    fields.insert("arguments".into(), schema.clone());
    Value::Object(fields)
}

fn offers<O>(group: &[Entry<O>], obj: &O) -> Vec<Value> {
    wire::available(group, obj)
        .iter()
        .map(|(entry, offered)| offer(entry.key, &entry.schema, offered))
        .collect()
}

// --- resolution -----------------------------------------------------------
//
// Generic over the connection, so the same load runs against the live database
// for a read and against an open transaction for a write.

async fn live_project<C: ConnectionTrait>(db: &C, slug: &str) -> Result<Project, Error> {
    project_services::project(db, slug)
        .await?
        .ok_or_else(|| Error::Invalid(format!("no project {slug:?}")))
}

/// A trash row is loaded together with the live projects, because that is what
/// `restore`'s refusal reads.
async fn restorable_project<C: ConnectionTrait>(db: &C, slug: &str) -> Result<Restorable, Error> {
    let deleted = project_services::trashed_projects(db)
        .await?
        .into_iter()
        .find(|d| d.inner.slug == slug)
        .ok_or_else(|| Error::Invalid(format!("no deleted project {slug:?}")))?;
    Ok(Restorable {
        deleted,
        live: project_services::projects(db).await?,
    })
}

async fn live_issue<C: ConnectionTrait>(db: &C, id: i64) -> Result<Issue, Error> {
    issue_services::issue(db, id)
        .await?
        .ok_or_else(|| Error::Invalid(format!("no issue {id}")))
}

async fn trashed_issue<C: ConnectionTrait>(db: &C, id: i64) -> Result<Deleted<Issue>, Error> {
    issue_services::trashed_issues(db)
        .await?
        .into_iter()
        .find(|d| d.inner.id == id)
        .ok_or_else(|| Error::Invalid(format!("no deleted issue {id}")))
}

fn holds<O>(key: &str, group: &[Entry<O>]) -> bool {
    group.iter().any(|entry| entry.key == key)
}

// --- the writes -----------------------------------------------------------
//
// Load then dispatch, inside one transaction — so `Action::run` stays the only
// path to a write, the hooks are checked against the row as it is now rather
// than as some earlier `show` reported it, and a refusal after rows are written
// rolls the whole call back. The store call each action ends in is the action's
// own, done by its `execute`, so all that is left here is choosing which loader
// an address becomes an object through: a live row for a `group` key, a
// restorable one for a `deleted_group` key.

async fn project_write(db: &Db, slug: &str, key: &str, payload: &Value) -> Outcome {
    if holds(key, &project_actions::group()) {
        db::transaction(db, async |tx| {
            let project = live_project(tx, slug).await?;
            wire::dispatch(&project_actions::group(), project, key, payload, tx).await
        })
        .await
    } else if holds(key, &project_actions::deleted_group()) {
        db::transaction(db, async |tx| {
            let restorable = restorable_project(tx, slug).await?;
            wire::dispatch(
                &project_actions::deleted_group(),
                restorable,
                key,
                payload,
                tx,
            )
            .await
        })
        .await
    } else {
        Err(Error::Invalid(format!("no action {key:?}")))
    }
}

async fn issue_write(db: &Db, id: i64, key: &str, payload: &Value) -> Outcome {
    if holds(key, &issue_actions::group()) {
        db::transaction(db, async |tx| {
            let issue = live_issue(tx, id).await?;
            wire::dispatch(&issue_actions::group(), issue, key, payload, tx).await
        })
        .await
    } else if holds(key, &issue_actions::deleted_group()) {
        db::transaction(db, async |tx| {
            let deleted = trashed_issue(tx, id).await?;
            wire::dispatch(&issue_actions::deleted_group(), deleted, key, payload, tx).await
        })
        .await
    } else {
        Err(Error::Invalid(format!("no action {key:?}")))
    }
}

/// The root creator has no object to address, so its object is the list of live
/// projects — which is exactly what its uniqueness refusal has to read.
async fn root_write(db: &Db, key: &str, payload: &Value) -> Outcome {
    db::transaction(db, async |tx| {
        let projects = project_services::projects(tx).await?;
        wire::dispatch(&project_actions::root(), projects, key, payload, tx).await
    })
    .await
}

// --- the reads ------------------------------------------------------------

async fn project_ls(db: &Db) -> Outcome {
    Ok(project_services::projects(db)
        .await?
        .iter()
        .map(project_line)
        .collect::<Vec<_>>()
        .join("\n"))
}

async fn issue_ls(db: &Db, project_slug: Option<&str>) -> Outcome {
    let slugs = match project_slug {
        Some(slug) => vec![live_project(db, slug).await?.slug],
        None => project_services::projects(db)
            .await?
            .into_iter()
            .map(|p| p.slug)
            .collect(),
    };
    let mut lines = Vec::new();
    for slug in slugs {
        for issue in issue_services::issues(db, &slug).await? {
            lines.push(issue_line(&issue));
        }
    }
    Ok(lines.join("\n"))
}

async fn project_show(db: &Db, slug: &str) -> Outcome {
    let project = live_project(db, slug).await?;
    let actions = offers(&project_actions::group(), &project);
    Ok(pretty(
        &json!({ "project": project_json(&project), "actions": actions }),
    ))
}

async fn issue_show(db: &Db, id: i64) -> Outcome {
    let issue = live_issue(db, id).await?;
    let actions = offers(&issue_actions::group(), &issue);
    Ok(pretty(
        &json!({ "issue": issue_json(&issue), "actions": actions }),
    ))
}

/// The trash, with what each row offers — which is `restore` and nothing else,
/// because that is the only action registered against the deleted types.
async fn trash(db: &Db) -> Outcome {
    let live = project_services::projects(db).await?;
    let projects: Vec<Value> = project_services::trashed_projects(db)
        .await?
        .into_iter()
        .map(|deleted| {
            let slug = deleted.inner.slug.clone();
            let deleted_at = deleted.deleted_at.clone();
            let restorable = Restorable {
                deleted,
                live: live.clone(),
            };
            json!({
                "project": slug,
                "deleted_at": deleted_at,
                "actions": offers(&project_actions::deleted_group(), &restorable),
            })
        })
        .collect();
    let issues: Vec<Value> = issue_services::trashed_issues(db)
        .await?
        .into_iter()
        .map(|deleted| {
            json!({
                "issue": deleted.inner.id,
                "title": deleted.inner.title,
                "deleted_at": deleted.deleted_at,
                "actions": offers(&issue_actions::deleted_group(), &deleted),
            })
        })
        .collect();
    Ok(pretty(&json!({ "projects": projects, "issues": issues })))
}

fn pretty(value: &Value) -> String {
    serde_json::to_string_pretty(value).unwrap_or_else(|e| e.to_string())
}

// --- the command tree -----------------------------------------------------

/// Every key one kind of object answers to, with the schema it advertises.
///
/// Two groups flattened into one list, because a subcommand is a subcommand
/// whether the write behind it is an `UPDATE`, an `INSERT` or a soft delete.
/// Which of those it is stays the action's `execute`'s business.
fn project_keys() -> Vec<(&'static str, Value)> {
    let mut keys: Vec<(&'static str, Value)> = Vec::new();
    for entry in project_actions::group() {
        keys.push((entry.key, entry.schema));
    }
    for entry in project_actions::deleted_group() {
        keys.push((entry.key, entry.schema));
    }
    keys
}

fn issue_keys() -> Vec<(&'static str, Value)> {
    let mut keys: Vec<(&'static str, Value)> = Vec::new();
    for entry in issue_actions::group() {
        keys.push((entry.key, entry.schema));
    }
    for entry in issue_actions::deleted_group() {
        keys.push((entry.key, entry.schema));
    }
    keys
}

/// One option per field of the payload, and — clap rejecting what it was not
/// told about — nothing else.
///
/// `required` comes from the schema, so a missing `--title` is a usage error
/// rather than a payload the decoder refuses further in. The requirement is not
/// restated here, only enforced earlier; what a title has to *contain* is still
/// the action's, which is why a blank one gets past this and is refused by
/// `execute`.
///
/// A [`Kind::Enum`] becomes a [`PossibleValuesParser`], so a bad status is a
/// usage error listing the alternatives rather than a decode failure quoting a
/// type name. `description` becomes the help text. Both are things the OCaml
/// side has nothing to fill: `ppx_deriving_jsonschema` emits no doc comment,
/// and its enum values only exist because that spike writes the table by hand.
fn option(field: &Field) -> Arg {
    let arg = Arg::new(field.name.clone())
        .long(field.name.clone())
        .value_name("VALUE")
        .required(field.required)
        .help(field.description.clone().unwrap_or_default());
    match &field.kind {
        Kind::Enum(values) => arg.value_parser(PossibleValuesParser::new(values)),
        Kind::Text | Kind::OptionalText => arg,
    }
}

/// One subcommand per registered action, whether or not it applies right now.
///
/// Availability belongs to an object and a command tree does not have one yet —
/// the subcommand is parsed before the row has been read. So `delete` is a
/// command on an active project too, and the refusal comes back from the
/// dispatch against the live object rather than from the parser. This is the
/// same snapshot the TUI's menu draws, taken earlier.
///
/// A schema with no options to render is reported rather than approximated,
/// exactly as the TUI reports it: the subcommand exists and refuses, because
/// one that dropped the field it could not render would be refused by the
/// decoder for a reason nothing in `--help` mentions.
fn generated(key: &'static str, schema: &Value, address: Arg) -> Command {
    let command = Command::new(key)
        .about(format!("Run the {key} action."))
        .arg(address);
    match form::of_schema(schema) {
        Err(_) => command,
        Ok(fields) => command.args(fields.iter().map(option)),
    }
}

fn raw(address: Arg) -> Command {
    Command::new("action")
        .about("Run an action by key, passing its arguments as JSON.")
        .arg(address)
        .arg(
            Arg::new("key")
                .value_name("KEY")
                .required(true)
                .help("The action to run."),
        )
        .arg(
            Arg::new("payload")
                .value_name("JSON")
                .default_value("{}")
                .help("The action's arguments, as a JSON object."),
        )
}

fn slug() -> Arg {
    Arg::new("slug")
        .value_name("SLUG")
        .required(true)
        .help("The project's slug.")
}

fn issue_id() -> Arg {
    Arg::new("id")
        .value_name("ID")
        .required(true)
        .value_parser(clap::value_parser!(i64))
        .help("The issue's id.")
}

/// The whole tree. Pure — it reads the registries and no database.
pub fn command() -> Command {
    let project = project_keys()
        .into_iter()
        .map(|(key, schema)| generated(key, &schema, slug()))
        .fold(
            Command::new("project")
                .about("Projects.")
                .subcommand_required(true)
                .subcommand(Command::new("ls").about("List live projects."))
                .subcommand(
                    Command::new("show")
                        .about("Print a project and what it offers.")
                        .arg(slug()),
                )
                .subcommand(raw(slug())),
            Command::subcommand,
        );

    let issue = issue_keys()
        .into_iter()
        .map(|(key, schema)| generated(key, &schema, issue_id()))
        .fold(
            Command::new("issue")
                .about("Issues.")
                .subcommand_required(true)
                .subcommand(
                    Command::new("ls").about("List live issues.").arg(
                        Arg::new("project")
                            .long("project")
                            .value_name("SLUG")
                            .help("Only this project."),
                    ),
                )
                .subcommand(
                    Command::new("show")
                        .about("Print an issue and what it offers.")
                        .arg(issue_id()),
                )
                .subcommand(raw(issue_id())),
            Command::subcommand,
        );

    Command::new("tt")
        .about("A small task tracker, driven from a command line.")
        .subcommand_required(true)
        .arg(
            Arg::new("db")
                .long("db")
                .value_name("URI")
                .env("TT_DB")
                .default_value("sqlite:tt.db?mode=rwc")
                .global(true)
                .help("The database to open."),
        )
        .subcommand(project)
        .subcommand(issue)
        // The root creator addresses no object, so its `action` takes a key and
        // a blob and nothing else.
        .subcommand(
            Command::new("action")
                .about("Run a root action by key, passing its arguments as JSON.")
                .arg(
                    Arg::new("key")
                        .value_name("KEY")
                        .required(true)
                        .help("The action to run."),
                )
                .arg(
                    Arg::new("payload")
                        .value_name("JSON")
                        .default_value("{}")
                        .help("The action's arguments, as a JSON object."),
                ),
        )
        .subcommand(Command::new("trash").about("What has been deleted, and what it offers."))
}

pub fn database(matches: &ArgMatches) -> String {
    matches
        .get_one::<String>("db")
        .cloned()
        .unwrap_or_else(|| "sqlite:tt.db?mode=rwc".to_string())
}

/// What the typed-in options make.
///
/// An option that was not passed is dropped rather than sent, which is what
/// lets `--note` absent and `--note ""` mean different things here: the first
/// omits the field, the second sends the `null` [`form::payload`] encodes a
/// blank optional as. A TUI has one blank state and cannot tell them apart.
fn payload_of(fields: &[Field], matches: &ArgMatches) -> Value {
    let values: Vec<(Field, String)> = fields
        .iter()
        .filter_map(|field| {
            matches
                .get_one::<String>(&field.name)
                .map(|value| (field.clone(), value.clone()))
        })
        .collect();
    form::payload(&values)
}

fn blob(matches: &ArgMatches) -> Result<Value, Error> {
    let raw = matches
        .get_one::<String>("payload")
        .map(String::as_str)
        .unwrap_or("{}");
    serde_json::from_str(raw).map_err(|e| Error::Invalid(e.to_string()))
}

fn key_of(matches: &ArgMatches) -> &str {
    matches
        .get_one::<String>("key")
        .map(String::as_str)
        .unwrap_or("")
}

/// The generated subcommand's arguments, or the reason its schema could not be
/// rendered as options at all.
fn generated_payload(
    keys: &[(&'static str, Value)],
    key: &str,
    matches: &ArgMatches,
) -> Result<Value, Error> {
    let schema = keys
        .iter()
        .find(|(k, _)| *k == key)
        .map(|(_, schema)| schema)
        .ok_or_else(|| Error::Invalid(format!("no action {key:?}")))?;
    match form::of_schema(schema) {
        Err(message) => Err(Error::Invalid(format!("{key}: {message}"))),
        Ok(fields) => Ok(payload_of(&fields, matches)),
    }
}

async fn project_command(db: &Db, matches: &ArgMatches) -> Outcome {
    match matches.subcommand() {
        Some(("ls", _)) => project_ls(db).await,
        Some(("show", m)) => project_show(db, m.get_one::<String>("slug").expect("required")).await,
        Some(("action", m)) => {
            project_write(
                db,
                m.get_one::<String>("slug").expect("required"),
                key_of(m),
                &blob(m)?,
            )
            .await
        }
        Some((key, m)) => {
            let payload = generated_payload(&project_keys(), key, m)?;
            project_write(
                db,
                m.get_one::<String>("slug").expect("required"),
                key,
                &payload,
            )
            .await
        }
        None => Err(Error::Invalid("no subcommand".into())),
    }
}

async fn issue_command(db: &Db, matches: &ArgMatches) -> Outcome {
    match matches.subcommand() {
        Some(("ls", m)) => issue_ls(db, m.get_one::<String>("project").map(String::as_str)).await,
        Some(("show", m)) => issue_show(db, *m.get_one::<i64>("id").expect("required")).await,
        Some(("action", m)) => {
            issue_write(
                db,
                *m.get_one::<i64>("id").expect("required"),
                key_of(m),
                &blob(m)?,
            )
            .await
        }
        Some((key, m)) => {
            let payload = generated_payload(&issue_keys(), key, m)?;
            issue_write(
                db,
                *m.get_one::<i64>("id").expect("required"),
                key,
                &payload,
            )
            .await
        }
        None => Err(Error::Invalid("no subcommand".into())),
    }
}

/// One parsed command line against one open database.
pub async fn run(db: &Db, matches: &ArgMatches) -> Outcome {
    match matches.subcommand() {
        Some(("project", m)) => project_command(db, m).await,
        Some(("issue", m)) => issue_command(db, m).await,
        Some(("action", m)) => root_write(db, key_of(m), &blob(m)?).await,
        Some(("trash", _)) => trash(db).await,
        _ => Err(Error::Invalid("no command".into())),
    }
}
