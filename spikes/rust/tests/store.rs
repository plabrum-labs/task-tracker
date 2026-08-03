//! The SQL edge, against a real in-memory SQLite database.
//!
//! No mocks, no `MockDatabase`, and no seam invented for one: `initialise` runs
//! the same DDL the binary runs and every query below goes to sqlite. sqlx
//! opens `sqlite::memory:` shared-cache and sea-orm caps a SQLite pool at one
//! connection, so one `connect` is one database and one fixture — the tests
//! cannot see each other's rows.
//!
//! The soft-delete cases are the point. Liveness is derived rather than stored,
//! so what has to be shown is that one row written on the way out is one row
//! cleared on the way back, and that nothing else changed in between.

use tt_spike::domains::issue::services as issue_services;
use tt_spike::domains::issue::{self, Priority, Status};
use tt_spike::domains::project::services as project_services;
use tt_spike::domains::project::{self, Project};
use tt_spike::domains::schema;
use tt_spike::platform::db::{self, Db};

async fn fixture() -> Db {
    let db = db::connect("sqlite::memory:")
        .await
        .expect("connect should succeed");
    schema::initialise(&db).await.expect("DDL should run");
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
    .expect("create_project should succeed")
}

async fn an_issue(db: &Db, project: &Project, title: &str, priority: Priority) -> issue::Issue {
    issue_services::create_issue(
        db,
        issue::Draft {
            project_id: project.id,
            title: title.into(),
            body: String::new(),
            priority,
        },
    )
    .await
    .expect("create_issue should succeed")
}

fn titles(issues: &[issue::Issue]) -> Vec<&str> {
    issues.iter().map(|i| i.title.as_str()).collect()
}

// --- creation -------------------------------------------------------------

#[tokio::test]
async fn create_returns_the_stored_row_with_its_assigned_id() {
    let db = fixture().await;
    let tt = a_project(&db, "tt").await;
    assert!(tt.id > 0);
    assert_eq!(tt.status, project::Status::Active);
    assert_eq!((tt.todo, tt.doing, tt.done), (0, 0, 0));
    // One instant in both stamps, because a write takes the clock once.
    assert_eq!(tt.created_at, tt.updated_at);

    let other = a_project(&db, "other").await;
    assert_ne!(other.id, tt.id);

    let issue = an_issue(&db, &tt, "first", Priority::High).await;
    assert!(issue.id > 0);
    assert_eq!(issue.status, Status::Todo);
    // The projection, not the draft: `project_slug` is not a column, it comes
    // from the join.
    assert_eq!(issue.project_slug, "tt");
    assert_eq!(issue.created_at, issue.updated_at);
}

#[tokio::test]
async fn counts_are_part_of_the_projection() {
    let db = fixture().await;
    let tt = a_project(&db, "tt").await;
    for title in ["a", "b", "c"] {
        an_issue(&db, &tt, title, Priority::Normal).await;
    }
    let issues = issue_services::issues(&db, "tt")
        .await
        .expect("issues should load");
    let doing = issue::Issue {
        status: Status::Doing,
        ..issues[0].clone()
    };
    issue_services::update_issue(&db, &doing)
        .await
        .expect("update");

    let tt = project_services::project(&db, "tt")
        .await
        .expect("project should load")
        .expect("project should be there");
    assert_eq!((tt.todo, tt.doing, tt.done), (2, 1, 0));
    assert_eq!(tt.issue_count(), 3);

    // A project with nothing under it still gets zeroes rather than nothing.
    let empty = a_project(&db, "empty").await;
    let empty = project_services::project(&db, &empty.slug)
        .await
        .expect("project should load")
        .expect("project should be there");
    assert_eq!((empty.todo, empty.doing, empty.done), (0, 0, 0));
}

// --- ordering -------------------------------------------------------------

#[tokio::test]
async fn issues_come_back_high_priority_first_then_oldest_first() {
    let db = fixture().await;
    let tt = a_project(&db, "tt").await;
    // Inserted normal, high, high — so neither insertion order nor priority
    // alone produces the expected order.
    for (title, priority) in [
        ("first", Priority::Normal),
        ("second", Priority::High),
        ("third", Priority::High),
    ] {
        an_issue(&db, &tt, title, priority).await;
    }
    let issues = issue_services::issues(&db, "tt")
        .await
        .expect("issues should load");
    assert_eq!(titles(&issues), vec!["second", "third", "first"]);
}

// --- updates --------------------------------------------------------------

#[tokio::test]
async fn an_update_writes_the_editable_columns_and_leaves_the_rest() {
    let db = fixture().await;
    let tt = a_project(&db, "tt").await;
    let issue = an_issue(&db, &tt, "first", Priority::Normal).await;

    let edited = issue::Issue {
        title: "renamed".into(),
        body: "why".into(),
        status: Status::Doing,
        priority: Priority::High,
        status_note: Some("started".into()),
        ..issue.clone()
    };
    issue_services::update_issue(&db, &edited)
        .await
        .expect("update");

    let read = issue_services::issue(&db, issue.id)
        .await
        .expect("issue should load")
        .expect("issue should be there");
    assert_eq!(read.title, "renamed");
    assert_eq!(read.status_note, Some("started".into()));
    assert_eq!(read.created_at, issue.created_at);
    assert_eq!(read.project_id, issue.project_id);

    // A nullable column has one write path: `Set(None)` clears it.
    let cleared = issue::Issue {
        status_note: None,
        ..read
    };
    issue_services::update_issue(&db, &cleared)
        .await
        .expect("update");
    assert_eq!(
        issue_services::issue(&db, issue.id)
            .await
            .expect("issue should load")
            .expect("issue should be there")
            .status_note,
        None
    );
}

// --- soft deletes ---------------------------------------------------------

#[tokio::test]
async fn deleting_a_project_hides_its_issues_and_restoring_brings_them_back() {
    let db = fixture().await;
    let tt = a_project(&db, "tt").await;
    an_issue(&db, &tt, "kept", Priority::Normal).await;
    let doomed = an_issue(&db, &tt, "doomed", Priority::Normal).await;

    // One issue deleted in its own right, before the project goes.
    issue_services::delete_issue(&db, &doomed)
        .await
        .expect("delete");
    assert_eq!(
        titles(&issue_services::issues(&db, "tt").await.expect("issues")),
        vec!["kept"]
    );

    project_services::delete_project(&db, &tt)
        .await
        .expect("delete");
    assert!(
        project_services::project(&db, "tt")
            .await
            .expect("project")
            .is_none()
    );
    assert!(
        issue_services::issues(&db, "tt")
            .await
            .expect("issues")
            .is_empty()
    );
    assert!(
        issue_services::issue(&db, doomed.id)
            .await
            .expect("issue")
            .is_none()
    );

    // The trash is by the row's own `deleted_at`, so the hidden issue is not in
    // it — one row was written on the way out, not two.
    let trashed_projects = project_services::trashed_projects(&db)
        .await
        .expect("trash");
    assert_eq!(trashed_projects.len(), 1);
    assert_eq!(trashed_projects[0].inner.slug, "tt");
    assert_eq!(
        issue_services::trashed_issues(&db)
            .await
            .expect("trash")
            .iter()
            .map(|d| d.inner.title.as_str())
            .collect::<Vec<_>>(),
        vec!["doomed"]
    );

    project_services::restore_project(&db, &trashed_projects[0])
        .await
        .expect("restore");

    // Exactly the issues that were not deleted in their own right.
    assert_eq!(
        titles(&issue_services::issues(&db, "tt").await.expect("issues")),
        vec!["kept"]
    );
    assert_eq!(
        issue_services::trashed_issues(&db)
            .await
            .expect("trash")
            .len(),
        1
    );
}

#[tokio::test]
async fn an_issue_can_be_restored_on_its_own() {
    let db = fixture().await;
    let tt = a_project(&db, "tt").await;
    let issue = an_issue(&db, &tt, "back", Priority::Normal).await;
    issue_services::delete_issue(&db, &issue)
        .await
        .expect("delete");

    let trashed = issue_services::trashed_issues(&db).await.expect("trash");
    assert_eq!(trashed.len(), 1);
    // The trash row still knows its project, because that read asks nothing of
    // the project but its slug.
    assert_eq!(trashed[0].inner.project_slug, "tt");

    issue_services::restore_issue(&db, &trashed[0])
        .await
        .expect("restore");
    assert_eq!(
        titles(&issue_services::issues(&db, "tt").await.expect("issues")),
        vec!["back"]
    );
    assert!(
        issue_services::trashed_issues(&db)
            .await
            .expect("trash")
            .is_empty()
    );
}

#[tokio::test]
async fn a_slug_is_reusable_once_its_project_is_deleted() {
    let db = fixture().await;
    let first = a_project(&db, "tt").await;
    project_services::delete_project(&db, &first)
        .await
        .expect("delete");

    let second = a_project(&db, "tt").await;
    assert_ne!(second.id, first.id);
    assert_eq!(
        project_services::projects(&db)
            .await
            .expect("projects")
            .iter()
            .map(|p| p.slug.as_str())
            .collect::<Vec<_>>(),
        vec!["tt"]
    );
}

// --- the two assertions the OCaml side cannot make ------------------------

#[tokio::test]
async fn the_emitted_schema_is_the_designed_one() {
    let db = fixture().await;
    let ddl = db::emitted_ddl(&db).await.expect("sqlite_master");
    let all = ddl.join("\n");

    // `sqlite_master` keeps the statement as written apart from `IF NOT
    // EXISTS`, which it drops.
    for table in ["CREATE TABLE projects", "CREATE TABLE issues"] {
        assert!(all.contains(table), "missing {table} in:\n{all}");
    }
    assert_eq!(
        all.matches("STRICT").count(),
        2,
        "both tables STRICT:\n{all}"
    );
    assert!(all.contains("CHECK (status IN ('active', 'archived'))"));
    assert!(all.contains("CHECK (status IN ('todo', 'doing', 'done'))"));
    assert!(all.contains("CHECK (priority IN (0, 1))"));
    assert!(all.contains("projects_slug_live"));
    assert!(all.contains("issues_by_project"));
    assert!(all.contains("ON DELETE CASCADE"));

    // The entity structs declare the columns and the DDL declares the table, so
    // this is the assertion that the two describe one thing. `sea-orm`'s own
    // `Schema::create_table_from_entity` could emit none of the above, which is
    // why the DDL is written out rather than generated.
    for (table, columns) in schema::declared_columns() {
        let statement = ddl
            .iter()
            .find(|sql| sql.contains(&format!("CREATE TABLE {table}")))
            .unwrap_or_else(|| panic!("no CREATE TABLE for {table}"));
        for column in columns {
            assert!(
                statement.contains(&format!("\n        {column} ")),
                "{table}.{column} is declared by the entity and not by the DDL:\n{statement}"
            );
        }
    }
}

#[tokio::test]
async fn a_duplicate_live_slug_is_refused_by_the_constraint() {
    // `createProject`'s hook is what turns this into a sentence; the partial
    // unique index is what guarantees it. This bypasses the hook entirely, so
    // what answers is the database.
    let db = fixture().await;
    a_project(&db, "tt").await;
    let again = project_services::create_project(
        &db,
        project::Draft {
            slug: "tt".into(),
            title: String::new(),
            body: String::new(),
        },
    )
    .await;
    assert!(again.is_err(), "the index should have refused it");
}

#[tokio::test]
async fn the_enum_round_trips_through_the_column_it_is_stored_in() {
    // `DeriveActiveEnum`'s `string_value` is a second table of strings beside
    // serde's `rename_all`, and the type system relates them not at all. This
    // is the only thing that says they agree: the values go through a column
    // whose CHECK constraint spells the wire names out, and come back as the
    // same variants.
    let db = fixture().await;
    let tt = a_project(&db, "tt").await;
    for (title, status, priority) in [
        ("a", Status::Todo, Priority::Normal),
        ("b", Status::Doing, Priority::High),
        ("c", Status::Done, Priority::Normal),
    ] {
        let issue = an_issue(&db, &tt, title, priority).await;
        let moved = issue::Issue { status, ..issue };
        issue_services::update_issue(&db, &moved)
            .await
            .expect("update");
        let read = issue_services::issue(&db, moved.id)
            .await
            .expect("issue should load")
            .expect("issue should be there");
        assert_eq!(read.status, status);
        assert_eq!(read.priority, priority);
    }

    let archived = Project {
        status: project::Status::Archived,
        ..project_services::project(&db, "tt")
            .await
            .expect("project")
            .expect("project should be there")
    };
    project_services::update_project(&db, &archived)
        .await
        .expect("update");
    assert_eq!(
        project_services::project(&db, "tt")
            .await
            .expect("project")
            .expect("project should be there")
            .status,
        project::Status::Archived
    );
}
