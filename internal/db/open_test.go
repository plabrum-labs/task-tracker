package db

import (
	"context"
	"database/sql"
	"net/url"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/Plabrum/tt/ent"
	"github.com/Plabrum/tt/ent/issue"
	"github.com/Plabrum/tt/ent/ref"
	"github.com/Plabrum/tt/migrations"
)

// newTestClient returns a fresh, fully migrated in-memory client. It goes
// through the same Open production does, so a broken migration path fails here
// rather than on someone's real database.
//
// SetMaxOpenConns(1) inside Open is what keeps `:memory:` coherent: one
// connection means one database, with no cache=shared lifetime games.
func newTestClient(t *testing.T) *ent.Client {
	t.Helper()
	client, err := OpenAndMigrate(t.Context(), DSN(":memory:"))
	if err != nil {
		t.Fatalf("opening test client: %v", err)
	}
	t.Cleanup(func() {
		if err := client.Close(); err != nil {
			t.Errorf("closing test client: %v", err)
		}
	})
	return client
}

func TestRoundTrip(t *testing.T) {
	ctx := context.Background()
	client := newTestClient(t)

	proj := client.Project.Create().
		SetSlug("tt").
		SetTitle("task tracker").
		SaveX(ctx)
	lbl := client.Label.Create().
		SetName("backend").
		SetDescription("server-side work").
		SaveX(ctx)
	due := time.Date(2026, 8, 1, 0, 0, 0, 0, time.UTC)
	ms := client.Milestone.Create().
		SetName("v1").
		SetProject(proj).
		SetDue(due).
		SaveX(ctx)

	iss := client.Issue.Create().
		SetTitle("wire up the schema").
		SetBody("the instruction set an agent reads").
		SetProject(proj).
		SetMilestone(ms).
		AddLabels(lbl).
		SaveX(ctx)
	client.Comment.Create().
		SetAuthor("phil").
		SetBody("started on this").
		SetIssue(iss).
		SaveX(ctx)

	blocker := client.Issue.Create().
		SetTitle("pick a driver").
		SetProject(proj).
		SaveX(ctx)
	client.Ref.Create().
		SetBlocked(iss).
		SetBlocker(blocker).
		SetKind(ref.KindSubtask).
		SaveX(ctx)

	got := client.Issue.Query().
		Where(issue.IDEQ(iss.ID)).
		WithProject().
		WithMilestone().
		WithLabels().
		WithComments().
		WithBlockedBy().
		OnlyX(ctx)

	if got.Title != "wire up the schema" {
		t.Errorf("title = %q, want %q", got.Title, "wire up the schema")
	}
	if got.Status != issue.StatusTodo {
		t.Errorf("status = %q, want %q", got.Status, issue.StatusTodo)
	}
	if got.Priority != issue.PriorityNormal {
		t.Errorf("priority = %q, want %q", got.Priority, issue.PriorityNormal)
	}
	if got.ClosedAt != nil {
		t.Errorf("closed_at = %v, want nil", got.ClosedAt)
	}
	if got.CreatedAt.IsZero() || got.UpdatedAt.IsZero() {
		t.Errorf("timestamps not stamped: created=%v updated=%v", got.CreatedAt, got.UpdatedAt)
	}
	if got.Edges.Project.Slug != "tt" {
		t.Errorf("project slug = %q, want %q", got.Edges.Project.Slug, "tt")
	}
	if got.Edges.Milestone.Name != "v1" {
		t.Errorf("milestone = %q, want %q", got.Edges.Milestone.Name, "v1")
	}
	if got.Edges.Milestone.Due == nil || !got.Edges.Milestone.Due.Equal(due) {
		t.Errorf("milestone due = %v, want %v", got.Edges.Milestone.Due, due)
	}
	if len(got.Edges.Labels) != 1 || got.Edges.Labels[0].Name != "backend" {
		t.Errorf("labels = %v, want [backend]", got.Edges.Labels)
	}
	if len(got.Edges.Comments) != 1 || got.Edges.Comments[0].Body != "started on this" {
		t.Errorf("comments = %v, want one comment", got.Edges.Comments)
	}
	if len(got.Edges.BlockedBy) != 1 || got.Edges.BlockedBy[0].ID != blocker.ID {
		t.Errorf("blocked_by = %v, want [%d]", got.Edges.BlockedBy, blocker.ID)
	}

	// The reverse direction of the same ref, which is what the eligibility
	// predicate in DESIGN.md traverses.
	blocks := client.Issue.Query().
		Where(issue.IDEQ(blocker.ID)).
		QueryBlocks().
		AllX(ctx)
	if len(blocks) != 1 || blocks[0].ID != iss.ID {
		t.Errorf("blocks = %v, want [%d]", blocks, iss.ID)
	}

	// The kind rides on the edge, not on either issue.
	kind := client.Ref.Query().
		Where(ref.BlockedID(iss.ID), ref.BlockerID(blocker.ID)).
		OnlyX(ctx).
		Kind
	if kind != ref.KindSubtask {
		t.Errorf("ref kind = %q, want %q", kind, ref.KindSubtask)
	}
}

// TestOpenFileTwice is the embedded-migration path end to end: a brand-new
// database file comes up migrated with no external tool, and reopening it
// applies nothing a second time.
func TestOpenFileTwice(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "tt.db")

	first, err := OpenAndMigrate(ctx, DSN(path))
	if err != nil {
		t.Fatalf("first open: %v", err)
	}
	proj := first.Project.Create().SetSlug("tt").SaveX(ctx)
	first.Issue.Create().SetTitle("survives a reopen").SetProject(proj).SaveX(ctx)
	if err := first.Close(); err != nil {
		t.Fatalf("closing first: %v", err)
	}

	if _, err := os.Stat(path); err != nil {
		t.Fatalf("database file was not created: %v", err)
	}

	second, err := OpenAndMigrate(ctx, DSN(path))
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

	// One revision per migration file, written once — a second application
	// would have re-run the CREATE TABLEs and failed long before this.
	want := len(embeddedSQLFiles(t))
	if got := countRevisions(t, path); got != want {
		t.Errorf("revisions = %d, want %d", got, want)
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
			client, err := OpenAndMigrate(t.Context(), dsn)
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

	client, err := OpenAndMigrate(t.Context(), dsn)
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

func embeddedSQLFiles(t *testing.T) []string {
	t.Helper()
	entries, err := migrations.FS.ReadDir(".")
	if err != nil {
		t.Fatalf("reading embedded migrations: %v", err)
	}
	var names []string
	for _, e := range entries {
		if filepath.Ext(e.Name()) == ".sql" {
			names = append(names, e.Name())
		}
	}
	if len(names) == 0 {
		t.Fatal("no embedded migrations found")
	}
	return names
}

func countRevisions(t *testing.T, path string) int {
	t.Helper()
	raw, err := sql.Open("sqlite", DSN(path))
	if err != nil {
		t.Fatalf("opening raw: %v", err)
	}
	defer raw.Close()

	var n int
	if err := raw.QueryRow(`SELECT count(*) FROM ` + revisionsTable).Scan(&n); err != nil {
		t.Fatalf("counting revisions: %v", err)
	}
	return n
}

// TestStartCompareAndSwap covers the predicate-guarded update that `start`
// relies on: the guard is what stops two sessions both believing they won.
func TestStartCompareAndSwap(t *testing.T) {
	ctx := context.Background()
	client := newTestClient(t)

	proj := client.Project.Create().SetSlug("tt").SaveX(ctx)
	iss := client.Issue.Create().SetTitle("claim me").SetProject(proj).SaveX(ctx)

	claim := func() int {
		t.Helper()
		n, err := client.Issue.Update().
			Where(issue.IDEQ(iss.ID), issue.StatusEQ(issue.StatusTodo)).
			SetStatus(issue.StatusDoing).
			Save(ctx)
		if err != nil {
			t.Fatalf("compare-and-swap: %v", err)
		}
		return n
	}

	if n := claim(); n != 1 {
		t.Errorf("first claim affected %d rows, want 1", n)
	}
	if n := claim(); n != 0 {
		t.Errorf("second claim affected %d rows, want 0", n)
	}

	if got := client.Issue.GetX(ctx, iss.ID).Status; got != issue.StatusDoing {
		t.Errorf("status = %q, want %q", got, issue.StatusDoing)
	}
}

// TestRefPairIsUnique is what lets Link upsert instead of defending against
// duplicates in application code: an edge schema is an entity with its own id,
// so only the index stops a second identical row.
func TestRefPairIsUnique(t *testing.T) {
	ctx := context.Background()
	client := newTestClient(t)

	proj := client.Project.Create().SetSlug("tt").SaveX(ctx)
	blocked := client.Issue.Create().SetTitle("blocked").SetProject(proj).SaveX(ctx)
	blocker := client.Issue.Create().SetTitle("blocker").SetProject(proj).SaveX(ctx)

	client.Ref.Create().SetBlocked(blocked).SetBlocker(blocker).SaveX(ctx)

	_, err := client.Ref.Create().
		SetBlocked(blocked).
		SetBlocker(blocker).
		SetKind(ref.KindSubtask).
		Save(ctx)
	if err == nil {
		t.Fatal("duplicate (blocked, blocker) was accepted, want a constraint error")
	}
	if !ent.IsConstraintError(err) {
		t.Errorf("err = %v, want a constraint error", err)
	}

	if n := client.Ref.Query().CountX(ctx); n != 1 {
		t.Errorf("refs = %d, want 1", n)
	}
}
