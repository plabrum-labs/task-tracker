package issues_test

import (
	"errors"
	"testing"

	"github.com/Plabrum/tt/backend/contract"
	"github.com/Plabrum/tt/backend/errs"
	"github.com/Plabrum/tt/backend/internal/actions"
	"github.com/Plabrum/tt/backend/internal/dbtest"
	"github.com/Plabrum/tt/backend/internal/ent"
	"github.com/Plabrum/tt/backend/internal/issues"
)

// The same rule is checked twice: once to build the offered, once here against
// live rows. actions_test.go covers the offered half; this file covers the write
// refusing what the offered refused.

func add(t *testing.T, cl *ent.Client, title string) contract.Issue {
	t.Helper()
	created, err := issues.Create(t.Context(), cl, contract.IssueAddParams{Project: "tt", Title: title})
	if err != nil {
		t.Fatalf("creating issue %q: %v", title, err)
	}
	return created
}

func closeIssue(t *testing.T, cl *ent.Client, id int) contract.Issue {
	t.Helper()
	closed, err := issues.Run(t.Context(), cl, id, issues.Close, actions.None{})
	if err != nil {
		t.Fatalf("closing issue %d: %v", id, err)
	}
	return closed
}

func TestCreate(t *testing.T) {
	t.Parallel()
	cl := dbtest.Client(t)

	created, err := issues.Create(t.Context(), cl, contract.IssueAddParams{
		Project:   "My Repo",
		Title:     "  ship it  ",
		Body:      "the body",
		Priority:  contract.PriorityHi,
		Labels:    []string{"Bug"},
		Milestone: "V1",
	})
	if err != nil {
		t.Fatalf("Create: %v", err)
	}

	if created.Title != "ship it" {
		t.Errorf("Title = %q, want %q", created.Title, "ship it")
	}
	if created.Status != contract.IssueTodo {
		t.Errorf("Status = %q, want %q", created.Status, contract.IssueTodo)
	}
	// The slug and the taxonomy names are normalised on the way in, so the same
	// project cannot arrive twice under two spellings.
	if created.Project.Slug != "my-repo" {
		t.Errorf("Project.Slug = %q, want %q", created.Project.Slug, "my-repo")
	}
	if len(created.Labels) != 1 || created.Labels[0].Name != "bug" {
		t.Errorf("Labels = %+v, want one label named bug", created.Labels)
	}
	if created.Milestone == nil || created.Milestone.Name != "v1" {
		t.Errorf("Milestone = %+v, want v1", created.Milestone)
	}
}

func TestCreateRejectsBadParams(t *testing.T) {
	t.Parallel()
	cl := dbtest.Client(t)

	tests := []struct {
		name   string
		params contract.IssueAddParams
	}{
		{"no project", contract.IssueAddParams{Title: "x"}},
		{"no title", contract.IssueAddParams{Project: "tt"}},
		{"blank title", contract.IssueAddParams{Project: "tt", Title: "   "}},
		{"unknown priority", contract.IssueAddParams{Project: "tt", Title: "x", Priority: "urgent"}},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			if _, err := issues.Create(t.Context(), cl, tt.params); !errors.Is(err, errs.ErrInvalid) {
				t.Errorf("Create error = %v, want ErrInvalid", err)
			}
		})
	}
}

// A rejected issue takes its container with it: capture should never leave
// behind an empty project for work that was never written.
func TestCreateRollsBackTheProject(t *testing.T) {
	t.Parallel()
	cl := dbtest.Client(t)

	_, err := issues.Create(t.Context(), cl, contract.IssueAddParams{
		Project:  "brand-new",
		Title:    "x",
		Priority: "urgent",
	})
	if err == nil {
		t.Fatal("Create succeeded, want an error")
	}
	n, err := cl.Project.Query().Count(t.Context())
	if err != nil {
		t.Fatalf("counting projects: %v", err)
	}
	if n != 0 {
		t.Errorf("projects = %d, want 0", n)
	}
}

func TestStartRefusedWhileBlocked(t *testing.T) {
	t.Parallel()
	cl := dbtest.Client(t)

	work := add(t, cl, "work")
	blocker := add(t, cl, "blocker")
	if _, err := issues.Run(t.Context(), cl, work.ID, issues.AddDep, blocker.ID); err != nil {
		t.Fatalf("AddDep: %v", err)
	}

	_, err := issues.Run(t.Context(), cl, work.ID, issues.Start, actions.None{})
	if !errors.Is(err, errs.ErrConflict) {
		t.Fatalf("Start error = %v, want ErrConflict", err)
	}

	// Finishing the blocker is what unblocks it, and the offered agrees.
	closeIssue(t, cl, blocker.ID)
	reloaded, err := issues.Load(t.Context(), cl, work.ID)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if reloaded.Blocked {
		t.Error("Blocked = true after the blocker closed")
	}
	if _, err := issues.Run(t.Context(), cl, work.ID, issues.Start, actions.None{}); err != nil {
		t.Errorf("Start after unblocking: %v", err)
	}
}

func TestCloseRefusedWithOpenSubIssues(t *testing.T) {
	t.Parallel()
	cl := dbtest.Client(t)

	parent := add(t, cl, "parent")
	child, err := issues.Run(t.Context(), cl, parent.ID, issues.AddSubIssue, contract.IssueAddParams{Title: "child"})
	if err != nil {
		t.Fatalf("AddSubIssue: %v", err)
	}

	_, err = issues.Run(t.Context(), cl, parent.ID, issues.Close, actions.None{})
	if !errors.Is(err, errs.ErrConflict) {
		t.Fatalf("Close error = %v, want ErrConflict", err)
	}

	closeIssue(t, cl, child.ID)
	if _, err := issues.Run(t.Context(), cl, parent.ID, issues.Close, actions.None{}); err != nil {
		t.Errorf("Close after the sub-issue closed: %v", err)
	}
}

// A sub-issue has exactly one parent, and it lands in its parent's project
// whatever the params say.
func TestAddSubIssue(t *testing.T) {
	t.Parallel()
	cl := dbtest.Client(t)

	parent := add(t, cl, "parent")
	child, err := issues.Run(t.Context(), cl, parent.ID, issues.AddSubIssue, contract.IssueAddParams{
		Project: "somewhere-else",
		Title:   "child",
	})
	if err != nil {
		t.Fatalf("AddSubIssue: %v", err)
	}
	if child.Project.Slug != parent.Project.Slug {
		t.Errorf("child project = %q, want the parent's %q", child.Project.Slug, parent.Project.Slug)
	}
	if child.Parent == nil || child.Parent.ID != parent.ID {
		t.Fatalf("child Parent = %+v, want issue %d", child.Parent, parent.ID)
	}

	reloaded, err := issues.Load(t.Context(), cl, parent.ID)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if len(reloaded.Subtasks) != 1 || reloaded.Subtasks[0].ID != child.ID {
		t.Errorf("parent Subtasks = %+v, want just issue %d", reloaded.Subtasks, child.ID)
	}
}

func TestTransitionsRefusedOutOfOrder(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name string
		// setup leaves the issue in the state the write should be refused from.
		setup  func(t *testing.T, cl *ent.Client, id int)
		action *issues.Action[actions.None]
	}{
		{
			name:   "start on a done issue",
			setup:  func(t *testing.T, cl *ent.Client, id int) { t.Helper(); closeIssue(t, cl, id) },
			action: issues.Start,
		},
		{
			name:   "close on a done issue",
			setup:  func(t *testing.T, cl *ent.Client, id int) { t.Helper(); closeIssue(t, cl, id) },
			action: issues.Close,
		},
		{
			name:   "reopen on a todo issue",
			setup:  func(_ *testing.T, _ *ent.Client, _ int) {},
			action: issues.Reopen,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			cl := dbtest.Client(t)
			issue := add(t, cl, "work")
			tt.setup(t, cl, issue.ID)

			if _, err := issues.Run(t.Context(), cl, issue.ID, tt.action, actions.None{}); !errors.Is(err, errs.ErrConflict) {
				t.Errorf("error = %v, want ErrConflict", err)
			}
		})
	}
}

func TestCloseAndReopenMoveClosedAt(t *testing.T) {
	t.Parallel()
	cl := dbtest.Client(t)

	issue := add(t, cl, "work")
	closed := closeIssue(t, cl, issue.ID)
	if closed.ClosedAt == nil {
		t.Fatal("ClosedAt is nil after closing")
	}

	reopened, err := issues.Run(t.Context(), cl, issue.ID, issues.Reopen, actions.None{})
	if err != nil {
		t.Fatalf("Reopen: %v", err)
	}
	if reopened.ClosedAt != nil {
		t.Errorf("ClosedAt = %v after reopening, want nil", reopened.ClosedAt)
	}
	if reopened.Status != contract.IssueTodo {
		t.Errorf("Status = %q, want %q", reopened.Status, contract.IssueTodo)
	}
}

func TestLabels(t *testing.T) {
	t.Parallel()
	cl := dbtest.Client(t)

	issue := add(t, cl, "work")

	// Nothing to remove yet, so the no action it and the write refuses.
	if _, err := issues.Run(t.Context(), cl, issue.ID, issues.RemoveLabel, "bug"); !errors.Is(err, errs.ErrConflict) {
		t.Errorf("RemoveLabel with no labels = %v, want ErrConflict", err)
	}

	labelled, err := issues.Run(t.Context(), cl, issue.ID, issues.AddLabel, "Bug")
	if err != nil {
		t.Fatalf("AddLabel: %v", err)
	}
	if len(labelled.Labels) != 1 || labelled.Labels[0].Name != "bug" {
		t.Fatalf("Labels = %+v, want one label named bug", labelled.Labels)
	}

	// Adding it twice is not an error: the label is on the issue either way.
	again, err := issues.Run(t.Context(), cl, issue.ID, issues.AddLabel, "bug")
	if err != nil {
		t.Fatalf("AddLabel twice: %v", err)
	}
	if len(again.Labels) != 1 {
		t.Errorf("Labels = %+v after adding twice, want one", again.Labels)
	}

	bare, err := issues.Run(t.Context(), cl, issue.ID, issues.RemoveLabel, "bug")
	if err != nil {
		t.Fatalf("RemoveLabel: %v", err)
	}
	if len(bare.Labels) != 0 {
		t.Errorf("Labels = %+v, want none", bare.Labels)
	}
}

func TestDeps(t *testing.T) {
	t.Parallel()
	cl := dbtest.Client(t)

	work := add(t, cl, "work")
	blocker := add(t, cl, "blocker")

	// Nothing to remove yet.
	if _, err := issues.Run(t.Context(), cl, work.ID, issues.RemoveDep, blocker.ID); !errors.Is(err, errs.ErrConflict) {
		t.Errorf("RemoveDep with no dependencies = %v, want ErrConflict", err)
	}

	linked, err := issues.Run(t.Context(), cl, work.ID, issues.AddDep, blocker.ID)
	if err != nil {
		t.Fatalf("AddDep: %v", err)
	}
	if len(linked.Blockers) != 1 || linked.Blockers[0].ID != blocker.ID {
		t.Fatalf("Blockers = %+v, want issue %d", linked.Blockers, blocker.ID)
	}
	if !linked.Blocked {
		t.Error("Blocked = false with an open blocker")
	}

	// The reverse edge is the same row read the other way.
	other, err := issues.Load(t.Context(), cl, blocker.ID)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if len(other.Blocks) != 1 || other.Blocks[0].ID != work.ID {
		t.Errorf("Blocks = %+v, want issue %d", other.Blocks, work.ID)
	}

	if _, err := issues.Run(t.Context(), cl, work.ID, issues.AddDep, blocker.ID); !errors.Is(err, errs.ErrConflict) {
		t.Errorf("AddDep twice = %v, want ErrConflict", err)
	}

	dropped, err := issues.Run(t.Context(), cl, work.ID, issues.RemoveDep, blocker.ID)
	if err != nil {
		t.Fatalf("RemoveDep: %v", err)
	}
	if len(dropped.Blockers) != 0 {
		t.Errorf("Blockers = %+v, want none", dropped.Blockers)
	}
}

// The cycle check walks the chain, so it is not a offered rule.
func TestAddDepRefusesCycles(t *testing.T) {
	t.Parallel()
	cl := dbtest.Client(t)

	a := add(t, cl, "a")
	b := add(t, cl, "b")
	c := add(t, cl, "c")

	if _, err := issues.Run(t.Context(), cl, a.ID, issues.AddDep, b.ID); err != nil {
		t.Fatalf("AddDep a->b: %v", err)
	}
	if _, err := issues.Run(t.Context(), cl, b.ID, issues.AddDep, c.ID); err != nil {
		t.Fatalf("AddDep b->c: %v", err)
	}

	tests := []struct {
		name          string
		id, blockerID int
	}{
		{"itself", a.ID, a.ID},
		{"one hop", b.ID, a.ID},
		{"through the chain", c.ID, a.ID},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			if _, err := issues.Run(t.Context(), cl, tt.id, issues.AddDep, tt.blockerID); !errors.Is(err, errs.ErrConflict) {
				t.Errorf("AddDep error = %v, want ErrConflict", err)
			}
		})
	}
}

func TestEdit(t *testing.T) {
	t.Parallel()
	cl := dbtest.Client(t)

	issue := add(t, cl, "before")
	title := "after"
	edited, err := issues.Run(t.Context(), cl, issue.ID, issues.Edit, contract.IssueEditParams{Title: &title})
	if err != nil {
		t.Fatalf("Edit: %v", err)
	}
	if edited.Title != "after" {
		t.Errorf("Title = %q, want %q", edited.Title, "after")
	}

	blank := "   "
	if _, err := issues.Run(t.Context(), cl, issue.ID, issues.Edit, contract.IssueEditParams{Title: &blank}); !errors.Is(err, errs.ErrInvalid) {
		t.Errorf("Edit to a blank title = %v, want ErrInvalid", err)
	}
	if _, err := issues.Run(t.Context(), cl, issue.ID, issues.Edit, contract.IssueEditParams{}); !errors.Is(err, errs.ErrInvalid) {
		t.Errorf("Edit with nothing set = %v, want ErrInvalid", err)
	}
}

// Deleting a parent should not delete work nobody asked to lose.
func TestDeleteKeepsSubIssues(t *testing.T) {
	t.Parallel()
	cl := dbtest.Client(t)

	parent := add(t, cl, "parent")
	child, err := issues.Run(t.Context(), cl, parent.ID, issues.AddSubIssue, contract.IssueAddParams{Title: "child"})
	if err != nil {
		t.Fatalf("AddSubIssue: %v", err)
	}

	if _, err := issues.Run(t.Context(), cl, parent.ID, issues.Delete, actions.None{}); err != nil {
		t.Fatalf("Delete: %v", err)
	}
	if _, err := issues.Load(t.Context(), cl, parent.ID); !errors.Is(err, errs.ErrNotFound) {
		t.Errorf("Load of the deleted parent = %v, want ErrNotFound", err)
	}

	orphan, err := issues.Load(t.Context(), cl, child.ID)
	if err != nil {
		t.Fatalf("Load of the child: %v", err)
	}
	if orphan.Parent != nil {
		t.Errorf("child Parent = %+v, want nil", orphan.Parent)
	}
}

func TestMissingIDsAreNotFound(t *testing.T) {
	t.Parallel()
	cl := dbtest.Client(t)

	if _, err := issues.Load(t.Context(), cl, 999); !errors.Is(err, errs.ErrNotFound) {
		t.Errorf("Load = %v, want ErrNotFound", err)
	}
	if _, err := issues.Run(t.Context(), cl, 999, issues.Start, actions.None{}); !errors.Is(err, errs.ErrNotFound) {
		t.Errorf("Start = %v, want ErrNotFound", err)
	}
	if _, err := issues.Run(t.Context(), cl, 999, issues.Delete, actions.None{}); !errors.Is(err, errs.ErrNotFound) {
		t.Errorf("Delete = %v, want ErrNotFound", err)
	}
}

func TestSetPriorityAndMilestone(t *testing.T) {
	t.Parallel()
	cl := dbtest.Client(t)

	issue := add(t, cl, "work")

	bumped, err := issues.Run(t.Context(), cl, issue.ID, issues.SetPriority, contract.PriorityHi)
	if err != nil {
		t.Fatalf("SetPriority: %v", err)
	}
	if bumped.Priority != contract.PriorityHi {
		t.Errorf("Priority = %q, want %q", bumped.Priority, contract.PriorityHi)
	}
	if _, err := issues.Run(t.Context(), cl, issue.ID, issues.SetPriority, "urgent"); !errors.Is(err, errs.ErrInvalid) {
		t.Errorf("SetPriority to an unknown value = %v, want ErrInvalid", err)
	}

	filed, err := issues.Run(t.Context(), cl, issue.ID, issues.SetMilestone, "V1")
	if err != nil {
		t.Fatalf("SetMilestone: %v", err)
	}
	if filed.Milestone == nil || filed.Milestone.Name != "v1" {
		t.Fatalf("Milestone = %+v, want v1", filed.Milestone)
	}

	cleared, err := issues.Run(t.Context(), cl, issue.ID, issues.SetMilestone, "")
	if err != nil {
		t.Fatalf("SetMilestone to empty: %v", err)
	}
	if cleared.Milestone != nil {
		t.Errorf("Milestone = %+v, want nil", cleared.Milestone)
	}
}

func TestComment(t *testing.T) {
	t.Parallel()
	cl := dbtest.Client(t)

	issue := add(t, cl, "work")
	commented, err := issues.Run(t.Context(), cl, issue.ID, issues.Comment, "  a note  ")
	if err != nil {
		t.Fatalf("Comment: %v", err)
	}
	if len(commented.Comments) != 1 {
		t.Fatalf("Comments = %+v, want one", commented.Comments)
	}
	if commented.Comments[0].Body != "a note" {
		t.Errorf("Body = %q, want %q", commented.Comments[0].Body, "a note")
	}
	if commented.Comments[0].Edited() {
		t.Error("a fresh comment reports itself edited")
	}

	if _, err := issues.Run(t.Context(), cl, issue.ID, issues.Comment, "   "); !errors.Is(err, errs.ErrInvalid) {
		t.Errorf("Comment with a blank body = %v, want ErrInvalid", err)
	}
}
