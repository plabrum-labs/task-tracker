//! The two frontends, driven with no terminal and no pty.
//!
//! `form::of_schema` and `tui::on_key` are pure, `cli::command` reads no
//! database, and `tui::render` draws into a buffer — so the whole of both
//! frontends is assertable without a person, a sleep or a mock. That is what
//! `tui.rs` splitting the key handler from the write is for.

use crossterm::event::KeyCode;
use ratatui::Terminal;
use ratatui::backend::TestBackend;
use serde_json::{Value, json};

use tt_spike::form::{self, Field, Kind};
use tt_spike::issue::{self, Priority};
use tt_spike::project;
use tt_spike::store::{self, Db};
use tt_spike::tui::{Control, Intent, Row, Screen, State};
use tt_spike::{cli, issue_actions, project_actions, tui};

fn schema(key: &str) -> Value {
    issue_actions::group()
        .into_iter()
        .chain(issue_actions::trash())
        .find(|entry| entry.key == key)
        .expect("action should be registered")
        .schema
}

// --- the form -------------------------------------------------------------

#[test]
fn an_enum_field_becomes_a_selector_over_the_values_the_schema_advertises() {
    let fields = form::of_schema(&schema("editStatus")).expect("editStatus should render");
    assert_eq!(
        fields,
        vec![
            Field {
                name: "status".into(),
                required: true,
                kind: Kind::Enum(vec!["todo".into(), "doing".into(), "done".into()]),
                description: Some("Where the issue is up to.".into()),
            },
            Field {
                name: "note".into(),
                required: false,
                kind: Kind::OptionalText,
                description: Some("What to record about the move.".into()),
            },
        ]
    );
}

#[test]
fn the_fields_are_in_the_order_the_payload_type_declares_them() {
    // Alphabetically this would be body, priority, title. It is not, because
    // `serde_json` is built with `preserve_order`.
    let schema = project_actions::creators()
        .into_iter()
        .find(|entry| entry.key == "addIssue")
        .expect("addIssue should be registered")
        .schema;
    let names: Vec<String> = form::of_schema(&schema)
        .expect("addIssue should render")
        .into_iter()
        .map(|field| field.name)
        .collect();
    assert_eq!(names, vec!["title", "body", "priority"]);
}

#[test]
fn an_action_with_no_arguments_renders_a_form_with_no_fields() {
    assert_eq!(form::of_schema(&schema("delete")), Ok(Vec::new()));
}

#[test]
fn a_schema_the_form_cannot_render_fails_the_whole_form() {
    // Dropping the field would render a form that cannot express the action,
    // and submitting it would produce a payload the decoder refuses for a
    // reason nothing on screen mentions.
    let unrenderable = json!({
        "type": "object",
        "properties": { "count": { "type": "integer" } },
        "required": ["count"]
    });
    assert!(form::of_schema(&unrenderable).is_err());
    assert!(form::of_schema(&json!({ "type": "null" })).is_err());
}

#[test]
fn a_blank_optional_field_submits_the_null_its_schema_advertises() {
    let fields = form::of_schema(&schema("editStatus")).expect("editStatus should render");
    let values = vec![
        (fields[0].clone(), "doing".to_string()),
        (fields[1].clone(), String::new()),
    ];
    let payload = form::payload(&values);
    assert_eq!(payload, json!({ "status": "doing", "note": null }));

    // And the decoder accepts exactly that, which is the assertion the OCaml
    // side cannot make: there, `[@jsonschema.option]` advertises the null that
    // `[@yojson.option]` refuses, so the only encoding both halves accept is to
    // omit the field.
    let issue =
        tt_spike::wire::dispatch(&issue_actions::group(), an_issue(), "editStatus", &payload)
            .expect("the schema's own null should decode");
    assert_eq!(issue.status_note, None);
}

#[test]
fn a_blank_required_text_field_is_submitted_and_refused_by_the_action() {
    // The form does not second-guess `execute`; a form that did would be a
    // second place that has to agree.
    let fields = form::of_schema(&schema("editTitle")).expect("editTitle should render");
    let payload = form::payload(&[(fields[0].clone(), String::new())]);
    assert_eq!(payload, json!({ "title": "" }));
    assert!(
        tt_spike::wire::dispatch(&issue_actions::group(), an_issue(), "editTitle", &payload)
            .is_err()
    );
}

fn an_issue() -> issue::Issue {
    issue::Issue {
        id: 1,
        project_id: 1,
        project_slug: "tt".into(),
        title: "a title".into(),
        body: String::new(),
        status: issue::Status::Todo,
        priority: Priority::Normal,
        status_note: None,
        created_at: "2026-01-01T00:00:00Z".into(),
        updated_at: "2026-01-01T00:00:00Z".into(),
    }
}

// --- the TUI --------------------------------------------------------------

async fn seeded() -> Db {
    let db = store::connect("sqlite::memory:").await.expect("connect");
    store::initialise(&db).await.expect("DDL");
    let tt = store::create_project(
        &db,
        project::Draft {
            slug: "tt".into(),
            title: "task tracker".into(),
            body: String::new(),
        },
    )
    .await
    .expect("create_project");
    store::create_issue(
        &db,
        issue::Draft {
            project_id: tt.id,
            title: "ship the mvp".into(),
            body: String::new(),
            priority: Priority::High,
        },
    )
    .await
    .expect("create_issue");
    db
}

fn labels(state: &State) -> Vec<String> {
    state.rows.iter().map(Row::label).collect()
}

/// What is actually on screen, as lines of text.
fn drawn(state: &State) -> Vec<String> {
    let mut terminal =
        Terminal::new(TestBackend::new(90, 16)).expect("a test backend needs no terminal");
    terminal
        .draw(|frame| tui::render(state, frame))
        .expect("render should not fail");
    terminal
        .backend()
        .buffer()
        .content()
        .chunks(90)
        .map(|row| {
            row.iter()
                .map(|cell| cell.symbol())
                .collect::<String>()
                .trim_end()
                .to_string()
        })
        .filter(|line| !line.is_empty())
        .collect()
}

async fn press(db: &Db, state: State, key: KeyCode) -> State {
    let intent = tui::on_key(&state, key);
    tui::apply(db, state, intent).await
}

#[tokio::test]
async fn the_first_screen_offers_the_root_creator_and_the_projects_under_it() {
    let db = seeded().await;
    let state = tui::start(&db).await;
    assert_eq!(state.screen, Screen::Projects);
    let labels = labels(&state);
    assert_eq!(labels[0], "createProject");
    assert!(labels[1].starts_with("tt"), "{labels:?}");
    assert!(
        drawn(&state)
            .iter()
            .any(|line| line.contains("> createProject"))
    );
}

#[tokio::test]
async fn a_key_means_the_same_thing_whether_or_not_anything_runs() {
    // `on_key` is pure, so this is the whole of what it decides.
    let db = seeded().await;
    let state = tui::start(&db).await;
    assert_eq!(tui::on_key(&state, KeyCode::Down), Intent::Move(1));
    assert_eq!(tui::on_key(&state, KeyCode::Up), Intent::Move(-1));
    assert_eq!(tui::on_key(&state, KeyCode::Enter), Intent::Enter);
    assert_eq!(tui::on_key(&state, KeyCode::Char('q')), Intent::Quit);
    assert_eq!(tui::on_key(&state, KeyCode::Char('z')), Intent::Ignored);

    // The same keys mean different things once a form is up.
    let filling = press(&db, state, KeyCode::Enter).await;
    assert!(filling.form.is_some());
    assert_eq!(
        tui::on_key(&filling, KeyCode::Char('q')),
        Intent::Insert('q')
    );
    assert_eq!(tui::on_key(&filling, KeyCode::Right), Intent::Cycle(1));
    assert_eq!(tui::on_key(&filling, KeyCode::Enter), Intent::Submit);
    assert_eq!(tui::on_key(&filling, KeyCode::Esc), Intent::Back);
}

#[tokio::test]
async fn the_three_screens_are_reached_by_moving_and_pressing_enter() {
    let db = seeded().await;
    let mut state = tui::start(&db).await;

    // Down past `createProject` onto the project, then in.
    state = press(&db, state, KeyCode::Down).await;
    state = press(&db, state, KeyCode::Enter).await;
    assert_eq!(state.screen, Screen::Issues("tt".into()));
    assert!(state.header.contains("project tt"));
    let rows = labels(&state);
    assert_eq!(
        rows[..4],
        [
            "editTitle".to_string(),
            "editBody".to_string(),
            "editStatus".to_string(),
            "delete (archive it first)".to_string(),
        ]
    );
    // A refusal is rendered rather than obeyed, and it says why.
    assert!(
        drawn(&state)
            .iter()
            .any(|l| l.contains("delete (archive it first)"))
    );

    // Past the four actions and the creator onto the issue, then in.
    for _ in 0..5 {
        state = press(&db, state, KeyCode::Down).await;
    }
    state = press(&db, state, KeyCode::Enter).await;
    assert_eq!(
        state.screen,
        Screen::Detail {
            project: "tt".into(),
            id: 1
        }
    );
    assert!(state.header.contains("ship the mvp"));
    assert_eq!(
        labels(&state),
        vec![
            "editTitle",
            "editBody",
            "editStatus",
            "editPriority",
            "delete"
        ]
    );

    // Escape walks back out the way it came in.
    state = press(&db, state, KeyCode::Esc).await;
    assert_eq!(state.screen, Screen::Issues("tt".into()));
    state = press(&db, state, KeyCode::Esc).await;
    assert_eq!(state.screen, Screen::Projects);
    state = press(&db, state, KeyCode::Esc).await;
    assert_eq!(state.screen, Screen::Projects);
}

#[tokio::test]
async fn an_enum_field_is_a_cycling_selector_and_a_text_field_is_typed_into() {
    let db = seeded().await;
    let mut state = tui::start(&db).await;
    for _ in 0..2 {
        state = press(&db, state, KeyCode::Down).await;
    }
    state = press(&db, state, KeyCode::Enter).await; // into the project
    for _ in 0..5 {
        state = press(&db, state, KeyCode::Down).await;
    }
    state = press(&db, state, KeyCode::Enter).await; // into the issue
    for _ in 0..2 {
        state = press(&db, state, KeyCode::Down).await;
    }
    state = press(&db, state, KeyCode::Enter).await; // editStatus

    let form = state.form.clone().expect("editStatus should open a form");
    assert_eq!(form.key, "editStatus");
    assert_eq!(
        form.entries[0].control,
        Control::Choosing {
            values: vec!["todo".into(), "doing".into(), "done".into()],
            index: 0
        }
    );
    // The label is the doc comment, not the property name.
    assert!(
        drawn(&state)
            .iter()
            .any(|line| line.contains("Where the issue is up to.")),
        "{:?}",
        drawn(&state)
    );

    state = press(&db, state, KeyCode::Right).await;
    assert_eq!(
        state.form.as_ref().expect("form").entries[0]
            .control
            .value(),
        "doing"
    );
    // It wraps, so a selector never has a blank state to mean "absent".
    for _ in 0..2 {
        state = press(&db, state, KeyCode::Right).await;
    }
    assert_eq!(
        state.form.as_ref().expect("form").entries[0]
            .control
            .value(),
        "todo"
    );
    state = press(&db, state, KeyCode::Right).await;

    // Tab to the note, type into it, rub a character out.
    state = press(&db, state, KeyCode::Tab).await;
    for c in "started!".chars() {
        state = press(&db, state, KeyCode::Char(c)).await;
    }
    state = press(&db, state, KeyCode::Backspace).await;
    assert_eq!(
        state.form.as_ref().expect("form").entries[1]
            .control
            .value(),
        "started"
    );

    state = press(&db, state, KeyCode::Enter).await;
    assert!(state.form.is_none(), "a successful write closes the form");
    let issue = store::issue(&db, 1)
        .await
        .expect("issue should load")
        .expect("issue should be there");
    assert_eq!(issue.status, issue::Status::Doing);
    assert_eq!(issue.status_note, Some("started".into()));
}

#[tokio::test]
async fn a_refusal_leaves_the_form_up_with_its_values_intact() {
    let db = seeded().await;
    let mut state = tui::start(&db).await;
    state = press(&db, state, KeyCode::Enter).await; // createProject

    for c in "tt".chars() {
        state = press(&db, state, KeyCode::Char(c)).await;
    }
    state = press(&db, state, KeyCode::Enter).await;

    assert!(state.form.is_some(), "the form should still be up");
    assert_eq!(
        state.form.as_ref().expect("form").entries[0]
            .control
            .value(),
        "tt"
    );
    assert!(state.status.contains("already exists"), "{}", state.status);
}

#[tokio::test]
async fn deleting_what_the_screen_is_about_falls_out_to_the_parent() {
    // Nothing here names `delete`. The screen is reloaded after every write and
    // the fall-out is what happens when the object it was about has gone.
    let db = seeded().await;
    let tt = store::project(&db, "tt")
        .await
        .expect("project")
        .expect("project should be there");
    let issue = store::issues(&db, "tt").await.expect("issues")[0].clone();
    store::delete_issue(&db, &issue).await.expect("delete");
    store::update_project(
        &db,
        &project::Project {
            status: project::Status::Archived,
            ..tt
        },
    )
    .await
    .expect("update");

    let mut state = tui::start(&db).await;
    state = press(&db, state, KeyCode::Down).await;
    state = press(&db, state, KeyCode::Enter).await;
    assert_eq!(state.screen, Screen::Issues("tt".into()));

    // Down to `delete`, which is runnable now the project is archived.
    state = press(&db, state, KeyCode::Down).await;
    state = press(&db, state, KeyCode::Down).await;
    state = press(&db, state, KeyCode::Down).await;
    assert!(matches!(
        state.selected_row(),
        Some(Row::Do { key, .. }) if key == "delete"
    ));
    state = press(&db, state, KeyCode::Enter).await; // the form has no fields
    state = press(&db, state, KeyCode::Enter).await; // submit it

    assert_eq!(state.screen, Screen::Projects);
    assert!(store::project(&db, "tt").await.expect("project").is_none());
}

// --- the CLI --------------------------------------------------------------

fn parse(argv: &[&str]) -> clap::ArgMatches {
    cli::command()
        .try_get_matches_from(argv)
        .unwrap_or_else(|e| panic!("{argv:?} should parse: {e}"))
}

#[test]
fn every_registered_action_has_a_subcommand_and_nothing_else_does() {
    let subcommands = |name: &str| -> Vec<String> {
        cli::command()
            .find_subcommand(name)
            .expect("subcommand should exist")
            .get_subcommands()
            .map(|c| c.get_name().to_string())
            .collect()
    };
    assert_eq!(
        subcommands("issue"),
        vec![
            "ls",
            "show",
            "action",
            "editTitle",
            "editBody",
            "editStatus",
            "editPriority",
            "delete",
            "restore",
        ]
    );
    assert_eq!(
        subcommands("project"),
        vec![
            "ls",
            "show",
            "action",
            "editTitle",
            "editBody",
            "editStatus",
            "delete",
            "addIssue",
            "restore",
        ]
    );
}

#[test]
fn a_required_field_is_a_required_option_and_an_enum_is_a_closed_one() {
    // `--status` comes from the schema, so a missing one is a usage error
    // rather than a payload the decoder refuses further in.
    assert!(
        cli::command()
            .try_get_matches_from(["tt", "issue", "editStatus", "1"])
            .is_err()
    );
    // And a value outside the enum is a usage error listing the alternatives.
    assert!(
        cli::command()
            .try_get_matches_from(["tt", "issue", "editStatus", "1", "--status", "shipped"])
            .is_err()
    );
    // A blank title still gets past the parser, because what a title has to
    // contain is the action's business.
    assert!(
        cli::command()
            .try_get_matches_from(["tt", "issue", "editTitle", "1", "--title", ""])
            .is_ok()
    );
    // An option the schema does not advertise is rejected.
    assert!(
        cli::command()
            .try_get_matches_from(["tt", "issue", "editTitle", "1", "--bogus", "x"])
            .is_err()
    );
}

#[test]
fn the_help_text_is_the_payload_field_doc_comment() {
    let help = cli::command()
        .find_subcommand_mut("issue")
        .expect("issue should exist")
        .find_subcommand_mut("editStatus")
        .expect("editStatus should exist")
        .render_help()
        .to_string();
    assert!(help.contains("Where the issue is up to."), "{help}");
    assert!(
        help.contains("[possible values: todo, doing, done]"),
        "{help}"
    );
}

#[tokio::test]
async fn the_options_and_the_blob_reach_the_same_write() {
    let db = seeded().await;
    let spelled = cli::run(
        &db,
        &parse(&["tt", "issue", "editStatus", "1", "--status", "doing"]),
    )
    .await
    .expect("editStatus should run");

    let blob = cli::run(
        &db,
        &parse(&[
            "tt",
            "issue",
            "action",
            "1",
            "editStatus",
            r#"{"status":"doing"}"#,
        ]),
    )
    .await
    .expect("editStatus should run");

    assert_eq!(spelled, blob);
}

#[tokio::test]
async fn a_refusal_comes_back_from_the_live_row_and_not_from_the_parser() {
    let db = seeded().await;
    // `delete` is a subcommand on an active project, because a command tree is
    // built before any row has been read.
    let matches = parse(&["tt", "project", "delete", "tt"]);
    let refusal = cli::run(&db, &matches)
        .await
        .expect_err("should be refused");
    assert!(matches!(refusal, tt_spike::Error::Conflict(_)), "{refusal}");
    assert!(refusal.to_string().contains("archive it first"));
}

#[tokio::test]
async fn show_hands_an_agent_the_offers_and_their_schemas() {
    let db = seeded().await;
    let output = cli::run(&db, &parse(&["tt", "issue", "show", "1"]))
        .await
        .expect("show should run");
    let value: Value = serde_json::from_str(&output).expect("show should emit JSON");
    assert_eq!(value["issue"]["project"], json!("tt"));
    assert_eq!(value["issue"]["priority"], json!("high"));

    let actions = value["actions"]
        .as_array()
        .expect("actions should be a list");
    assert_eq!(
        actions.iter().map(|a| a["key"].clone()).collect::<Vec<_>>(),
        vec![
            json!("editTitle"),
            json!("editBody"),
            json!("editStatus"),
            json!("editPriority"),
            json!("delete"),
        ]
    );
    // The schema is passed through exactly as it was derived, descriptions and
    // all — this is the payload of `tt issue show | jq '.actions[0].arguments'`.
    assert_eq!(
        actions[0]["arguments"]["properties"]["title"]["description"],
        json!("What to call the issue.")
    );
}

#[tokio::test]
async fn an_unknown_object_is_invalid_rather_than_a_crash() {
    let db = seeded().await;
    assert!(matches!(
        cli::run(&db, &parse(&["tt", "issue", "show", "99"])).await,
        Err(tt_spike::Error::Invalid(_))
    ));
    assert!(matches!(
        cli::run(&db, &parse(&["tt", "project", "show", "nope"])).await,
        Err(tt_spike::Error::Invalid(_))
    ));
    assert!(matches!(
        cli::run(
            &db,
            &parse(&["tt", "issue", "action", "1", "editTitle", "not json"])
        )
        .await,
        Err(tt_spike::Error::Invalid(_))
    ));
}
