// Package db opens the SQLite store and runs transactions against it. It does
// not migrate it: bringing a database up to date is `just db-upgrade`, a step
// of its own, run by the Atlas CLI against the committed files in migrations/.
package db

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"net/url"

	"entgo.io/ent/dialect"
	entsql "entgo.io/ent/dialect/sql"

	"github.com/Plabrum/tt/ent"

	_ "modernc.org/sqlite"
)

// DSN builds the connection string for a database at path.
//
// The pragmas live in the DSN rather than a post-open Exec because they are
// per-connection and database/sql pools connections — setting them once after
// Open would protect only the first one.
//
// foreign_keys(1) is load-bearing beyond enforcing the constraints: Ent's
// SQLite auto-migrate refuses to run without it, and that is how every test in
// the repo builds its schema. Dropping it from this list fails five packages
// with an error that names neither this function nor the pragma.
//
// The path is percent-escaped because the driver hands the whole string to
// SQLite as a URI: an unescaped `?` or `#` in the path would terminate it and
// the rest would be read as query or fragment. The driver splits on the first
// `?` before SQLite ever sees it, so the database silently opens at the
// truncated path and the first pragma is silently dropped. The default store
// sits under $HOME, so the path is not ours to constrain.
func DSN(path string) string {
	pragmas := url.Values{"_pragma": {
		"foreign_keys(1)",
		"journal_mode(WAL)",
		"busy_timeout(5000)",
	}}
	u := url.URL{
		Scheme: "file",
		// Opaque rather than Path: it is written out verbatim, so ":memory:"
		// survives as `file::memory:` instead of gaining a leading slash.
		Opaque:   (&url.URL{Path: path}).EscapedPath(),
		RawQuery: pragmas.Encode(),
	}
	return u.String()
}

// Open connects to dsn and returns an Ent client. Closing the client closes
// the underlying database.
//
// The schema is not this function's business — a database that has never been
// migrated opens fine and fails on first query.
func Open(ctx context.Context, dsn string) (*ent.Client, error) {
	sqldb, err := sql.Open("sqlite", dsn)
	if err != nil {
		return nil, fmt.Errorf("opening %s: %w", dsn, err)
	}
	// SQLite permits one writer. Serializing in Go gives clearer behaviour than
	// surfacing SQLITE_BUSY to the caller.
	sqldb.SetMaxOpenConns(1)

	// sql.Open is lazy — it parses the DSN and connects to nothing. Without a
	// ping, a bad path or a rejected pragma surfaces at whichever query runs
	// first, and the file itself is not created until then either.
	if err := sqldb.PingContext(ctx); err != nil {
		return nil, errors.Join(fmt.Errorf("connecting to %s: %w", dsn, err), sqldb.Close())
	}
	return ent.NewClient(ent.Driver(entsql.OpenDB(dialect.SQLite, sqldb))), nil
}

// WithTx runs fn inside a transaction, committing if it returns nil and rolling
// back otherwise.
//
// The deferred recover is load-bearing. The pool is pinned to a single
// connection, so a transaction that is neither committed nor rolled back holds
// the only connection there is and the next query blocks forever. Rolling back
// and re-panicking turns that hang back into the crash it was.
//
// Transactions do not nest: only an app method calls WithTx.
func WithTx(ctx context.Context, client *ent.Client, fn func(tx *ent.Client) error) error {
	tx, err := client.Tx(ctx)
	if err != nil {
		return fmt.Errorf("beginning transaction: %w", err)
	}
	defer func() {
		if p := recover(); p != nil {
			_ = tx.Rollback()
			panic(p)
		}
	}()

	if err := fn(tx.Client()); err != nil {
		// Joined rather than wrapped: a rollback failure is a second, separate
		// problem, and losing fn's error to it would hide the actual cause.
		if rerr := tx.Rollback(); rerr != nil {
			return errors.Join(err, fmt.Errorf("rolling back: %w", rerr))
		}
		return err
	}
	if err := tx.Commit(); err != nil {
		return fmt.Errorf("committing transaction: %w", err)
	}
	return nil
}
