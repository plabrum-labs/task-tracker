package db

import (
	"context"
	"database/sql"
	"errors"
	"net/url"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"github.com/Plabrum/tt/ent"
)

// newTestClient returns a fresh in-memory client with the schema in place.
// It duplicates internal/dbtest, which package db cannot import without a
// cycle.
//
// SetMaxOpenConns(1) inside Open is what keeps `:memory:` coherent: one
// connection means one database, with no cache=shared lifetime games.
func newTestClient(t *testing.T) *ent.Client {
	t.Helper()
	client, err := Open(t.Context(), DSN(":memory:"))
	if err != nil {
		t.Fatalf("opening test client: %v", err)
	}
	if err := client.Schema.Create(t.Context()); err != nil {
		t.Fatalf("creating test schema: %v", err)
	}
	t.Cleanup(func() {
		if err := client.Close(); err != nil {
			t.Errorf("closing test client: %v", err)
		}
	})
	return client
}

// TestOpenFileTwice pins the contract Open has now that it no longer migrates:
// it attaches to an existing database without touching it. The reopen does not
// create the schema, so anything Open did to it would show up as a missing
// table or a lost row.
func TestOpenFileTwice(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "tt.db")

	first, err := Open(ctx, DSN(path))
	if err != nil {
		t.Fatalf("first open: %v", err)
	}
	if err := first.Schema.Create(ctx); err != nil {
		t.Fatalf("creating schema: %v", err)
	}
	proj := first.Project.Create().SetSlug("tt").SaveX(ctx)
	first.Issue.Create().SetTitle("survives a reopen").SetProject(proj).SaveX(ctx)
	if err := first.Close(); err != nil {
		t.Fatalf("closing first: %v", err)
	}

	if _, err := os.Stat(path); err != nil {
		t.Fatalf("database file was not created: %v", err)
	}

	second, err := Open(ctx, DSN(path))
	if err != nil {
		t.Fatalf("second open: %v", err)
	}
	defer func() {
		if err := second.Close(); err != nil {
			t.Errorf("closing second: %v", err)
		}
	}()

	if n := second.Issue.Query().CountX(ctx); n != 1 {
		t.Errorf("issues after reopen = %d, want 1", n)
	}
}

// TestDSNEscapesPath covers the characters that make a path stop being a path
// once it is pasted into a URI. The driver hands the whole DSN to SQLite with
// SQLITE_OPEN_URI, so `?` would start the query string and `#` the fragment;
// the store lives under $HOME, where a directory name is not ours to choose.
func TestDSNEscapesPath(t *testing.T) {
	t.Parallel()

	for _, dir := range []string{"a space", "a?question", "a#hash", "a%percent"} {
		t.Run(dir, func(t *testing.T) {
			t.Parallel()
			path := filepath.Join(t.TempDir(), dir, "tt.db")
			if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
				t.Fatalf("creating %s: %v", filepath.Dir(path), err)
			}
			dsn := DSN(path)

			u, err := url.Parse(dsn)
			if err != nil {
				t.Fatalf("url.Parse(%q): %v", dsn, err)
			}
			// An absolute path parses back into Path, a rootless one like
			// ":memory:" into Opaque. Exactly one is ever set.
			if got := u.Opaque + u.Path; got != path {
				t.Errorf("path round-tripped as %q, want %q", got, path)
			}
			if got := u.Query()["_pragma"]; !slices.Equal(got, wantPragmas) {
				t.Errorf("pragmas = %q, want %q", got, wantPragmas)
			}

			// The parse above only proves the string is well formed. Opening
			// proves SQLite reads the same path back out of it.
			client, err := Open(t.Context(), dsn)
			if err != nil {
				t.Fatalf("opening %q: %v", dsn, err)
			}
			defer func() {
				if err := client.Close(); err != nil {
					t.Errorf("closing: %v", err)
				}
			}()
			if _, err := os.Stat(path); err != nil {
				t.Errorf("database was not created at %s: %v", path, err)
			}
		})
	}
}

var wantPragmas = []string{"foreign_keys(1)", "journal_mode(WAL)", "busy_timeout(5000)"}

// TestDSNInMemory: dbtest hands every package `:memory:`, and escaping the
// leading colon would turn it into a file called ":memory:" on disk.
func TestDSNInMemory(t *testing.T) {
	t.Parallel()

	dsn := DSN(":memory:")
	if want := "file::memory:"; !strings.HasPrefix(dsn, want) {
		t.Fatalf("DSN(%q) = %q, want it to start with %q", ":memory:", dsn, want)
	}

	client, err := Open(t.Context(), dsn)
	if err != nil {
		t.Fatalf("opening in-memory: %v", err)
	}
	defer func() {
		if err := client.Close(); err != nil {
			t.Errorf("closing: %v", err)
		}
	}()

	raw, err := sql.Open("sqlite", dsn)
	if err != nil {
		t.Fatalf("opening raw: %v", err)
	}
	defer raw.Close()

	var file string
	if err := raw.QueryRow(
		`SELECT file FROM pragma_database_list WHERE name = 'main'`).Scan(&file); err != nil {
		t.Fatalf("reading database file: %v", err)
	}
	if file != "" {
		t.Errorf("main database file = %q, want empty — it landed on disk", file)
	}
}

func TestWithTxCommits(t *testing.T) {
	ctx := context.Background()
	client := newTestClient(t)

	err := WithTx(ctx, client, func(tx *ent.Client) error {
		tx.Project.Create().SetSlug("committed").SaveX(ctx)
		return nil
	})
	if err != nil {
		t.Fatalf("WithTx: %v", err)
	}

	if n := client.Project.Query().CountX(ctx); n != 1 {
		t.Errorf("projects = %d, want 1", n)
	}
}

// TestWithTxRollsBackOnError pins both halves of the contract: the write is
// undone, and the caller still gets its own error rather than a rollback report.
func TestWithTxRollsBackOnError(t *testing.T) {
	ctx := context.Background()
	client := newTestClient(t)

	boom := errors.New("boom")
	err := WithTx(ctx, client, func(tx *ent.Client) error {
		tx.Project.Create().SetSlug("doomed").SaveX(ctx)
		return boom
	})
	if !errors.Is(err, boom) {
		t.Errorf("err = %v, want %v", err, boom)
	}

	if n := client.Project.Query().CountX(ctx); n != 0 {
		t.Errorf("projects = %d, want 0", n)
	}
}

// TestWithTxRollsBackOnPanic is the regression test for the hang described on
// WithTx: with SetMaxOpenConns(1), a transaction leaked by a panic makes every
// later query block forever. The final query is the assertion that matters —
// if it returns at all, the connection was released.
func TestWithTxRollsBackOnPanic(t *testing.T) {
	ctx := context.Background()
	client := newTestClient(t)

	func() {
		defer func() {
			p := recover()
			if p == nil {
				t.Error("WithTx swallowed the panic, want it re-raised")
			}
			if got, ok := p.(string); !ok || got != "kaboom" {
				t.Errorf("recovered %v, want %q", p, "kaboom")
			}
		}()
		_ = WithTx(ctx, client, func(tx *ent.Client) error {
			tx.Project.Create().SetSlug("doomed").SaveX(ctx)
			panic("kaboom")
		})
	}()

	if n := client.Project.Query().CountX(ctx); n != 0 {
		t.Errorf("projects = %d, want 0", n)
	}
	// And the client is still usable, not deadlocked behind a leaked tx.
	client.Project.Create().SetSlug("after").SaveX(ctx)
}
