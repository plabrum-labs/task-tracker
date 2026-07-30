package issue_test

import (
	"errors"
	"testing"
	"time"

	"github.com/Plabrum/tt/ent"
	entissue "github.com/Plabrum/tt/ent/issue"
	entref "github.com/Plabrum/tt/ent/ref"
	"github.com/Plabrum/tt/internal/dbtest"
	"github.com/Plabrum/tt/internal/issue"
	"github.com/Plabrum/tt/internal/project"
)

// TestGetDetail renders the whole contract against real rows. A shape that has
// never been assembled from the database is not frozen, it is just declared.
func TestGetDetail(t *testing.T) {
	t.Parallel()
	ctx := t.Context()
	client := dbtest.Client(t)

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

	got, err := issue.Get(ctx, client, parent.ID)
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
	ctx := t.Context()
	client := dbtest.Client(t)

	projectID, err := project.Ensure(ctx, client, "tt")
	if err != nil {
		t.Fatalf("Ensure: %v", err)
	}
	added, err := issue.Create(ctx, client, projectID, issue.AddParams{
		Project: "tt", Title: "alone",
	})
	if err != nil {
		t.Fatalf("Create: %v", err)
	}
	got, err := issue.Get(ctx, client, added.ID)
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
	ctx := t.Context()
	client := dbtest.Client(t)

	_, err := issue.Get(ctx, client, 404)
	if !errors.Is(err, issue.ErrNotFound) {
		t.Errorf("err = %v, want it to wrap issue.ErrNotFound", err)
	}
}
