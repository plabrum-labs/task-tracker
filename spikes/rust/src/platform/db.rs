//! The database, with no table in it.
//!
//! Everything a query needs and nothing about what is being queried: a
//! connection, the way to open one, the way to apply a DDL text, and the
//! narrowing of sea-orm's error. A domain's `services.rs` names the tables;
//! this file cannot.
//!
//! This is the same split [`crate::platform::wire`] makes for JSON. What keeps
//! the base tables out of reach is that [`crate::domains::schema`] declares them
//! `pub(in crate::domains)`, so the entity types, the raw DDL and the row
//! structs can be named from a domain's services and from nowhere else — and
//! every read is then guaranteed to carry its liveness predicate by
//! construction, because a frontend cannot write the query that forgets it.
//!
//! That is what `store.mli` buys the OCaml spike, bought with one keyword
//! instead of a second copy of every signature. It is also what stands in for
//! the SQL views neither side declares.

use std::fmt;

use sea_orm::{
    ConnectionTrait, Database, DatabaseConnection, DatabaseTransaction, DbBackend, DbErr,
    Statement, TransactionTrait,
};

use crate::platform::error::Error;

pub type Db = DatabaseConnection;

/// One error type for the whole edge, separate from [`crate::Error`].
///
/// [`crate::Error`] is the domain's refusals and stays free of anything
/// transport- or storage-shaped; nothing a caller can do about a `DbErr` is
/// anything an action refused.
///
/// `MissingAfterInsert` is the case with no row to blame: a create writes, then
/// loads the row back through the same projection every other read uses, and
/// that load can only come up empty if something removed the row in between.
#[derive(Debug)]
pub enum StoreError {
    Db(DbErr),
    MissingAfterInsert(&'static str),
}

impl fmt::Display for StoreError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            StoreError::Db(e) => write!(f, "{e}"),
            StoreError::MissingAfterInsert(what) => {
                write!(f, "{what} vanished between the insert and the read")
            }
        }
    }
}

impl std::error::Error for StoreError {}

impl From<DbErr> for StoreError {
    fn from(e: DbErr) -> Self {
        StoreError::Db(e)
    }
}

/// `sqlite::memory:` is opened shared-cache by sqlx and sea-orm caps a SQLite
/// pool at one connection, so a pooled in-memory database is one database and a
/// test fixture is one `connect`.
pub async fn connect(url: &str) -> Result<Db, StoreError> {
    Ok(Database::connect(url).await?)
}

/// One public call is one transaction. `f` runs inside `BEGIN`…`COMMIT`; any
/// [`Error`] it returns — including one after rows are written — rolls the whole
/// thing back. The frontends open this at their edge and hand the transaction
/// down to an action's `execute`, so what an action does is undone in full if
/// it, or anything after it, says no.
///
/// OCaml's `Db.transaction` takes a `unit -> result` because its connection is
/// ambient; Rust's is not, so `f` is an async closure handed the open
/// transaction to borrow. That borrow living across the `await` is exactly what
/// the async-closure traits were added to express, and it is why this stays one
/// helper rather than a begin/commit dance repeated at every write.
///
/// A driver-level failure — `begin`, `commit` or `rollback` — is the machine
/// saying no rather than the row, so it reaches a caller as [`Error::Broken`]
/// through [`StoreError`].
pub async fn transaction<T, F>(db: &Db, f: F) -> Result<T, Error>
where
    F: AsyncFnOnce(&DatabaseTransaction) -> Result<T, Error>,
{
    let tx = db.begin().await.map_err(StoreError::from)?;
    match f(&tx).await {
        Ok(value) => {
            tx.commit().await.map_err(StoreError::from)?;
            Ok(value)
        }
        Err(e) => {
            tx.rollback().await.map_err(StoreError::from)?;
            Err(e)
        }
    }
}

/// Applies a DDL text, which is safe on a database that already has the tables.
/// The statements are the caller's: this file knows how to run them and not
/// what is in them — see [`crate::domains::schema`].
pub async fn apply_ddl(db: &Db, ddl: &[&str]) -> Result<(), StoreError> {
    for sql in ddl {
        db.execute_unprepared(sql).await?;
    }
    Ok(())
}

/// What the database actually holds, so a test can hold the emitted schema
/// against the columns the entities declare.
pub async fn emitted_ddl(db: &Db) -> Result<Vec<String>, StoreError> {
    Ok(db
        .query_all(Statement::from_string(
            DbBackend::Sqlite,
            "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name",
        ))
        .await?
        .iter()
        .filter_map(|row| row.try_get_by_index::<String>(0).ok())
        .collect())
}
