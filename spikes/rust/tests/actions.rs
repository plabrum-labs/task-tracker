//! The action layer, with no database in sight.
//!
//! Every `execute` is a pure function of its object and its payload, so all of
//! this runs against literals. The first half is the typed path — payload
//! structs, no JSON. The second is the wire, where JSON is the point.
//!
//! Per the root `CLAUDE.md`, every action key gets two cases: the menu
//! withholds it, and the write refuses it. **No issue action refuses
//! anything** — there is no WIP rule and nothing else about an issue constrains
//! what may be done to it — so for those the honest pair is "always offered"
//! and the refusal `execute` states. Saying so in the test names is better than
//! inventing a rule to satisfy the shape.

use std::borrow::Cow;

use serde_json::{Value, json};

use tt_spike::Error;
use tt_spike::domains::issue::actions as issue_actions;
use tt_spike::domains::issue::{self, Issue, Priority, Status};
use tt_spike::domains::project::actions as project_actions;
use tt_spike::domains::project::{self, Project, Restorable};
use tt_spike::platform::action::{Action, Creator, Offered};
use tt_spike::platform::deleted::Deleted;
use tt_spike::platform::wire;

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

#[test]
fn edit_title_trims_and_refuses_a_blank_title() {
    let got = issue_actions::EditTitle::run(
        issue(),
        issue_actions::EditTitlePayload {
            title: " new ".into(),
        },
    )
    .expect("editTitle should run");
    assert_eq!(got.title, "new");

    assert!(matches!(
        issue_actions::EditTitle::run(
            issue(),
            issue_actions::EditTitlePayload {
                title: "   ".into()
            }
        ),
        Err(Error::Invalid(_))
    ));
}

#[test]
fn edit_body_accepts_the_blank_that_edit_title_refuses() {
    // The same payload shape and a different rule, which is what an action
    // still earns over a form generated from the schema.
    let got = issue_actions::EditBody::run(
        issue(),
        issue_actions::EditBodyPayload {
            body: String::new(),
        },
    )
    .expect("editBody should run");
    assert_eq!(got.body, "");
}

#[test]
fn edit_status_replaces_the_note_it_arrived_with() {
    let noted = issue_actions::EditStatus::run(
        issue(),
        issue_actions::EditStatusPayload {
            status: Status::Doing,
            note: Some("started".into()),
        },
    )
    .expect("editStatus should run");
    assert_eq!(noted.status, Status::Doing);
    assert_eq!(noted.status_note, Some("started".into()));

    // Moving without one clears the old note rather than leaving it to describe
    // a state the issue is no longer in.
    let cleared = issue_actions::EditStatus::run(
        noted,
        issue_actions::EditStatusPayload {
            status: Status::Done,
            note: None,
        },
    )
    .expect("editStatus should run");
    assert_eq!(cleared.status_note, None);
}

#[test]
fn edit_priority_sets_the_priority() {
    let got = issue_actions::EditPriority::run(
        issue(),
        issue_actions::EditPriorityPayload {
            priority: Priority::High,
        },
    )
    .expect("editPriority should run");
    assert_eq!(got.priority, Priority::High);
}

#[test]
fn issue_delete_and_restore_are_the_identity() {
    // What the write is lives entirely in the store call the group is paired
    // with, because the column it sets is not on `Issue` at all.
    let got = issue_actions::Delete::run(issue(), Default::default()).expect("delete should run");
    assert_eq!(got, issue());

    let deleted = Deleted {
        inner: issue(),
        deleted_at: "2026-01-02T00:00:00Z".into(),
    };
    assert_eq!(
        issue_actions::Restore::availability(&deleted),
        Some(Offered::Runnable)
    );
    assert_eq!(
        issue_actions::Restore::run(deleted.clone(), Default::default()),
        Ok(deleted)
    );
}

// --- projects: the half with preconditions --------------------------------

#[test]
fn edit_status_is_refused_while_anything_is_doing() {
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
    // active is refused too.
    assert!(matches!(
        project_actions::EditStatus::run(
            busy,
            project_actions::EditStatusPayload {
                status: project::Status::Active
            }
        ),
        Err(Error::Conflict(_))
    ));
}

#[test]
fn edit_status_is_runnable_once_nothing_is_doing() {
    let quiet = Project {
        todo: 2,
        done: 1,
        ..project()
    };
    assert_eq!(
        project_actions::EditStatus::availability(&quiet),
        Some(Offered::Runnable)
    );
    let archived = project_actions::EditStatus::run(
        quiet,
        project_actions::EditStatusPayload {
            status: project::Status::Archived,
        },
    )
    .expect("editStatus should run");
    assert_eq!(archived.status, project::Status::Archived);
}

#[test]
fn delete_is_refused_while_the_project_is_active() {
    assert_eq!(
        project_actions::Delete::availability(&project()),
        Some(refused("archive it first"))
    );
    assert!(matches!(
        project_actions::Delete::run(project(), Default::default()),
        Err(Error::Conflict(_))
    ));

    let archived = Project {
        status: project::Status::Archived,
        ..project()
    };
    assert_eq!(
        project_actions::Delete::availability(&archived),
        Some(Offered::Runnable)
    );
    assert!(project_actions::Delete::run(archived, Default::default()).is_ok());
}

#[test]
fn add_issue_is_refused_while_the_project_is_archived() {
    let archived = Project {
        status: project::Status::Archived,
        ..project()
    };
    assert_eq!(
        project_actions::AddIssue::availability(&archived),
        Some(refused("project is archived"))
    );
    assert!(matches!(
        project_actions::AddIssue::run(
            &archived,
            project_actions::AddIssuePayload {
                title: "nope".into(),
                body: None,
                priority: None,
            }
        ),
        Err(Error::Conflict(_))
    ));
}

#[test]
fn add_issue_defaults_what_the_payload_leaves_out() {
    let draft = project_actions::AddIssue::run(
        &project(),
        project_actions::AddIssuePayload {
            title: " ship it ".into(),
            body: None,
            priority: None,
        },
    )
    .expect("addIssue should run");
    assert_eq!(
        draft,
        issue::Draft {
            project_id: 1,
            title: "ship it".into(),
            body: String::new(),
            priority: Priority::Normal,
        }
    );
    assert!(matches!(
        project_actions::AddIssue::run(
            &project(),
            project_actions::AddIssuePayload {
                title: "  ".into(),
                body: None,
                priority: None,
            }
        ),
        Err(Error::Invalid(_))
    ));
}

#[test]
fn create_project_refuses_a_slug_the_list_already_holds() {
    // A hook is given the parent and not the payload, so this cannot be an
    // availability hook — the creator is always offered, and `create` is what
    // refuses.
    assert_eq!(
        project_actions::CreateProject::availability(&vec![project()]),
        Some(Offered::Runnable)
    );
    assert!(matches!(
        project_actions::CreateProject::run(
            &vec![project()],
            project_actions::CreateProjectPayload {
                slug: "tt".into(),
                title: None,
                body: None,
            }
        ),
        Err(Error::Conflict(_))
    ));
    assert!(matches!(
        project_actions::CreateProject::run(
            &vec![],
            project_actions::CreateProjectPayload {
                slug: "  ".into(),
                title: None,
                body: None,
            }
        ),
        Err(Error::Invalid(_))
    ));
    assert_eq!(
        project_actions::CreateProject::run(
            &vec![project()],
            project_actions::CreateProjectPayload {
                slug: "other".into(),
                title: Some("another".into()),
                body: None,
            }
        ),
        Ok(project::Draft {
            slug: "other".into(),
            title: "another".into(),
            body: String::new(),
        })
    );
}

#[test]
fn restore_is_refused_once_the_slug_is_taken_again() {
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
    assert!(matches!(
        project_actions::Restore::run(restorable(vec![project()]), Default::default()),
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
    // refusal survives to the menu with its reason attached — which is the
    // whole of what availability buys a frontend.
    assert_eq!(
        keys(&issue_actions::group(), &issue()),
        vec!["editTitle", "editBody", "editStatus", "editPriority"]
    );
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
        ]
    );
}

#[test]
fn dispatch_refuses_what_availability_refused() {
    assert!(matches!(
        wire::dispatch(&project_actions::trash(), project(), "delete", &json!({})),
        Err(Error::Conflict(_))
    ));
}

#[test]
fn the_wire_agrees_with_the_typed_path() {
    assert_eq!(
        wire::dispatch(
            &issue_actions::group(),
            issue(),
            "editTitle",
            &json!({ "title": " new " })
        ),
        issue_actions::EditTitle::run(
            issue(),
            issue_actions::EditTitlePayload {
                title: " new ".into()
            }
        )
    );
    assert_eq!(
        wire::create(
            &project_actions::creators(),
            &project(),
            "addIssue",
            &json!({ "title": "one" })
        ),
        project_actions::AddIssue::run(
            &project(),
            project_actions::AddIssuePayload {
                title: "one".into(),
                body: None,
                priority: None,
            }
        )
    );
}

#[test]
fn a_malformed_payload_is_invalid() {
    for payload in [
        json!({}),                           // missing a required field
        json!({ "title": 5 }),               // wrong type
        json!({ "title": "x", "bogus": 1 }), // not advertised
    ] {
        assert!(matches!(
            wire::dispatch(&issue_actions::group(), issue(), "editTitle", &payload),
            Err(Error::Invalid(_))
        ));
    }
    // A value outside the enum, which is the one the schema could have told the
    // caller about in advance.
    assert!(matches!(
        wire::dispatch(
            &issue_actions::group(),
            issue(),
            "editStatus",
            &json!({ "status": "shipped" })
        ),
        Err(Error::Invalid(_))
    ));
}

#[test]
fn an_action_with_no_arguments_takes_an_empty_object_and_not_null() {
    assert!(wire::dispatch(&issue_actions::trash(), issue(), "delete", &json!({})).is_ok());
    assert!(matches!(
        wire::dispatch(&issue_actions::trash(), issue(), "delete", &Value::Null),
        Err(Error::Invalid(_))
    ));
}

#[test]
fn an_unknown_key_is_invalid() {
    assert!(matches!(
        wire::dispatch(&issue_actions::group(), issue(), "explode", &json!({})),
        Err(Error::Invalid(_))
    ));
    assert!(matches!(
        wire::create(&project_actions::root(), &vec![], "explode", &json!({})),
        Err(Error::Invalid(_))
    ));
}

#[test]
fn one_key_in_two_groups_decodes_against_the_group_it_was_dispatched_on() {
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

    // A project status through the issue's `editStatus`.
    assert!(matches!(
        wire::dispatch(
            &issue_actions::group(),
            issue(),
            "editStatus",
            &json!({ "status": "archived" })
        ),
        Err(Error::Invalid(_))
    ));
    // An issue status, and an issue-only field, through the project's.
    assert!(matches!(
        wire::dispatch(
            &project_actions::group(),
            project(),
            "editStatus",
            &json!({ "status": "doing" })
        ),
        Err(Error::Invalid(_))
    ));
    assert!(matches!(
        wire::dispatch(
            &project_actions::group(),
            project(),
            "editStatus",
            &json!({ "status": "active", "note": "why" })
        ),
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
    let schema = project_actions::creators()
        .into_iter()
        .find(|entry| entry.key == "addIssue")
        .expect("addIssue should be registered")
        .schema;
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
    let schema = schema_of(&issue_actions::trash(), "delete");
    assert_eq!(schema["type"], json!("object"));
    assert_eq!(schema["additionalProperties"], json!(false));
    assert!(schema.get("properties").is_none());
    assert!(schema.get("required").is_none());
}

#[test]
fn the_advertised_schema_accepts_what_the_decoder_accepts() {
    // `serde` and `schemars` read the same attributes, so the schema saying
    // `note` may be null and the decoder accepting null are one decision. The
    // OCaml side asserts the opposite, because its two ppxes are two programs.
    assert!(
        wire::dispatch(
            &issue_actions::group(),
            issue(),
            "editStatus",
            &json!({ "status": "doing", "note": null })
        )
        .is_ok()
    );
    assert!(
        wire::dispatch(
            &issue_actions::group(),
            issue(),
            "editStatus",
            &json!({ "status": "doing" })
        )
        .is_ok()
    );
}
