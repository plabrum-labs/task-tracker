// Package db opens the SQLite store and brings it up to date. Opening a brand
// new database file is a complete operation: there is no "run migrations
// first" failure mode, because that is the failure mode that gets a personal
// CLI abandoned.
package db

import (
	"context"
	"database/sql"
	"errors"
	"fmt"

	"ariga.io/atlas/sql/migrate"
	atsqlite "ariga.io/atlas/sql/sqlite"
	"entgo.io/ent/dialect"
	entsql "entgo.io/ent/dialect/sql"

	"github.com/Plabrum/tt/ent"
	"github.com/Plabrum/tt/migrations"

	_ "modernc.org/sqlite"
)

// DSN builds the connection string for a database at path.
//
// The pragmas live in the DSN rather than a post-open Exec because they are
// per-connection and database/sql pools connections — setting them once after
// Open would protect only the first one.
func DSN(path string) string {
	return "file:" + path +
		"?_pragma=foreign_keys(1)" +
		"&_pragma=journal_mode(WAL)" +
		"&_pragma=busy_timeout(5000)"
}

// Open connects to dsn, applies any pending migrations, and returns a ready
// Ent client. Closing the client closes the underlying database.
func Open(ctx context.Context, dsn string) (*ent.Client, error) {
	sqldb, err := sql.Open("sqlite", dsn)
	if err != nil {
		return nil, fmt.Errorf("opening %s: %w", dsn, err)
	}
	// SQLite permits one writer. Serializing in Go gives clearer behaviour than
	// surfacing SQLITE_BUSY to the caller.
	sqldb.SetMaxOpenConns(1)

	if err := applyPending(ctx, sqldb); err != nil {
		return nil, errors.Join(err, sqldb.Close())
	}
	return ent.NewClient(ent.Driver(entsql.OpenDB(dialect.SQLite, sqldb))), nil
}

// applyPending executes every embedded migration the database has not seen.
func applyPending(ctx context.Context, sqldb *sql.DB) error {
	dir, err := embeddedDir()
	if err != nil {
		return err
	}
	// Catches an embedded directory whose contents no longer match atlas.sum —
	// a hand-edited migration, or two branches' migrations merged out of order.
	if err := migrate.Validate(dir); err != nil {
		return fmt.Errorf("validating embedded migrations: %w", err)
	}

	drv, err := atsqlite.Open(sqldb)
	if err != nil {
		return fmt.Errorf("opening atlas sqlite driver: %w", err)
	}
	revs, err := newRevisions(ctx, sqldb)
	if err != nil {
		return err
	}
	ex, err := migrate.NewExecutor(drv, dir, revs)
	if err != nil {
		return fmt.Errorf("building migration executor: %w", err)
	}
	// n <= 0 means "all pending".
	if err := ex.ExecuteN(ctx, 0); err != nil && !errors.Is(err, migrate.ErrNoPendingFiles) {
		return fmt.Errorf("applying migrations: %w", err)
	}
	return nil
}

// embeddedDir copies the embedded migrations into an in-memory Atlas
// directory, which is the only shape its Executor accepts.
func embeddedDir() (migrate.Dir, error) {
	entries, err := migrations.FS.ReadDir(".")
	if err != nil {
		return nil, fmt.Errorf("reading embedded migrations: %w", err)
	}
	dir := &migrate.MemDir{}
	for _, e := range entries {
		if e.IsDir() {
			continue
		}
		b, err := migrations.FS.ReadFile(e.Name())
		if err != nil {
			return nil, fmt.Errorf("reading embedded %s: %w", e.Name(), err)
		}
		if err := dir.WriteFile(e.Name(), b); err != nil {
			return nil, fmt.Errorf("staging %s: %w", e.Name(), err)
		}
	}
	return dir, nil
}
