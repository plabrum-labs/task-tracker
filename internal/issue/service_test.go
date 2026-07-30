package issue_test

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/Plabrum/tt/ent"
	entissue "github.com/Plabrum/tt/ent/issue"
	entproject "github.com/Plabrum/tt/ent/project"
	entref "github.com/Plabrum/tt/ent/ref"
	"github.com/Plabrum/tt/internal/dbtest"
	"github.com/Plabrum/tt/internal/issue"
	"github.com/Plabrum/tt/internal/project"
)

func newService(t *testing.T) (*ent.Client, *issue.Service) {
	t.Helper()
	client := dbtest.Client(t)
	return client, issue.NewService(client, project.NewService(client))
}

func TestAddCreatesProject(t *testing.T) {
	t.Parallel()
	ctx := context.Background()
	client, svc := newService(t)

	got, err := svc.Add(ctx, issue.AddParams{Project: "brand-new", Title: "capture me"})
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
	ctx := context.Background()
	client, svc := newService(t)

	for _, title := range []string{"first", "second", "third"} {
		if _, err := svc.Add(ctx, issue.AddParams{Project: "tt", Title: title}); err != nil {
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
	ctx := context.Background()
	_, svc := newService(t)

	got, err := svc.Add(ctx, issue.AddParams{Project: "tt", Title: "  padded title  "})
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
	ctx := context.Background()
	_, svc := newService(t)

	got, err := svc.Add(ctx, issue.AddParams{
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
		{"sub-of", func(p *issue.AddParams) { p.SubOf = 12 }, "not wired yet"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			ctx := context.Background()
			client, svc := newService(t)

			p := ok
			tt.mutate(&p)
			_, err := svc.Add(ctx, p)
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

// TestAddIsAtomic is the reason EnsureIn takes a client instead of opening its
// own transaction. The project upsert happens before the not-wired guards, so
// a rejected `tt add -M v1` in a fresh directory must not leave the project
// behind — otherwise `tt ls -A` grows a row for work that was never captured.
func TestAddIsAtomic(t *testing.T) {
	t.Parallel()
	ctx := context.Background()
	client, svc := newService(t)

	_, err := svc.Add(ctx, issue.AddParams{
		Project: "ghost", Title: "never written", Milestone: "v1",
	})
	if err == nil {
		t.Fatal("Add succeeded, want the milestone guard to reject it")
	}

	if n := client.Project.Query().Where(entproject.SlugEQ("ghost")).CountX(ctx); n != 0 {
		t.Errorf("project rows = %d, want 0 — the upsert was not rolled back", n)
	}
}

// TestGetDetail renders the whole contract against real rows. A shape that has
// never been assembled from the database is not frozen, it is just declared.
func TestGetDetail(t *testing.T) {
	t.Parallel()
	ctx := context.Background()
	client, svc := newService(t)

	proj := client.Project.Create().SetSlug("tt").SaveX(ctx)
	newIssue := func(title string, status entissue.Status) *ent.Issue {
		return client.Issue.Create().
			SetTitle(title).SetProject(proj).SetStatus(status).SaveX(ctx)
	}

	parent := newIssue("ship the domain core", entissue.StatusDoing)
	blocker := newIssue("pick a driver", entissue.StatusDone)
	childDone := newIssue("model.go", entissue.StatusDone)
	childTodo := newIssue("service.go", entissue.StatusTodo)
	dependent := newIssue("ship the CLI", entissue.StatusTodo)

	link := func(blocked, blocker *ent.Issue, kind entref.Kind) {
		client.Ref.Create().SetBlocked(blocked).SetBlocker(blocker).SetKind(kind).SaveX(ctx)
	}
	link(parent, blocker, entref.KindDep)
	link(parent, childDone, entref.KindSubtask)
	link(parent, childTodo, entref.KindSubtask)
	link(dependent, parent, entref.KindDep)

	// Written out of order so the assertion below is testing the query's
	// ordering rather than insertion order.
	at := func(min int) time.Time { return time.Date(2026, 1, 2, 3, min, 0, 0, time.UTC) }
	client.Comment.Create().SetIssue(parent).SetAuthor("phil").
		SetBody("second").SetAt(at(30)).SaveX(ctx)
	client.Comment.Create().SetIssue(parent).SetAuthor("phil").
		SetBody("first").SetAt(at(10)).SaveX(ctx)

	got, err := svc.Get(ctx, parent.ID)
	if err != nil {
		t.Fatalf("Get: %v", err)
	}

	if got.ID != parent.ID || got.Title != "ship the domain core" {
		t.Errorf("issue = %d/%q, want %d/%q",
			got.ID, got.Title, parent.ID, "ship the domain core")
	}
	if got.Status != issue.StatusDoing {
		t.Errorf("status = %q, want %q", got.Status, issue.StatusDoing)
	}
	if got.Project != "tt" {
		t.Errorf("project = %q, want %q", got.Project, "tt")
	}

	// Blockers hold only the dep. The subtask children arrive through the same
	// edge and must not leak into this list, even though they block just as
	// hard.
	if len(got.Blockers) != 1 || got.Blockers[0].ID != blocker.ID {
		t.Fatalf("blockers = %+v, want just %d", got.Blockers, blocker.ID)
	}
	if got.Blockers[0].Kind != issue.KindDep || got.Blockers[0].Status != issue.StatusDone {
		t.Errorf("blocker = %+v, want kind=dep status=done", got.Blockers[0])
	}

	if len(got.Subtasks) != 2 {
		t.Fatalf("subtasks = %+v, want 2", got.Subtasks)
	}
	if got.Subtasks[0].ID != childDone.ID || got.Subtasks[1].ID != childTodo.ID {
		t.Errorf("subtasks = %+v, want [%d %d] in id order",
			got.Subtasks, childDone.ID, childTodo.ID)
	}
	if got.Subtasks[0].Kind != issue.KindSubtask {
		t.Errorf("subtask kind = %q, want %q", got.Subtasks[0].Kind, issue.KindSubtask)
	}

	if len(got.Dependents) != 1 || got.Dependents[0].ID != dependent.ID {
		t.Errorf("dependents = %+v, want just %d", got.Dependents, dependent.ID)
	}

	if want := (issue.Rollup{Done: 1, Total: 2}); got.Rollup != want {
		t.Errorf("rollup = %+v, want %+v", got.Rollup, want)
	}

	if len(got.Comments) != 2 {
		t.Fatalf("comments = %+v, want 2", got.Comments)
	}
	if got.Comments[0].Body != "first" || got.Comments[1].Body != "second" {
		t.Errorf("comments = [%q %q], want them in `at` order",
			got.Comments[0].Body, got.Comments[1].Body)
	}
	if !got.Comments[0].At.Equal(at(10)) {
		t.Errorf("comment at = %v, want %v", got.Comments[0].At, at(10))
	}
}

// TestGetEmptyDetail: an issue with nothing attached still answers with empty
// slices, so `tt show --json` prints [] and a consumer never branches on null.
func TestGetEmptyDetail(t *testing.T) {
	t.Parallel()
	ctx := context.Background()
	_, svc := newService(t)

	added, err := svc.Add(ctx, issue.AddParams{Project: "tt", Title: "alone"})
	if err != nil {
		t.Fatalf("Add: %v", err)
	}
	got, err := svc.Get(ctx, added.ID)
	if err != nil {
		t.Fatalf("Get: %v", err)
	}

	if got.Blockers == nil || got.Subtasks == nil || got.Dependents == nil ||
		got.Comments == nil || got.Labels == nil {
		t.Errorf("detail has nil slices: %+v", got)
	}
	if want := (issue.Rollup{}); got.Rollup != want {
		t.Errorf("rollup = %+v, want %+v", got.Rollup, want)
	}
}

func TestGetNotFound(t *testing.T) {
	t.Parallel()
	ctx := context.Background()
	_, svc := newService(t)

	_, err := svc.Get(ctx, 404)
	if !errors.Is(err, issue.ErrNotFound) {
		t.Errorf("err = %v, want it to wrap issue.ErrNotFound", err)
	}
}
