package db

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"time"

	"ariga.io/atlas/sql/migrate"
)

// revisionsTable is Atlas's conventional name for the migration history table.
const revisionsTable = "atlas_schema_revisions"

// revisions is a migrate.RevisionReadWriter over a plain SQLite table.
//
// Atlas ships no persistent implementation — Ent uses a no-op internally — so
// tracking which migrations have run is ours to do. It talks raw database/sql
// because it runs before the Ent client exists.
type revisions struct {
	db *sql.DB
}

var _ migrate.RevisionReadWriter = (*revisions)(nil)

// newRevisions creates the history table if it is not already there.
func newRevisions(ctx context.Context, db *sql.DB) (*revisions, error) {
	const stmt = `CREATE TABLE IF NOT EXISTS ` + revisionsTable + ` (
		version          text NOT NULL PRIMARY KEY,
		description      text NOT NULL,
		type             integer NOT NULL,
		applied          integer NOT NULL,
		total            integer NOT NULL,
		executed_at      datetime NOT NULL,
		execution_time   integer NOT NULL,
		error            text NOT NULL,
		error_stmt       text NOT NULL,
		hash             text NOT NULL,
		partial_hashes   text NOT NULL,
		operator_version text NOT NULL
	)`
	if _, err := db.ExecContext(ctx, stmt); err != nil {
		return nil, fmt.Errorf("creating %s: %w", revisionsTable, err)
	}
	return &revisions{db: db}, nil
}

func (r *revisions) Ident() *migrate.TableIdent {
	return &migrate.TableIdent{Name: revisionsTable}
}

const revisionColumns = `version, description, type, applied, total, executed_at,
	execution_time, error, error_stmt, hash, partial_hashes, operator_version`

func (r *revisions) ReadRevisions(ctx context.Context) ([]*migrate.Revision, error) {
	rows, err := r.db.QueryContext(ctx,
		`SELECT `+revisionColumns+` FROM `+revisionsTable+` ORDER BY version`)
	if err != nil {
		return nil, fmt.Errorf("reading revisions: %w", err)
	}
	defer rows.Close()

	var revs []*migrate.Revision
	for rows.Next() {
		rev, err := scanRevision(rows)
		if err != nil {
			return nil, err
		}
		revs = append(revs, rev)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("reading revisions: %w", err)
	}
	return revs, nil
}

func (r *revisions) ReadRevision(ctx context.Context, version string) (*migrate.Revision, error) {
	row := r.db.QueryRowContext(ctx,
		`SELECT `+revisionColumns+` FROM `+revisionsTable+` WHERE version = ?`, version)
	rev, err := scanRevision(row)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, migrate.ErrRevisionNotExist
	}
	return rev, err
}

func (r *revisions) WriteRevision(ctx context.Context, rev *migrate.Revision) error {
	hashes, err := json.Marshal(rev.PartialHashes)
	if err != nil {
		return fmt.Errorf("encoding partial hashes for %s: %w", rev.Version, err)
	}
	_, err = r.db.ExecContext(ctx,
		`INSERT INTO `+revisionsTable+` (`+revisionColumns+`)
		 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
		 ON CONFLICT (version) DO UPDATE SET
		   description = excluded.description,
		   type = excluded.type,
		   applied = excluded.applied,
		   total = excluded.total,
		   executed_at = excluded.executed_at,
		   execution_time = excluded.execution_time,
		   error = excluded.error,
		   error_stmt = excluded.error_stmt,
		   hash = excluded.hash,
		   partial_hashes = excluded.partial_hashes,
		   operator_version = excluded.operator_version`,
		rev.Version, rev.Description, int(rev.Type), rev.Applied, rev.Total,
		rev.ExecutedAt, int64(rev.ExecutionTime), rev.Error, rev.ErrorStmt,
		rev.Hash, string(hashes), rev.OperatorVersion,
	)
	if err != nil {
		return fmt.Errorf("writing revision %s: %w", rev.Version, err)
	}
	return nil
}

func (r *revisions) DeleteRevision(ctx context.Context, version string) error {
	if _, err := r.db.ExecContext(ctx,
		`DELETE FROM `+revisionsTable+` WHERE version = ?`, version); err != nil {
		return fmt.Errorf("deleting revision %s: %w", version, err)
	}
	return nil
}

// scanner is satisfied by both *sql.Row and *sql.Rows.
type scanner interface {
	Scan(dest ...any) error
}

func scanRevision(s scanner) (*migrate.Revision, error) {
	var (
		rev           migrate.Revision
		typ           int
		executionTime int64
		partialHashes string
	)
	err := s.Scan(
		&rev.Version, &rev.Description, &typ, &rev.Applied, &rev.Total,
		&rev.ExecutedAt, &executionTime, &rev.Error, &rev.ErrorStmt,
		&rev.Hash, &partialHashes, &rev.OperatorVersion,
	)
	if err != nil {
		return nil, err
	}
	rev.Type = migrate.RevisionType(typ)
	rev.ExecutionTime = time.Duration(executionTime)
	if err := json.Unmarshal([]byte(partialHashes), &rev.PartialHashes); err != nil {
		return nil, fmt.Errorf("decoding partial hashes for %s: %w", rev.Version, err)
	}
	return &rev, nil
}
