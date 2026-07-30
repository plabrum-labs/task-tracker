package db

import (
	"database/sql"
	"errors"
	"strings"
	"testing"
	"time"

	"ariga.io/atlas/sql/migrate"
)

// newTestRevisions returns a revisions over a fresh in-memory database.
func newTestRevisions(t *testing.T) (*sql.DB, *revisions) {
	t.Helper()
	sqldb, err := sql.Open("sqlite", DSN(":memory:"))
	if err != nil {
		t.Fatalf("opening: %v", err)
	}
	// One connection means one `:memory:` database rather than a new empty one
	// per pooled connection.
	sqldb.SetMaxOpenConns(1)
	t.Cleanup(func() {
		if err := sqldb.Close(); err != nil {
			t.Errorf("closing: %v", err)
		}
	})

	revs, err := newRevisions(t.Context(), sqldb)
	if err != nil {
		t.Fatalf("newRevisions: %v", err)
	}
	return sqldb, revs
}

// sampleRevision is a filled-in revision: every field non-zero, so a column
// swapped in the INSERT or the SELECT shows up as a mismatch rather than as two
// equal zero values.
//
// ExecutedAt is UTC and truncated to microseconds because the datetime
// round-trip drops the monotonic clock and does not carry a location.
func sampleRevision(version string) *migrate.Revision {
	return &migrate.Revision{
		Version:         version,
		Description:     "add the `refs` table",
		Type:            migrate.RevisionTypeExecute,
		Applied:         3,
		Total:           4,
		ExecutedAt:      time.Date(2026, 7, 30, 12, 34, 56, 123456000, time.UTC),
		ExecutionTime:   1500 * time.Millisecond,
		Error:           `near "SELCT": syntax error`,
		ErrorStmt:       `SELCT 1`,
		Hash:            "h1:abc123=",
		PartialHashes:   []string{`h1:one"quoted=`, "h1:twö=", "h1:three="},
		OperatorVersion: "tt/test",
	}
}

func assertRevisionEqual(t *testing.T, got, want *migrate.Revision) {
	t.Helper()
	if !got.ExecutedAt.Equal(want.ExecutedAt) {
		t.Errorf("ExecutedAt = %v, want %v", got.ExecutedAt, want.ExecutedAt)
	}
	// Compared field by field with the time zeroed, so a mismatch names the
	// column instead of printing two long structs.
	g, w := *got, *want
	g.ExecutedAt, w.ExecutedAt = time.Time{}, time.Time{}
	if g.Version != w.Version || g.Description != w.Description || g.Type != w.Type ||
		g.Applied != w.Applied || g.Total != w.Total || g.ExecutionTime != w.ExecutionTime ||
		g.Error != w.Error || g.ErrorStmt != w.ErrorStmt || g.Hash != w.Hash ||
		g.OperatorVersion != w.OperatorVersion {
		t.Errorf("revision = %+v, want %+v", g, w)
	}
	if len(got.PartialHashes) != len(want.PartialHashes) {
		t.Fatalf("PartialHashes = %q, want %q", got.PartialHashes, want.PartialHashes)
	}
	for i := range want.PartialHashes {
		if got.PartialHashes[i] != want.PartialHashes[i] {
			t.Errorf("PartialHashes[%d] = %q, want %q",
				i, got.PartialHashes[i], want.PartialHashes[i])
		}
	}
}

func TestRevisionRoundTrip(t *testing.T) {
	t.Parallel()
	ctx := t.Context()
	_, revs := newTestRevisions(t)

	want := sampleRevision("20260730120000")
	if err := revs.WriteRevision(ctx, want); err != nil {
		t.Fatalf("WriteRevision: %v", err)
	}
	got, err := revs.ReadRevision(ctx, want.Version)
	if err != nil {
		t.Fatalf("ReadRevision: %v", err)
	}
	assertRevisionEqual(t, got, want)
}

// TestRevisionNilPartialHashes: a baseline revision has no statement hashes,
// and it must come back as nil rather than as an empty slice — the executor
// treats the two differently when it decides whether a file was partially
// applied.
func TestRevisionNilPartialHashes(t *testing.T) {
	t.Parallel()
	ctx := t.Context()
	_, revs := newTestRevisions(t)

	rev := sampleRevision("20260730120000")
	rev.PartialHashes = nil
	if err := revs.WriteRevision(ctx, rev); err != nil {
		t.Fatalf("WriteRevision: %v", err)
	}
	got, err := revs.ReadRevision(ctx, rev.Version)
	if err != nil {
		t.Fatalf("ReadRevision: %v", err)
	}
	if got.PartialHashes != nil {
		t.Errorf("PartialHashes = %#v, want nil", got.PartialHashes)
	}
}

// TestWriteRevisionUpdatesInPlace covers the ON CONFLICT block. The executor
// writes a revision once before running a file and again after, so an INSERT
// that did not upsert would fail the second write and leave every migration
// recorded as unfinished.
func TestWriteRevisionUpdatesInPlace(t *testing.T) {
	t.Parallel()
	ctx := t.Context()
	sqldb, revs := newTestRevisions(t)

	first := sampleRevision("20260730120000")
	first.Applied = 1
	if err := revs.WriteRevision(ctx, first); err != nil {
		t.Fatalf("first WriteRevision: %v", err)
	}

	second := sampleRevision("20260730120000")
	second.Applied = 4
	second.Error = ""
	second.ErrorStmt = ""
	second.PartialHashes = []string{"h1:only="}
	if err := revs.WriteRevision(ctx, second); err != nil {
		t.Fatalf("second WriteRevision: %v", err)
	}

	var n int
	if err := sqldb.QueryRowContext(ctx,
		`SELECT count(*) FROM `+revisionsTable).Scan(&n); err != nil {
		t.Fatalf("counting: %v", err)
	}
	if n != 1 {
		t.Errorf("rows = %d, want 1 — the second write inserted instead of updating", n)
	}

	got, err := revs.ReadRevision(ctx, second.Version)
	if err != nil {
		t.Fatalf("ReadRevision: %v", err)
	}
	assertRevisionEqual(t, got, second)
}

func TestReadRevisionMissing(t *testing.T) {
	t.Parallel()
	_, revs := newTestRevisions(t)

	_, err := revs.ReadRevision(t.Context(), "20990101000000")
	if !errors.Is(err, migrate.ErrRevisionNotExist) {
		t.Errorf("err = %v, want it to wrap migrate.ErrRevisionNotExist", err)
	}
}

// TestReadRevisionsOrdersByVersion: the executor compares the last applied
// version against the directory's files, so insertion order must not decide
// what "last" means.
func TestReadRevisionsOrdersByVersion(t *testing.T) {
	t.Parallel()
	ctx := t.Context()
	_, revs := newTestRevisions(t)

	for _, v := range []string{"20260730120000", "20260101000000", "20261231235959"} {
		if err := revs.WriteRevision(ctx, sampleRevision(v)); err != nil {
			t.Fatalf("WriteRevision(%s): %v", v, err)
		}
	}

	got, err := revs.ReadRevisions(ctx)
	if err != nil {
		t.Fatalf("ReadRevisions: %v", err)
	}
	want := []string{"20260101000000", "20260730120000", "20261231235959"}
	if len(got) != len(want) {
		t.Fatalf("revisions = %d, want %d", len(got), len(want))
	}
	for i, w := range want {
		if got[i].Version != w {
			t.Errorf("revision %d = %s, want %s", i, got[i].Version, w)
		}
	}
}

func TestDeleteRevision(t *testing.T) {
	t.Parallel()
	ctx := t.Context()
	_, revs := newTestRevisions(t)

	rev := sampleRevision("20260730120000")
	if err := revs.WriteRevision(ctx, rev); err != nil {
		t.Fatalf("WriteRevision: %v", err)
	}
	if err := revs.DeleteRevision(ctx, rev.Version); err != nil {
		t.Fatalf("DeleteRevision: %v", err)
	}
	if _, err := revs.ReadRevision(ctx, rev.Version); !errors.Is(err, migrate.ErrRevisionNotExist) {
		t.Errorf("err after delete = %v, want migrate.ErrRevisionNotExist", err)
	}

	// Deleting what is not there is how the executor undoes a revision it never
	// managed to write, so it is a no-op rather than a failure.
	if err := revs.DeleteRevision(ctx, rev.Version); err != nil {
		t.Errorf("second DeleteRevision: %v", err)
	}
}

// TestNewRevisionsIsIdempotent: every open calls it, so the second open of an
// existing database must not fail on the table already being there — nor drop
// the history it holds.
func TestNewRevisionsIsIdempotent(t *testing.T) {
	t.Parallel()
	ctx := t.Context()
	sqldb, revs := newTestRevisions(t)

	rev := sampleRevision("20260730120000")
	if err := revs.WriteRevision(ctx, rev); err != nil {
		t.Fatalf("WriteRevision: %v", err)
	}

	again, err := newRevisions(ctx, sqldb)
	if err != nil {
		t.Fatalf("second newRevisions: %v", err)
	}
	got, err := again.ReadRevisions(ctx)
	if err != nil {
		t.Fatalf("ReadRevisions: %v", err)
	}
	if len(got) != 1 || got[0].Version != rev.Version {
		t.Errorf("revisions = %+v, want just %s", got, rev.Version)
	}
}

// TestReadRevisionCorruptHashes: partial_hashes is the one column that is not a
// scalar, so it is the one that can be well-typed and still unreadable.
func TestReadRevisionCorruptHashes(t *testing.T) {
	t.Parallel()
	ctx := t.Context()
	sqldb, revs := newTestRevisions(t)

	rev := sampleRevision("20260730120000")
	if err := revs.WriteRevision(ctx, rev); err != nil {
		t.Fatalf("WriteRevision: %v", err)
	}
	if _, err := sqldb.ExecContext(ctx,
		`UPDATE `+revisionsTable+` SET partial_hashes = ? WHERE version = ?`,
		"not json", rev.Version); err != nil {
		t.Fatalf("corrupting partial_hashes: %v", err)
	}

	_, err := revs.ReadRevision(ctx, rev.Version)
	if err == nil {
		t.Fatal("ReadRevision succeeded, want a decode error")
	}
	if !strings.Contains(err.Error(), "decoding partial hashes for "+rev.Version) {
		t.Errorf("err = %v, want it to name the revision it could not decode", err)
	}
}
