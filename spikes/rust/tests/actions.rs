//! The action layer, over a real in-memory SQLite database.
//!
//! Availability is a pure function of the object, so the menu cases run against
//! literals with no database in sight. `execute` is not pure any more — it holds
//! the transaction and writes for itself — so the cases that `run` an action
//! seed a row, run, and assert both the message that comes back and the row's
//! new state.
//!
//! Per the root `CLAUDE.md`, every action key gets two cases: the menu withholds
//! it, and the write refuses it. **No issue action refuses anything** — there is
//! no WIP rule and nothing else about an issue constrains what may be done to
//! it — so for those the honest pair is "always offered" and the refusal
//! `execute` states. Saying so in the test names is better than inventing a rule
//! to satisfy the shape.

use std::borrow::Cow;

use serde_json::{Value, json};

use tt_spike::Error;
use tt_spike::domains::issue::actions as issue_actions;
use tt_spike::domains::issue::schemas as issue_schemas;
use tt_spike::domains::issue::services as issue_services;
use tt_spike::domains::issue::{self, Issue, Priority, Status};
use tt_spike::domains::project::actions as project_actions;
use tt_spike::domains::project::schemas as project_schemas;
use tt_spike::domains::project::services as project_services;
use tt_spike::domains::project::{self, Project, Restorable};
use tt_spike::domains::schema;
use tt_spike::platform::action::{Action, Offered};
use tt_spike::platform::db::{self, Db};
use tt_spike::platform::deleted::Deleted;
use tt_spike::platform::wire;

// --- fixtures -------------------------------------------------------------

async fn fixture() -> Db {
    let db = db::connect("sqlite::memory:").await.expect("connect");
    schema::initialise(&db).await.expect("DDL");
    db
}

async fn a_project(db: &Db, slug: &str) -> Project {
    project_services::create_project(
        db,
        project::Draft {
            slug: slug.into(),
            title: slug.into(),
            body: String::new(),
        },
    )
    .await
    .expect("create_project")
}

async fn an_issue(db: &Db, project: &Project, title: &str) -> Issue {
    issue_services::create_issue(
        db,
        issue::Draft {
            project_id: project.id,
            title: title.into(),
            body: String::new(),
            priority: Priority::Normal,
        },
    )
    .await
    .expect("create_issue")
}

/// Run a typed action inside one transaction, committing on success so a later
/// read sees what it wrote. This is `Action::run` with the transaction the
/// frontends open around it.
async fn run<A: Action>(db: &Db, obj: A::Obj, payload: A::Payload) -> Result<String, Error> {
    db::transaction(db, async move |tx| A::run(obj, payload, tx).await).await
}

/// The same, through the erased wire path: decode the blob against the group,
/// then run.
async fn dispatch<O>(
    db: &Db,
    group: &[wire::Entry<O>],
    obj: O,
    key: &str,
    payload: &Value,
) -> Result<String, Error> {
    db::transaction(db, async move |tx| {
        wire::dispatch(group, obj, key, payload, tx).await
    })
    .await
}

// --- literals, for the availability cases ---------------------------------

fn issue() -> Issue {
    Issue {
        id: 1,
        project_id: 1,
        project_slug: "tt".into(),
        title: "a title".into(),
        body: String::new(),
        status: Status::Todo,
        priority: Priority::Normal,
        status_note: None,
        created_at: "2026-01-01T00:00:00Z".into(),
        updated_at: "2026-01-01T00:00:00Z".into(),
    }
}

fn project() -> Project {
    Project {
        id: 1,
        slug: "tt".into(),
        title: "task tracker".into(),
        body: String::new(),
        status: project::Status::Active,
        todo: 0,
        doing: 0,
        done: 0,
        created_at: "2026-01-01T00:00:00Z".into(),
        updated_at: "2026-01-01T00:00:00Z".into(),
    }
}

fn refused(reason: &str) -> Offered {
    Offered::Refused(Cow::Owned(reason.to_string()))
}

// --- issues: the menu never withholds anything ----------------------------

#[test]
fn every_issue_edit_is_always_offered() {
    for status in [Status::Todo, Status::Doing, Status::Done] {
        for priority in [Priority::Normal, Priority::High] {
            let issue = Issue {
                status,
                priority,
                ..issue()
            };
            assert_eq!(
                issue_actions::EditTitle::availability(&issue),
                Some(Offered::Runnable)
            );
            assert_eq!(
                issue_actions::EditBody::availability(&issue),
                Some(Offered::Runnable)
            );
            assert_eq!(
                issue_actions::EditStatus::availability(&issue),
                Some(Offered::Runnable)
            );
            assert_eq!(
                issue_actions::EditPriority::availability(&issue),
                Some(Offered::Runnable)
            );
            assert_eq!(
                issue_actions::Delete::availability(&issue),
                Some(Offered::Runnable)
            );
        }
    }
}

#[tokio::test]
async fn edit_title_trims_saves_and_refuses_a_blank_title() {
    let db = fixture().await;
    let tt = a_project(&db, "tt").await;
    let seeded = an_issue(&db, &tt, "old").await;

    let message = run::<issue_actions::EditTitle>(
        &db,
        seeded.clone(),
        issue_schemas::EditTitlePayload {
            title: " new ".into(),
        },
    )
    .await
    .expect("editTitle should run");
    assert_eq!(message, "issue 1: saved");
    let read = issue_services::issue(&db, seeded.id)
        .await
        .expect("load")
        .expect("issue is there");
    assert_eq!(read.title, "new");

    assert!(matches!(
        run::<issue_actions::EditTitle>(
            &db,
            seeded,
            issue_schemas::EditTitlePayload {
                title: "   ".into()
            }
        )
        .await,
        Err(Error::Invalid(_))
    ));
}

#[tokio::test]
async fn edit_body_accepts_the_blank_that_edit_title_refuses() {
    // The same payload shape and a different rule, which is what an action still
    // earns over a form generated from the schema.
    let db = fixture().await;
    let tt = a_project(&db, "tt").await;
    let seeded = an_issue(&db, &tt, "one").await;

    run::<issue_actions::EditBody>(
        &db,
        seeded.clone(),
        issue_schemas::EditBodyPayload {
            body: String::new(),
        },
    )
    .await
    .expect("editBody should run");
    let read = issue_services::issue(&db, seeded.id)
        .await
        .expect("load")
        .expect("issue is there");
    assert_eq!(read.body, "");
}

#[tokio::test]
async fn edit_status_replaces_the_note_it_arrived_with() {
    let db = fixture().await;
    let tt = a_project(&db, "tt").await;
    let seeded = an_issue(&db, &tt, "one").await;

    run::<issue_actions::EditStatus>(
        &db,
        seeded.clone(),
        issue_schemas::EditStatusPayload {
            status: Status::Doing,
            note: Some("started".into()),
        },
    )
    .await
    .expect("editStatus should run");
    let noted = issue_services::issue(&db, seeded.id)
        .await
        .expect("load")
        .expect("issue is there");
    assert_eq!(noted.status, Status::Doing);
    assert_eq!(noted.status_note, Some("started".into()));

    // Moving without one clears the old note rather than leaving it to describe
    // a state the issue is no longer in.
    run::<issue_actions::EditStatus>(
        &db,
        noted,
        issue_schemas::EditStatusPayload {
            status: Status::Done,
            note: None,
        },
    )
    .await
    .expect("editStatus should run");
    let cleared = issue_services::issue(&db, seeded.id)
        .await
        .expect("load")
        .expect("issue is there");
    assert_eq!(cleared.status_note, None);
}

#[tokio::test]
async fn edit_priority_sets_the_priority() {
    let db = fixture().await;
    let tt = a_project(&db, "tt").await;
    let seeded = an_issue(&db, &tt, "one").await;

    run::<issue_actions::EditPriority>(
        &db,
        seeded.clone(),
        issue_schemas::EditPriorityPayload {
            priority: Priority::High,
        },
    )
    .await
    .expect("editPriority should run");
    let read = issue_services::issue(&db, seeded.id)
        .await
        .expect("load")
        .expect("issue is there");
    assert_eq!(read.priority, Priority::High);
}

#[tokio::test]
async fn delete_hides_an_issue_and_restore_brings_it_back() {
    let db = fixture().await;
    let tt = a_project(&db, "tt").await;
    let seeded = an_issue(&db, &tt, "here").await;

    let message = run::<issue_actions::Delete>(&db, seeded.clone(), Default::default())
        .await
        .expect("delete should run");
    assert_eq!(message, "issue 1: deleted");
    assert!(
        issue_services::issue(&db, seeded.id)
            .await
            .expect("load")
            .is_none()
    );

    let deleted = issue_services::trashed_issues(&db)
        .await
        .expect("trash")
        .remove(0);
    assert_eq!(
        issue_actions::Restore::availability(&deleted),
        Some(Offered::Runnable)
    );
    let message = run::<issue_actions::Restore>(&db, deleted, Default::default())
        .await
        .expect("restore should run");
    assert_eq!(message, "issue 1: restored");
    assert!(
        issue_services::issue(&db, seeded.id)
            .await
            .expect("load")
            .is_some()
    );
}

// --- projects: the half with preconditions --------------------------------

#[tokio::test]
async fn edit_status_is_refused_while_anything_is_doing() {
    let busy = Project {
        doing: 1,
        ..project()
    };
    assert_eq!(
        project_actions::EditStatus::availability(&busy),
        Some(refused("finish or drop 1 issue first"))
    );
    // The count is per-object, which is what `Cow` in `Offered::Refused` buys.
    let busier = Project {
        doing: 3,
        ..project()
    };
    assert_eq!(
        project_actions::EditStatus::availability(&busier),
        Some(refused("finish or drop 3 issues first"))
    );
    // Stated against the object rather than the payload, so asking to stay
    // active is refused too. The refusal is `run`'s, before any write.
    let db = fixture().await;
    assert!(matches!(
        run::<project_actions::EditStatus>(
            &db,
            busy,
            project_schemas::EditStatusPayload {
                status: project::Status::Active
            }
        )
        .await,
        Err(Error::Conflict(_))
    ));
}

#[tokio::test]
async fn edit_status_is_runnable_once_nothing_is_doing() {
    let db = fixture().await;
    let seeded = a_project(&db, "tt").await;
    assert_eq!(
        project_actions::EditStatus::availability(&seeded),
        Some(Offered::Runnable)
    );
    let message = run::<project_actions::EditStatus>(
        &db,
        seeded,
        project_schemas::EditStatusPayload {
            status: project::Status::Archived,
        },
    )
    .await
    .expect("editStatus should run");
    assert_eq!(message, "project tt: saved");
    assert_eq!(
        project_services::project(&db, "tt")
            .await
            .expect("load")
            .expect("project is there")
            .status,
        project::Status::Archived
    );
}

#[tokio::test]
async fn delete_is_refused_while_the_project_is_active() {
    assert_eq!(
        project_actions::Delete::availability(&project()),
        Some(refused("archive it first"))
    );
    let db = fixture().await;
    let seeded = a_project(&db, "tt").await;
    assert!(matches!(
        run::<project_actions::Delete>(&db, seeded, Default::default()).await,
        Err(Error::Conflict(_))
    ));

    // Archive it, and it goes.
    project_services::update_project(
        &db,
        &Project {
            status: project::Status::Archived,
            ..project_services::project(&db, "tt")
                .await
                .expect("load")
                .expect("project is there")
        },
    )
    .await
    .expect("archive");
    let archived = project_services::project(&db, "tt")
        .await
        .expect("load")
        .expect("project is there");
    assert_eq!(
        project_actions::Delete::availability(&archived),
        Some(Offered::Runnable)
    );
    let message = run::<project_actions::Delete>(&db, archived, Default::default())
        .await
        .expect("delete should run");
    assert_eq!(message, "project tt: deleted");
    assert!(
        project_services::project(&db, "tt")
            .await
            .expect("load")
            .is_none()
    );
}

#[tokio::test]
async fn add_issue_is_refused_while_the_project_is_archived() {
    let archived = Project {
        status: project::Status::Archived,
        ..project()
    };
    assert_eq!(
        project_actions::AddIssue::availability(&archived),
        Some(refused("project is archived"))
    );
    let db = fixture().await;
    assert!(matches!(
        run::<project_actions::AddIssue>(
            &db,
            archived,
            project_schemas::AddIssuePayload {
                title: "nope".into(),
                body: None,
                priority: None,
            }
        )
        .await,
        Err(Error::Conflict(_))
    ));
}

#[tokio::test]
async fn add_issue_defaults_what_the_payload_leaves_out() {
    let db = fixture().await;
    let seeded = a_project(&db, "tt").await;
    let message = run::<project_actions::AddIssue>(
        &db,
        seeded,
        project_schemas::AddIssuePayload {
            title: " ship it ".into(),
            body: None,
            priority: None,
        },
    )
    .await
    .expect("addIssue should run");
    assert_eq!(message, "issue 1: created");
    let issue = &issue_services::issues(&db, "tt").await.expect("issues")[0];
    assert_eq!(issue.title, "ship it");
    assert_eq!(issue.body, "");
    assert_eq!(issue.priority, Priority::Normal);

    assert!(matches!(
        run::<project_actions::AddIssue>(
            &db,
            project_services::project(&db, "tt")
                .await
                .expect("load")
                .expect("project is there"),
            project_schemas::AddIssuePayload {
                title: "  ".into(),
                body: None,
                priority: None,
            }
        )
        .await,
        Err(Error::Invalid(_))
    ));
}

#[tokio::test]
async fn create_project_refuses_a_slug_the_list_already_holds() {
    // A hook is given the object and not the payload, so this cannot be an
    // availability hook — the creator is always offered, and `execute` is what
    // refuses.
    assert_eq!(
        project_actions::CreateProject::availability(&vec![project()]),
        Some(Offered::Runnable)
    );
    let db = fixture().await;
    let tt = a_project(&db, "tt").await;
    assert!(matches!(
        run::<project_actions::CreateProject>(
            &db,
            vec![tt],
            project_schemas::CreateProjectPayload {
                slug: "tt".into(),
                title: None,
                body: None,
            }
        )
        .await,
        Err(Error::Conflict(_))
    ));
    assert!(matches!(
        run::<project_actions::CreateProject>(
            &db,
            vec![],
            project_schemas::CreateProjectPayload {
                slug: "  ".into(),
                title: None,
                body: None,
            }
        )
        .await,
        Err(Error::Invalid(_))
    ));
    let message = run::<project_actions::CreateProject>(
        &db,
        vec![],
        project_schemas::CreateProjectPayload {
            slug: "other".into(),
            title: Some("another".into()),
            body: None,
        },
    )
    .await
    .expect("createProject should run");
    assert_eq!(message, "project other: created");
    assert_eq!(
        project_services::project(&db, "other")
            .await
            .expect("load")
            .expect("project is there")
            .title,
        "another"
    );
}

#[tokio::test]
async fn restore_is_refused_once_the_slug_is_taken_again() {
    // The trash row alone cannot answer this, which is why the object `restore`
    // is offered against carries the live list as well.
    let restorable = |live: Vec<Project>| Restorable {
        deleted: Deleted {
            inner: project(),
            deleted_at: "2026-01-02T00:00:00Z".into(),
        },
        live,
    };
    assert_eq!(
        project_actions::Restore::availability(&restorable(vec![])),
        Some(Offered::Runnable)
    );
    assert_eq!(
        project_actions::Restore::availability(&restorable(vec![project()])),
        Some(refused("project \"tt\" exists again"))
    );
    let db = fixture().await;
    assert!(matches!(
        run::<project_actions::Restore>(&db, restorable(vec![project()]), Default::default()).await,
        Err(Error::Conflict(_))
    ));
}

// --- the wire -------------------------------------------------------------

fn keys<O>(group: &[wire::Entry<O>], obj: &O) -> Vec<&'static str> {
    wire::available(group, obj)
        .iter()
        .map(|(entry, _)| entry.key)
        .collect()
}

fn schema_of<O>(group: &[wire::Entry<O>], key: &str) -> Value {
    group
        .iter()
        .find(|entry| entry.key == key)
        .expect("action should be registered")
        .schema
        .clone()
}

#[test]
fn a_group_keeps_refused_actions_and_drops_absent_ones() {
    // Nothing in this spike is ever absent, so what is being shown is that a
    // refusal survives to the menu with its reason attached — which is the whole
    // of what availability buys a frontend.
    assert_eq!(
        keys(&issue_actions::group(), &issue()),
        vec![
            "editTitle",
            "editBody",
            "editStatus",
            "editPriority",
            "delete"
        ]
    );
    // An active project with two issues doing: the edits are offered, editStatus
    // and delete come back refused, and addIssue is runnable — one group, told
    // apart by what each `execute` writes rather than by which list it sat in.
    let busy = Project {
        doing: 2,
        ..project()
    };
    assert_eq!(
        wire::available(&project_actions::group(), &busy)
            .into_iter()
            .map(|(entry, offered)| (entry.key, offered))
            .collect::<Vec<_>>(),
        vec![
            ("editTitle", Offered::Runnable),
            ("editBody", Offered::Runnable),
            ("editStatus", refused("finish or drop 2 issues first")),
            ("delete", refused("archive it first")),
            ("addIssue", Offered::Runnable),
        ]
    );
}

#[tokio::test]
async fn dispatch_refuses_what_availability_refused() {
    let db = fixture().await;
    assert!(matches!(
        dispatch(
            &db,
            &project_actions::group(),
            project(),
            "delete",
            &json!({})
        )
        .await,
        Err(Error::Conflict(_))
    ));
}

#[tokio::test]
async fn the_wire_agrees_with_the_typed_path() {
    // Two fresh databases, each seeded the same way, so the erased path and the
    // typed one produce the same message and the same row.
    let erased = fixture().await;
    let tt = a_project(&erased, "tt").await;
    let seeded = an_issue(&erased, &tt, "old").await;
    let via_wire = dispatch(
        &erased,
        &issue_actions::group(),
        seeded,
        "editTitle",
        &json!({ "title": " new " }),
    )
    .await;

    let typed = fixture().await;
    let tt = a_project(&typed, "tt").await;
    let seeded = an_issue(&typed, &tt, "old").await;
    let via_typed = run::<issue_actions::EditTitle>(
        &typed,
        seeded,
        issue_schemas::EditTitlePayload {
            title: " new ".into(),
        },
    )
    .await;

    assert_eq!(via_wire, via_typed);
    assert_eq!(via_wire, Ok("issue 1: saved".to_string()));

    // The same for a creator, whose `execute` writes a different table. Each
    // fresh database assigns the created issue id 1, so the messages match.
    let erased = fixture().await;
    let tt = a_project(&erased, "tt").await;
    let via_wire = dispatch(
        &erased,
        &project_actions::group(),
        tt,
        "addIssue",
        &json!({ "title": "one" }),
    )
    .await;

    let typed = fixture().await;
    let tt = a_project(&typed, "tt").await;
    let via_typed = run::<project_actions::AddIssue>(
        &typed,
        tt,
        project_schemas::AddIssuePayload {
            title: "one".into(),
            body: None,
            priority: None,
        },
    )
    .await;

    assert_eq!(via_wire, via_typed);
    assert_eq!(via_wire, Ok("issue 1: created".to_string()));
}

#[tokio::test]
async fn a_malformed_payload_is_invalid() {
    let db = fixture().await;
    for payload in [
        json!({}),                           // missing a required field
        json!({ "title": 5 }),               // wrong type
        json!({ "title": "x", "bogus": 1 }), // not advertised
    ] {
        assert!(matches!(
            dispatch(&db, &issue_actions::group(), issue(), "editTitle", &payload).await,
            Err(Error::Invalid(_))
        ));
    }
    // A value outside the enum, which is the one the schema could have told the
    // caller about in advance.
    assert!(matches!(
        dispatch(
            &db,
            &issue_actions::group(),
            issue(),
            "editStatus",
            &json!({ "status": "shipped" })
        )
        .await,
        Err(Error::Invalid(_))
    ));
}

#[tokio::test]
async fn an_action_with_no_arguments_takes_an_empty_object_and_not_null() {
    let db = fixture().await;
    let tt = a_project(&db, "tt").await;
    let seeded = an_issue(&db, &tt, "here").await;
    assert!(
        dispatch(&db, &issue_actions::group(), seeded, "delete", &json!({}))
            .await
            .is_ok()
    );
    assert!(matches!(
        dispatch(
            &db,
            &issue_actions::group(),
            issue(),
            "delete",
            &Value::Null
        )
        .await,
        Err(Error::Invalid(_))
    ));
}

#[tokio::test]
async fn an_unknown_key_is_invalid() {
    let db = fixture().await;
    assert!(matches!(
        dispatch(&db, &issue_actions::group(), issue(), "explode", &json!({})).await,
        Err(Error::Invalid(_))
    ));
    assert!(matches!(
        dispatch(&db, &project_actions::root(), vec![], "explode", &json!({})).await,
        Err(Error::Invalid(_))
    ));
}

#[tokio::test]
async fn one_key_in_two_groups_decodes_against_the_group_it_was_dispatched_on() {
    // `editTitle`, `editBody` and `editStatus` are registered against both
    // objects. In Rust these are distinct structs in distinct modules, so there
    // is nothing to collide; what is being asserted is that a dispatcher which
    // resolved the key before it resolved the object kind gets a decode failure
    // rather than the wrong write.
    let issue_keys = keys(&issue_actions::group(), &issue());
    let project_keys = keys(&project_actions::group(), &project());
    for key in ["editTitle", "editBody", "editStatus"] {
        assert!(issue_keys.contains(&key) && project_keys.contains(&key));
    }

    let db = fixture().await;
    // A project status through the issue's `editStatus`.
    assert!(matches!(
        dispatch(
            &db,
            &issue_actions::group(),
            issue(),
            "editStatus",
            &json!({ "status": "archived" })
        )
        .await,
        Err(Error::Invalid(_))
    ));
    // An issue status, and an issue-only field, through the project's.
    assert!(matches!(
        dispatch(
            &db,
            &project_actions::group(),
            project(),
            "editStatus",
            &json!({ "status": "doing" })
        )
        .await,
        Err(Error::Invalid(_))
    ));
    assert!(matches!(
        dispatch(
            &db,
            &project_actions::group(),
            project(),
            "editStatus",
            &json!({ "status": "active", "note": "why" })
        )
        .await,
        Err(Error::Invalid(_))
    ));
}

// --- the derived schemas --------------------------------------------------
//
// Snapshots, so a change to a payload type shows up here as a diff. These are
// what `tt issue show` hands an agent.

#[test]
fn a_required_field_carries_its_doc_comment() {
    assert_eq!(
        schema_of(&issue_actions::group(), "editTitle"),
        json!({
            "type": "object",
            "additionalProperties": false,
            "properties": {
                "title": { "type": "string", "description": "What to call the issue." }
            },
            "required": ["title"],
            "title": "EditTitlePayload",
            "$schema": "https://json-schema.org/draft/2020-12/schema"
        })
    );
}

#[test]
fn an_enum_field_is_a_ref_into_defs() {
    assert_eq!(
        schema_of(&issue_actions::group(), "editStatus"),
        json!({
            "type": "object",
            "additionalProperties": false,
            "properties": {
                "status": {
                    "$ref": "#/$defs/Status",
                    "description": "Where the issue is up to."
                },
                "note": {
                    "type": ["string", "null"],
                    "description": "What to record about the move."
                }
            },
            "required": ["status"],
            "title": "EditStatusPayload",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$defs": {
                "Status": {
                    "type": "string",
                    "enum": ["todo", "doing", "done"],
                    "description": "Where an issue is up to."
                }
            }
        })
    );
}

#[test]
fn an_optional_enum_is_an_any_of_with_a_null_arm() {
    let schema = schema_of(&project_actions::group(), "addIssue");
    assert_eq!(
        schema["properties"]["priority"],
        json!({
            "anyOf": [{ "$ref": "#/$defs/Priority" }, { "type": "null" }],
            "description": "How far up the list it sorts. Defaults to normal."
        })
    );
    assert_eq!(schema["required"], json!(["title"]));
}

#[test]
fn an_action_with_no_arguments_advertises_no_properties() {
    let schema = schema_of(&issue_actions::group(), "delete");
    assert_eq!(schema["type"], json!("object"));
    assert_eq!(schema["additionalProperties"], json!(false));
    assert!(schema.get("properties").is_none());
    assert!(schema.get("required").is_none());
}

#[tokio::test]
async fn the_advertised_schema_accepts_what_the_decoder_accepts() {
    // `serde` and `schemars` read the same attributes, so the schema saying
    // `note` may be null and the decoder accepting null are one decision. The
    // OCaml side asserts the opposite, because its two ppxes are two programs.
    let db = fixture().await;
    let tt = a_project(&db, "tt").await;
    let seeded = an_issue(&db, &tt, "one").await;
    assert!(
        dispatch(
            &db,
            &issue_actions::group(),
            seeded.clone(),
            "editStatus",
            &json!({ "status": "doing", "note": null })
        )
        .await
        .is_ok()
    );
    assert!(
        dispatch(
            &db,
            &issue_actions::group(),
            seeded,
            "editStatus",
            &json!({ "status": "doing" })
        )
        .await
        .is_ok()
    );
}
