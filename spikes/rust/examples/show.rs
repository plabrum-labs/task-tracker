//! What the erased path looks like from outside: read the object, see what it
//! offers, pick one, supply arguments. A TUI user and an agent do the same
//! thing, so this is roughly `tt project show tt` followed by
//! `tt project action tt addIssue '{…}'`.
//!
//! Against a throwaway in-memory database, so it prints the same thing every
//! time it is run. The action layer needs the connection now — an `execute`
//! writes for itself and returns a message — so this drives the real path
//! rather than a set of literals.

use serde_json::json;

use tt_spike::domains::issue::actions as issue_actions;
use tt_spike::domains::issue::services as issue_services;
use tt_spike::domains::project::actions as project_actions;
use tt_spike::domains::project::services as project_services;
use tt_spike::domains::schema;
use tt_spike::platform::action::Offered;
use tt_spike::platform::db;
use tt_spike::platform::error::Error;
use tt_spike::platform::wire;

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

async fn program() -> Result<(), Error> {
    let db = db::connect("sqlite::memory:").await?;
    schema::initialise(&db).await?;

    // No projects yet: the root creator, offered against the empty list it is
    // checked against.
    let projects = project_services::projects(&db).await?;
    print("no projects yet", &project_actions::root(), &projects);

    // Make one, then add an issue under it — each a real dispatch inside its own
    // transaction, exactly as a frontend would run it.
    db::transaction(&db, async |tx| {
        let projects = project_services::projects(tx).await?;
        wire::dispatch(
            &project_actions::root(),
            projects,
            "createProject",
            &json!({ "slug": "tt", "title": "task tracker" }),
            tx,
        )
        .await
    })
    .await?;

    db::transaction(&db, async |tx| {
        let project = project_services::project(tx, "tt")
            .await?
            .ok_or_else(|| Error::Invalid("the project we just made".into()))?;
        wire::dispatch(
            &project_actions::group(),
            project,
            "addIssue",
            &json!({ "title": "ship the mvp", "priority": "high" }),
            tx,
        )
        .await
    })
    .await?;

    let project = project_services::project(&db, "tt")
        .await?
        .ok_or_else(|| Error::Invalid("the project".into()))?;
    let issues = issue_services::issues(&db, "tt").await?;
    print(
        "the project, with one issue to do",
        &project_actions::group(),
        &project,
    );
    for issue in &issues {
        print("the issue", &issue_actions::group(), issue);
    }
    Ok(())
}

#[tokio::main]
async fn main() {
    if let Err(e) = program().await {
        println!("{e}");
    }
}
