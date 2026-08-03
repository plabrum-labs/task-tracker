"""The tables, and the only file that names them into being.

The Core ``Table`` objects give ``services`` typed columns to ``select``,
``insert`` and ``update`` against. The raw ``DDL`` is what actually creates the
schema, because Core's ``create_all`` cannot emit ``STRICT``, a ``CHECK``, an
``ON DELETE CASCADE`` or a partial unique index — the same gap the Rust spike
records. So the tables declare the columns and the DDL declares the tables, and
``test_store`` asserts the two describe one schema by reading ``sqlite_master``.

The partial unique index is what makes a slug reusable once its project is
deleted, and it is the guarantee behind ``createProject``'s refusal rather than a
duplicate of it.
"""

from sqlalchemy import Column, Engine, Integer, MetaData, Table, Text

from tt.platform import db

metadata = MetaData()

projects = Table(
    "projects",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("slug", Text, nullable=False),
    Column("title", Text, nullable=False),
    Column("body", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
    Column("deleted_at", Text, nullable=True),
)

issues = Table(
    "issues",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("project_id", Integer, nullable=False),
    Column("title", Text, nullable=False),
    Column("body", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("priority", Integer, nullable=False),
    Column("status_note", Text, nullable=True),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
    Column("deleted_at", Text, nullable=True),
)

DDL: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS projects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        slug TEXT NOT NULL,
        title TEXT NOT NULL,
        body TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('active', 'archived')),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        deleted_at TEXT
    ) STRICT
    """,
    """
    CREATE TABLE IF NOT EXISTS issues (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
        title TEXT NOT NULL,
        body TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('todo', 'doing', 'done')),
        priority INTEGER NOT NULL CHECK (priority IN (0, 1)),
        status_note TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        deleted_at TEXT
    ) STRICT
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS projects_slug_live
        ON projects (slug) WHERE deleted_at IS NULL
    """,
    """
    CREATE INDEX IF NOT EXISTS issues_by_project
        ON issues (project_id, status) WHERE deleted_at IS NULL
    """,
]


def initialise(engine: Engine) -> None:
    """Create the schema, safe to run on a database that already has it."""
    db.apply_ddl(engine, DDL)


def declared_columns() -> list[tuple[str, list[str]]]:
    """The columns each table declares, in declaration order — paired with
    ``db.emitted_ddl`` this is the assertion that the raw DDL and the Core tables
    describe one schema."""
    return [(table.name, [column.name for column in table.columns]) for table in (projects, issues)]
