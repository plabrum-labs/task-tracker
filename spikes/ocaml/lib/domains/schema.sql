-- The schema, and the whole of it.
--
-- Dune embeds this file in the binary and Atlas diffs migrations/ against it,
-- so the database the app creates and the migrations the tool generates come
-- from one text. What the store still states a second time is the row
-- decoders: a SELECT names these columns again, in an order the compiler
-- checks only the types of.
--
-- PRAGMA foreign_keys is not here. It is a property of a connection rather
-- than of a schema, so store.ml sets it on the way in.
--
-- The partial unique index is what makes a slug reusable once its project is
-- deleted, and it is the guarantee behind createProject's refusal rather than
-- a duplicate of it.

CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'archived')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT
) STRICT;

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
) STRICT;

CREATE UNIQUE INDEX IF NOT EXISTS projects_slug_live
    ON projects (slug) WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS issues_by_project
    ON issues (project_id, status) WHERE deleted_at IS NULL;
