//! What the erased path looks like from outside: read the object, see what it
//! offers, pick one, supply arguments. A TUI user and an agent do the same
//! thing, so this is roughly `tt issue show` followed by
//! `tt issue action <id> <key> <json>`.
//!
//! Against literals rather than a database, because the action layer needs
//! neither. The refusals below are what the objects say about themselves.

use serde_json::json;

use tt_spike::action::Offered;
use tt_spike::issue::{self, Issue};
use tt_spike::project::{self, Project};
use tt_spike::{issue_actions, project_actions, wire};

fn print<O>(title: &str, group: &[wire::Entry<O>], obj: &O) {
    println!("--- {title}");
    for (entry, offered) in wire::available(group, obj) {
        let state = match &offered {
            Offered::Runnable => "runnable".to_string(),
            Offered::Refused(reason) => format!("refused: {reason}"),
        };
        println!("{:?} ({state})", entry.key);
        println!(
            "{}",
            serde_json::to_string_pretty(&entry.schema).unwrap_or_default()
        );
    }
}

fn main() {
    let issue = Issue {
        id: 7,
        project_id: 1,
        project_slug: "tt".into(),
        title: "the title".into(),
        body: String::new(),
        status: issue::Status::Todo,
        priority: issue::Priority::Normal,
        status_note: None,
        created_at: "2026-01-01T00:00:00Z".into(),
        updated_at: "2026-01-01T00:00:00Z".into(),
    };

    // One issue `doing`, so `editStatus` and `delete` come back refused with
    // their reasons — a menu built from availability shows what a fixed list of
    // commands cannot.
    let project = Project {
        id: 1,
        slug: "tt".into(),
        title: "task tracker".into(),
        body: String::new(),
        status: project::Status::Active,
        todo: 2,
        doing: 1,
        done: 0,
        created_at: "2026-01-01T00:00:00Z".into(),
        updated_at: "2026-01-01T00:00:00Z".into(),
    };

    print("issue", &issue_actions::group(), &issue);
    print("project", &project_actions::group(), &project);
    print("project trash", &project_actions::trash(), &project);

    println!("--- one dispatch");
    match wire::dispatch(
        &issue_actions::group(),
        issue,
        "editStatus",
        &json!({ "status": "doing", "note": "started" }),
    ) {
        Ok(updated) => println!(
            "=> {} is {} ({:?})",
            updated.subject(),
            wire::name_of(&updated.status),
            updated.status_note
        ),
        Err(e) => println!("=> {e}"),
    }

    println!("--- one creation");
    match wire::create(
        &project_actions::root(),
        &vec![project],
        "createProject",
        &json!({ "slug": "tt" }),
    ) {
        Ok(draft) => println!("=> {draft:?}"),
        Err(e) => println!("=> {e}"),
    }
}
