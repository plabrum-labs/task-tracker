package app_test

import (
	"strings"
	"testing"

	entissue "github.com/Plabrum/tt/ent/issue"
	entproject "github.com/Plabrum/tt/ent/project"
	"github.com/Plabrum/tt/internal/dbtest"
	"github.com/Plabrum/tt/internal/issue"
)

func ptr[T any](v T) *T { return &v }

func TestAddCreatesProject(t *testing.T) {
	t.Parallel()
	ctx := t.Context()
	client, api := dbtest.API(t)

	got, err := api.Add(ctx, issue.AddParams{Project: "brand-new", Title: "capture me"})
	if err != nil {
		t.Fatalf("Add: %v", err)
	}

	if got.Project != "brand-new" {
		t.Errorf("project = %q, want %q", got.Project, "brand-new")
	}
	if n := client.Project.Query().Where(entproject.SlugEQ("brand-new")).CountX(ctx); n != 1 {
		t.Errorf("project rows = %d, want 1", n)
	}
	// The issue is actually attached, not merely coincident with a project row.
	linked := client.Issue.Query().
		Where(entissue.HasProjectWith(entproject.SlugEQ("brand-new"))).
		CountX(ctx)
	if linked != 1 {
		t.Errorf("issues in brand-new = %d, want 1", linked)
	}
}

func TestAddReusesProject(t *testing.T) {
	t.Parallel()
	ctx := t.Context()
	client, api := dbtest.API(t)

	for _, title := range []string{"first", "second", "third"} {
		if _, err := api.Add(ctx, issue.AddParams{Project: "tt", Title: title}); err != nil {
			t.Fatalf("Add(%q): %v", title, err)
		}
	}

	if n := client.Project.Query().CountX(ctx); n != 1 {
		t.Errorf("projects = %d, want 1", n)
	}
	if n := client.Issue.Query().CountX(ctx); n != 3 {
		t.Errorf("issues = %d, want 3", n)
	}
}

func TestAddDefaults(t *testing.T) {
	t.Parallel()
	ctx := t.Context()
	_, api := dbtest.API(t)

	got, err := api.Add(ctx, issue.AddParams{Project: "tt", Title: "  padded title  "})
	if err != nil {
		t.Fatalf("Add: %v", err)
	}

	if got.ID == 0 {
		t.Error("id = 0, want an assigned id")
	}
	if got.Title != "padded title" {
		t.Errorf("title = %q, want it trimmed to %q", got.Title, "padded title")
	}
	if got.Status != issue.StatusTodo {
		t.Errorf("status = %q, want %q", got.Status, issue.StatusTodo)
	}
	if got.Priority != issue.PriorityNormal {
		t.Errorf("priority = %q, want %q", got.Priority, issue.PriorityNormal)
	}
	if got.Body != "" || got.Milestone != "" {
		t.Errorf("body = %q, milestone = %q, want both empty", got.Body, got.Milestone)
	}
	if got.ClosedAt != nil {
		t.Errorf("closed_at = %v, want nil", got.ClosedAt)
	}
	// Non-nil but empty: --json must print [] here, never null.
	if got.Labels == nil || len(got.Labels) != 0 {
		t.Errorf("labels = %#v, want an empty non-nil slice", got.Labels)
	}
	if got.CreatedAt.IsZero() || got.UpdatedAt.IsZero() {
		t.Errorf("timestamps not stamped: created=%v updated=%v", got.CreatedAt, got.UpdatedAt)
	}
}

func TestAddHonoursPriority(t *testing.T) {
	t.Parallel()
	ctx := t.Context()
	_, api := dbtest.API(t)

	got, err := api.Add(ctx, issue.AddParams{
		Project: "tt", Title: "urgent", Priority: issue.PriorityHi,
	})
	if err != nil {
		t.Fatalf("Add: %v", err)
	}
	if got.Priority != issue.PriorityHi {
		t.Errorf("priority = %q, want %q", got.Priority, issue.PriorityHi)
	}
}

func TestAddRejects(t *testing.T) {
	t.Parallel()
	ok := issue.AddParams{Project: "tt", Title: "fine"}

	tests := []struct {
		name    string
		mutate  func(*issue.AddParams)
		wantErr string
	}{
		{"empty title", func(p *issue.AddParams) { p.Title = "" }, "title is required"},
		{"whitespace title", func(p *issue.AddParams) { p.Title = "   " }, "title is required"},
		{"empty project", func(p *issue.AddParams) { p.Project = "" }, "project is required"},
		{"bad priority", func(p *issue.AddParams) { p.Priority = "urgent" }, "unknown priority"},
		{"labels", func(p *issue.AddParams) { p.Labels = []string{"backend"} }, "not wired yet"},
		{"milestone", func(p *issue.AddParams) { p.Milestone = "v1" }, "not wired yet"},
		{"sub-of", func(p *issue.AddParams) { p.SubOf = ptr(12) }, "not wired yet"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			ctx := t.Context()
			client, api := dbtest.API(t)

			p := ok
			tt.mutate(&p)
			_, err := api.Add(ctx, p)
			if err == nil {
				t.Fatalf("Add(%+v) succeeded, want %q", p, tt.wantErr)
			}
			if !strings.Contains(err.Error(), tt.wantErr) {
				t.Errorf("err = %q, want it to contain %q", err, tt.wantErr)
			}
			if n := client.Issue.Query().CountX(ctx); n != 0 {
				t.Errorf("issues = %d, want 0 after a rejected Add", n)
			}
		})
	}
}

// TestAddIsAtomic is the reason project.Ensure takes a client instead of
// opening its own transaction. The upsert happens first, so an `tt add` that
// fails after it must not leave the project behind — otherwise `tt ls -A` grows
// a row for work that was never captured.
func TestAddIsAtomic(t *testing.T) {
	t.Parallel()
	ctx := t.Context()
	client, api := dbtest.API(t)

	// Rejected by issue.Create's validation, which runs after the upsert. The
	// not-wired guards cannot serve here: they run before the transaction opens,
	// so they would pass this test without a rollback ever happening.
	_, err := api.Add(ctx, issue.AddParams{Project: "ghost", Title: "   "})
	if err == nil {
		t.Fatal("Add succeeded, want the empty title to reject it")
	}

	if n := client.Project.Query().Where(entproject.SlugEQ("ghost")).CountX(ctx); n != 0 {
		t.Errorf("project rows = %d, want 0 — the upsert was not rolled back", n)
	}
}
