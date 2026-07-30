package backend_test

import (
	"errors"
	"testing"

	"github.com/Plabrum/tt/backend/contract"
	"github.com/Plabrum/tt/backend/errs"
	"github.com/Plabrum/tt/backend/internal/dbtest"
)

// The API is all a frontend can see, so this file walks the surface a frontend
// actually uses rather than re-testing the rules the domains already cover.

func TestCaptureAndShow(t *testing.T) {
	t.Parallel()
	_, api := dbtest.API(t)

	created, err := api.Add(t.Context(), contract.IssueAddParams{Project: "tt", Title: "ship it"})
	if err != nil {
		t.Fatalf("Add: %v", err)
	}

	shown, err := api.ShowIssue(t.Context(), created.ID)
	if err != nil {
		t.Fatalf("ShowIssue: %v", err)
	}
	if shown.Title != "ship it" {
		t.Errorf("Title = %q, want %q", shown.Title, "ship it")
	}
	// A capture creates its project, and the issue carries the whole thing
	// rather than a slug a renderer would have to look up.
	if shown.Project.Slug != "tt" {
		t.Errorf("Project.Slug = %q, want %q", shown.Project.Slug, "tt")
	}
	if len(shown.Actions) == 0 {
		t.Error("a shown issue carries no actions")
	}

	listed, err := api.ListIssues(t.Context(), contract.IssueListParams{})
	if err != nil {
		t.Fatalf("ListIssues: %v", err)
	}
	if len(listed) != 1 || listed[0].ID != created.ID {
		t.Errorf("ListIssues = %+v, want just issue %d", listed, created.ID)
	}
}

// Writes return the object they mutated, reloaded, so a frontend never
// assembles state from what it sent.
func TestWritesReturnTheReloadedObject(t *testing.T) {
	t.Parallel()
	_, api := dbtest.API(t)

	created, err := api.Add(t.Context(), contract.IssueAddParams{Project: "tt", Title: "work"})
	if err != nil {
		t.Fatalf("Add: %v", err)
	}

	started, err := api.IssueStart(t.Context(), created.ID)
	if err != nil {
		t.Fatalf("IssueStart: %v", err)
	}
	if started.Status != contract.IssueDoing {
		t.Errorf("Status = %q, want %q", started.Status, contract.IssueDoing)
	}
	// The returned actions already reflect the write that just happened.
	for _, a := range started.Actions {
		if a.Key == contract.KeyIssueStart {
			t.Error("a doing issue still offers start")
		}
	}

	closed, err := api.IssueClose(t.Context(), created.ID)
	if err != nil {
		t.Fatalf("IssueClose: %v", err)
	}
	if closed.Status != contract.IssueDone || closed.ClosedAt == nil {
		t.Errorf("closed issue = %+v, want done with a ClosedAt", closed)
	}
}

func TestProjectLifecycle(t *testing.T) {
	t.Parallel()
	_, api := dbtest.API(t)

	if _, err := api.Add(t.Context(), contract.IssueAddParams{Project: "tt", Title: "work"}); err != nil {
		t.Fatalf("Add: %v", err)
	}

	archived, err := api.ProjectArchive(t.Context(), "tt")
	if err != nil {
		t.Fatalf("ProjectArchive: %v", err)
	}
	if archived.Status != contract.ProjectArchived {
		t.Errorf("Status = %q, want %q", archived.Status, contract.ProjectArchived)
	}

	// Archiving takes the project's work out of the way, and keeps new work out.
	listed, err := api.ListIssues(t.Context(), contract.IssueListParams{})
	if err != nil {
		t.Fatalf("ListIssues: %v", err)
	}
	if len(listed) != 0 {
		t.Errorf("ListIssues = %+v, want none", listed)
	}
	_, err = api.Add(t.Context(), contract.IssueAddParams{Project: "tt", Title: "more"})
	if !errors.Is(err, errs.ErrConflict) {
		t.Errorf("Add into an archived project = %v, want ErrConflict", err)
	}

	if _, err := api.ProjectRestore(t.Context(), "tt"); err != nil {
		t.Fatalf("ProjectRestore: %v", err)
	}
	if _, err := api.Add(t.Context(), contract.IssueAddParams{Project: "tt", Title: "more"}); err != nil {
		t.Errorf("Add after restoring: %v", err)
	}
}

func TestComments(t *testing.T) {
	t.Parallel()
	_, api := dbtest.API(t)

	issue, err := api.Add(t.Context(), contract.IssueAddParams{Project: "tt", Title: "work"})
	if err != nil {
		t.Fatalf("Add: %v", err)
	}

	// Commenting returns the issue, because that is what a frontend re-renders.
	commented, err := api.IssueComment(t.Context(), issue.ID, "a note")
	if err != nil {
		t.Fatalf("IssueComment: %v", err)
	}
	if len(commented.Comments) != 1 {
		t.Fatalf("Comments = %+v, want one", commented.Comments)
	}
	comment := commented.Comments[0]
	if len(comment.Actions) == 0 {
		t.Error("a comment carries no actions")
	}

	edited, err := api.CommentEdit(t.Context(), comment.ID, "a better note")
	if err != nil {
		t.Fatalf("CommentEdit: %v", err)
	}
	if !edited.Edited() {
		t.Error("an edited comment does not report itself edited")
	}

	if err := api.CommentDelete(t.Context(), comment.ID); err != nil {
		t.Fatalf("CommentDelete: %v", err)
	}
	reloaded, err := api.ShowIssue(t.Context(), issue.ID)
	if err != nil {
		t.Fatalf("ShowIssue: %v", err)
	}
	if len(reloaded.Comments) != 0 {
		t.Errorf("Comments = %+v, want none", reloaded.Comments)
	}
}

func TestTaxonomyReads(t *testing.T) {
	t.Parallel()
	_, api := dbtest.API(t)

	if _, err := api.Add(t.Context(), contract.IssueAddParams{
		Project:   "tt",
		Title:     "work",
		Labels:    []string{"bug"},
		Milestone: "v1",
	}); err != nil {
		t.Fatalf("Add: %v", err)
	}

	labels, err := api.ListLabels(t.Context())
	if err != nil {
		t.Fatalf("ListLabels: %v", err)
	}
	if len(labels) != 1 || labels[0].Name != "bug" {
		t.Errorf("ListLabels = %+v, want just bug", labels)
	}

	milestones, err := api.ListMilestones(t.Context(), "tt")
	if err != nil {
		t.Fatalf("ListMilestones: %v", err)
	}
	if len(milestones) != 1 || milestones[0].Name != "v1" {
		t.Errorf("ListMilestones = %+v, want just v1", milestones)
	}
}

// The four sentinels are the whole vocabulary a frontend matches on.
func TestSentinelsReachTheFrontend(t *testing.T) {
	t.Parallel()
	_, api := dbtest.API(t)

	if _, err := api.ShowIssue(t.Context(), 999); !errors.Is(err, errs.ErrNotFound) {
		t.Errorf("ShowIssue = %v, want ErrNotFound", err)
	}
	if _, err := api.ShowProject(t.Context(), "nope"); !errors.Is(err, errs.ErrNotFound) {
		t.Errorf("ShowProject = %v, want ErrNotFound", err)
	}
	if _, err := api.Add(t.Context(), contract.IssueAddParams{Title: "no project"}); !errors.Is(err, errs.ErrInvalid) {
		t.Errorf("Add = %v, want ErrInvalid", err)
	}
}
