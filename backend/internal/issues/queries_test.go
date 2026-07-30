package issues_test

import (
	"testing"

	"github.com/Plabrum/tt/backend/internal/dbtest"
	"github.com/Plabrum/tt/backend/internal/ent"
	"github.com/Plabrum/tt/backend/internal/issues"
	"github.com/Plabrum/tt/backend/internal/projects"
	"github.com/Plabrum/tt/backend/models"
)

// ids is what a list assertion compares, so a failure names the rows rather
// than dumping two graphs.
func ids(list []models.Issue) []int {
	out := make([]int, 0, len(list))
	for _, i := range list {
		out = append(out, i.ID)
	}
	return out
}

func equal(a, b []int) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

func addTo(t *testing.T, cl *ent.Client, project, title string) models.Issue {
	t.Helper()
	created, err := issues.Create(t.Context(), cl, models.IssueAddParams{Project: project, Title: title})
	if err != nil {
		t.Fatalf("creating issue %q: %v", title, err)
	}
	return created
}

func list(t *testing.T, cl *ent.Client, p models.IssueListParams) []models.Issue {
	t.Helper()
	found, err := issues.List(t.Context(), cl, p)
	if err != nil {
		t.Fatalf("List: %v", err)
	}
	return found
}

// The default hides done work.
func TestListDefaultsToOpenWork(t *testing.T) {
	t.Parallel()
	cl := dbtest.Client(t)

	open := addTo(t, cl, "tt", "open")
	done := addTo(t, cl, "tt", "done")
	if _, err := issues.Close(t.Context(), cl, done.ID); err != nil {
		t.Fatalf("Close: %v", err)
	}

	if got := ids(list(t, cl, models.IssueListParams{})); !equal(got, []int{open.ID}) {
		t.Errorf("default list = %v, want %v", got, []int{open.ID})
	}

	all := models.IssueListParams{Statuses: []models.IssueStatus{
		models.IssueTodo, models.IssueDoing, models.IssueDone,
	}}
	if got := ids(list(t, cl, all)); len(got) != 2 {
		t.Errorf("list of every status = %v, want both issues", got)
	}
}

// Archiving is what takes a project's work out of the way, so an unscoped list
// is over the active projects — but naming one reaches into it regardless.
func TestListExcludesArchivedProjects(t *testing.T) {
	t.Parallel()
	cl := dbtest.Client(t)

	kept := addTo(t, cl, "tt", "kept")
	hidden := addTo(t, cl, "old", "hidden")
	if _, err := projects.Archive(t.Context(), cl, "old"); err != nil {
		t.Fatalf("Archive: %v", err)
	}

	if got := ids(list(t, cl, models.IssueListParams{})); !equal(got, []int{kept.ID}) {
		t.Errorf("unscoped list = %v, want %v", got, []int{kept.ID})
	}

	scoped := ids(list(t, cl, models.IssueListParams{Project: "old"}))
	if !equal(scoped, []int{hidden.ID}) {
		t.Errorf("list of the archived project = %v, want %v", scoped, []int{hidden.ID})
	}

	// Restoring puts the work back in view.
	if _, err := projects.Restore(t.Context(), cl, "old"); err != nil {
		t.Fatalf("Restore: %v", err)
	}
	if got := ids(list(t, cl, models.IssueListParams{})); len(got) != 2 {
		t.Errorf("list after restoring = %v, want both issues", got)
	}
}

func TestListFilters(t *testing.T) {
	t.Parallel()
	cl := dbtest.Client(t)

	plain := addTo(t, cl, "tt", "plain work")
	tagged := addTo(t, cl, "tt", "tagged work")
	elsewhere := addTo(t, cl, "other", "elsewhere")

	if _, err := issues.AddLabel(t.Context(), cl, tagged.ID, "bug"); err != nil {
		t.Fatalf("AddLabel: %v", err)
	}
	if _, err := issues.SetMilestone(t.Context(), cl, tagged.ID, "v1"); err != nil {
		t.Fatalf("SetMilestone: %v", err)
	}

	blocked := addTo(t, cl, "tt", "blocked")
	if _, err := issues.AddDep(t.Context(), cl, blocked.ID, plain.ID); err != nil {
		t.Fatalf("AddDep: %v", err)
	}

	tests := []struct {
		name   string
		params models.IssueListParams
		want   []int
	}{
		{"by project", models.IssueListParams{Project: "other"}, []int{elsewhere.ID}},
		{"by label", models.IssueListParams{Labels: []string{"bug"}}, []int{tagged.ID}},
		{"by milestone", models.IssueListParams{Milestone: "v1"}, []int{tagged.ID}},
		{"by title, case-insensitively", models.IssueListParams{Search: "TAGGED"}, []int{tagged.ID}},
		{"blocked only", models.IssueListParams{BlockedOnly: true}, []int{blocked.ID}},
		{"a label nothing carries", models.IssueListParams{Labels: []string{"nope"}}, []int{}},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			if got := ids(list(t, cl, tt.params)); !equal(got, tt.want) {
				t.Errorf("list = %v, want %v", got, tt.want)
			}
		})
	}
}

// Labels are ANDed: two of them means both, not either.
func TestListLabelsAreAnded(t *testing.T) {
	t.Parallel()
	cl := dbtest.Client(t)

	both := addTo(t, cl, "tt", "both")
	one := addTo(t, cl, "tt", "one")
	for _, name := range []string{"bug", "backend"} {
		if _, err := issues.AddLabel(t.Context(), cl, both.ID, name); err != nil {
			t.Fatalf("AddLabel %q: %v", name, err)
		}
	}
	if _, err := issues.AddLabel(t.Context(), cl, one.ID, "bug"); err != nil {
		t.Fatalf("AddLabel: %v", err)
	}

	got := ids(list(t, cl, models.IssueListParams{Labels: []string{"bug", "backend"}}))
	if !equal(got, []int{both.ID}) {
		t.Errorf("list = %v, want %v", got, []int{both.ID})
	}
}

// High priority first, then oldest first.
func TestListPickOrder(t *testing.T) {
	t.Parallel()
	cl := dbtest.Client(t)

	first := addTo(t, cl, "tt", "first")
	second := addTo(t, cl, "tt", "second")
	third := addTo(t, cl, "tt", "third")
	if _, err := issues.SetPriority(t.Context(), cl, third.ID, models.PriorityHi); err != nil {
		t.Fatalf("SetPriority: %v", err)
	}

	want := []int{third.ID, first.ID, second.ID}
	if got := ids(list(t, cl, models.IssueListParams{})); !equal(got, want) {
		t.Errorf("pick order = %v, want %v", got, want)
	}
}

func TestListLimit(t *testing.T) {
	t.Parallel()
	cl := dbtest.Client(t)

	for _, title := range []string{"a", "b", "c"} {
		addTo(t, cl, "tt", title)
	}
	if got := list(t, cl, models.IssueListParams{Limit: 2}); len(got) != 2 {
		t.Errorf("limited list has %d rows, want 2", len(got))
	}
}

// A list row carries a menu as correct as a detail view's, because the loader
// eager-loads the edges the rules read.
func TestListRowsCarryTheirMenu(t *testing.T) {
	t.Parallel()
	cl := dbtest.Client(t)

	work := addTo(t, cl, "tt", "work")
	blocker := addTo(t, cl, "tt", "blocker")
	if _, err := issues.AddDep(t.Context(), cl, work.ID, blocker.ID); err != nil {
		t.Fatalf("AddDep: %v", err)
	}

	for _, row := range list(t, cl, models.IssueListParams{}) {
		if row.ID != work.ID {
			continue
		}
		if !row.Blocked {
			t.Error("Blocked = false on a list row with an open blocker")
		}
		action, ok := find(row.Actions, models.KeyIssueStart)
		if !ok {
			t.Fatal("a list row withholds start entirely")
		}
		if action.Runnable() {
			t.Error("start is runnable on a blocked list row")
		}
		if _, ok := find(row.Actions, models.KeyIssueRemoveDep); !ok {
			t.Error("a list row withholds remove-dep despite having a blocker")
		}
	}
}

// Loading stops one level down, so a detail view cannot recurse forever.
func TestLoadStopsOneLevelDown(t *testing.T) {
	t.Parallel()
	cl := dbtest.Client(t)

	grandparent := addTo(t, cl, "tt", "grandparent")
	parent, err := issues.AddSubIssue(t.Context(), cl, grandparent.ID, models.IssueAddParams{Title: "parent"})
	if err != nil {
		t.Fatalf("AddSubIssue: %v", err)
	}
	if _, err := issues.AddSubIssue(t.Context(), cl, parent.ID, models.IssueAddParams{Title: "child"}); err != nil {
		t.Fatalf("AddSubIssue: %v", err)
	}

	loaded, err := issues.Load(t.Context(), cl, parent.ID)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if loaded.Parent == nil {
		t.Fatal("Parent is nil")
	}
	if len(loaded.Subtasks) != 1 {
		t.Fatalf("Subtasks = %+v, want one", loaded.Subtasks)
	}
	// The nested issues carry their own scalars and menu, and nothing deeper.
	if len(loaded.Parent.Subtasks) != 0 {
		t.Errorf("Parent.Subtasks = %+v, want empty", loaded.Parent.Subtasks)
	}
	if len(loaded.Subtasks[0].Subtasks) != 0 {
		t.Errorf("Subtasks[0].Subtasks = %+v, want empty", loaded.Subtasks[0].Subtasks)
	}
	if loaded.Subtasks[0].Project.Slug == "" {
		t.Error("a nested issue has no project")
	}
	if len(loaded.Subtasks[0].Actions) == 0 {
		t.Error("a nested issue has no menu")
	}
}

// Every slice is non-nil, so --json prints [] and never null.
func TestSlicesAreNeverNil(t *testing.T) {
	t.Parallel()
	cl := dbtest.Client(t)

	issue := addTo(t, cl, "tt", "bare")
	loaded, err := issues.Load(t.Context(), cl, issue.ID)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}

	if loaded.Labels == nil || loaded.Subtasks == nil || loaded.Blockers == nil ||
		loaded.Blocks == nil || loaded.Comments == nil {
		t.Errorf("a nil slice on a bare issue: %+v", loaded)
	}
	if loaded.Parent != nil {
		t.Errorf("Parent = %+v, want nil", loaded.Parent)
	}
	if loaded.Milestone != nil {
		t.Errorf("Milestone = %+v, want nil", loaded.Milestone)
	}

	empty, err := issues.List(t.Context(), cl, models.IssueListParams{Search: "nothing matches"})
	if err != nil {
		t.Fatalf("List: %v", err)
	}
	if empty == nil {
		t.Error("an empty list is nil, want an empty slice")
	}
}
