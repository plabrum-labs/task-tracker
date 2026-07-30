package query_test

import (
	"context"
	"slices"
	"testing"
	"time"

	"github.com/Plabrum/tt/ent"
	entissue "github.com/Plabrum/tt/ent/issue"
	entref "github.com/Plabrum/tt/ent/ref"
	"github.com/Plabrum/tt/internal/dbtest"
	"github.com/Plabrum/tt/internal/issue"
	"github.com/Plabrum/tt/internal/query"
)

func newService(t *testing.T) (*ent.Client, *query.Service) {
	t.Helper()
	client := dbtest.Client(t)
	return client, query.NewService(client)
}

func ids(rows []query.Row) []int {
	out := make([]int, len(rows))
	for i, r := range rows {
		out[i] = r.ID
	}
	return out
}

func TestListScopesToProject(t *testing.T) {
	t.Parallel()
	ctx := context.Background()
	client, svc := newService(t)

	tt := client.Project.Create().SetSlug("tt").SaveX(ctx)
	other := client.Project.Create().SetSlug("other").SaveX(ctx)
	mine := client.Issue.Create().SetTitle("mine").SetProject(tt).SaveX(ctx)
	theirs := client.Issue.Create().SetTitle("theirs").SetProject(other).SaveX(ctx)

	scoped, err := svc.List(ctx, query.ListParams{Project: "tt"})
	if err != nil {
		t.Fatalf("List: %v", err)
	}
	if got := ids(scoped); !slices.Equal(got, []int{mine.ID}) {
		t.Errorf("scoped ids = %v, want %v", got, []int{mine.ID})
	}
	if scoped[0].Project != "tt" {
		t.Errorf("project = %q, want %q", scoped[0].Project, "tt")
	}

	// "" is -A: no scoping at all, not a project literally named "".
	all, err := svc.List(ctx, query.ListParams{})
	if err != nil {
		t.Fatalf("List(-A): %v", err)
	}
	if got := ids(all); !slices.Equal(got, []int{mine.ID, theirs.ID}) {
		t.Errorf("-A ids = %v, want %v", got, []int{mine.ID, theirs.ID})
	}
}

func TestListDefaultStatusesHideDone(t *testing.T) {
	t.Parallel()
	ctx := context.Background()
	client, svc := newService(t)

	proj := client.Project.Create().SetSlug("tt").SaveX(ctx)
	mk := func(title string, st entissue.Status) *ent.Issue {
		return client.Issue.Create().SetTitle(title).SetProject(proj).SetStatus(st).SaveX(ctx)
	}
	todo := mk("todo", entissue.StatusTodo)
	doing := mk("doing", entissue.StatusDoing)
	done := mk("done", entissue.StatusDone)

	byDefault, err := svc.List(ctx, query.ListParams{Project: "tt"})
	if err != nil {
		t.Fatalf("List: %v", err)
	}
	if got, want := ids(byDefault), []int{todo.ID, doing.ID}; !slices.Equal(got, want) {
		t.Errorf("default ids = %v, want %v", got, want)
	}

	// -a passes all three explicitly.
	all, err := svc.List(ctx, query.ListParams{
		Project:  "tt",
		Statuses: []issue.Status{issue.StatusTodo, issue.StatusDoing, issue.StatusDone},
	})
	if err != nil {
		t.Fatalf("List(-a): %v", err)
	}
	if got, want := ids(all), []int{todo.ID, doing.ID, done.ID}; !slices.Equal(got, want) {
		t.Errorf("-a ids = %v, want %v", got, want)
	}

	// A single explicit status is what a board column asks for.
	onlyDone, err := svc.List(ctx, query.ListParams{
		Project: "tt", Statuses: []issue.Status{issue.StatusDone},
	})
	if err != nil {
		t.Fatalf("List(done): %v", err)
	}
	if got, want := ids(onlyDone), []int{done.ID}; !slices.Equal(got, want) {
		t.Errorf("done ids = %v, want %v", got, want)
	}
}

// TestListPickOrder is the regression test for ordering on the priority column
// directly. The enum stores as text, so DESC sorts "normal" above "hi" — the
// inverse of the intended order — and ASC is right only by the accident that
// "hi" sorts before "normal", which a third level would end. The fixture is
// built so both mistakes fail: the hi issue is also the newest, so it can only
// reach the top by priority.
func TestListPickOrder(t *testing.T) {
	t.Parallel()
	ctx := context.Background()
	client, svc := newService(t)

	proj := client.Project.Create().SetSlug("tt").SaveX(ctx)
	at := func(day int) time.Time { return time.Date(2026, 1, day, 0, 0, 0, 0, time.UTC) }
	mk := func(title string, pr entissue.Priority, created time.Time) *ent.Issue {
		return client.Issue.Create().
			SetTitle(title).SetProject(proj).SetPriority(pr).SetCreatedAt(created).SaveX(ctx)
	}

	oldNormal := mk("old normal", entissue.PriorityNormal, at(1))
	newNormal := mk("new normal", entissue.PriorityNormal, at(3))
	newHi := mk("new hi", entissue.PriorityHi, at(4))
	oldHi := mk("old hi", entissue.PriorityHi, at(2))

	rows, err := svc.List(ctx, query.ListParams{Project: "tt"})
	if err != nil {
		t.Fatalf("List: %v", err)
	}
	want := []int{oldHi.ID, newHi.ID, oldNormal.ID, newNormal.ID}
	if got := ids(rows); !slices.Equal(got, want) {
		t.Errorf("pick order = %v, want %v (hi first, then oldest first)", got, want)
	}
}

// TestListOrderIsStable: two issues sharing a created_at must still come back
// in the same order every time, because the TUI degrades its selection by id
// across refreshes.
func TestListOrderIsStable(t *testing.T) {
	t.Parallel()
	ctx := context.Background()
	client, svc := newService(t)

	proj := client.Project.Create().SetSlug("tt").SaveX(ctx)
	same := time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)
	var want []int
	for _, title := range []string{"a", "b", "c", "d"} {
		e := client.Issue.Create().
			SetTitle(title).SetProject(proj).SetCreatedAt(same).SaveX(ctx)
		want = append(want, e.ID)
	}

	for range 3 {
		rows, err := svc.List(ctx, query.ListParams{Project: "tt"})
		if err != nil {
			t.Fatalf("List: %v", err)
		}
		if got := ids(rows); !slices.Equal(got, want) {
			t.Errorf("ids = %v, want %v", got, want)
		}
	}
}

func TestListLabels(t *testing.T) {
	t.Parallel()
	ctx := context.Background()
	client, svc := newService(t)

	proj := client.Project.Create().SetSlug("tt").SaveX(ctx)
	backend := client.Label.Create().SetName("backend").SaveX(ctx)
	ui := client.Label.Create().SetName("ui").SaveX(ctx)
	// Exists, but nothing carries it — the "filters everything out" case below.
	client.Label.Create().SetName("bug").SaveX(ctx)

	// Attached out of alphabetical order, so the assertion tests the query's
	// ordering rather than insertion order.
	both := client.Issue.Create().
		SetTitle("both").SetProject(proj).AddLabels(ui, backend).SaveX(ctx)
	client.Issue.Create().
		SetTitle("one").SetProject(proj).AddLabels(backend).SaveX(ctx)
	client.Issue.Create().SetTitle("none").SetProject(proj).SaveX(ctx)

	rows, err := svc.List(ctx, query.ListParams{Project: "tt"})
	if err != nil {
		t.Fatalf("List: %v", err)
	}
	for _, r := range rows {
		if r.Labels == nil {
			t.Errorf("issue %d has nil labels, want an empty slice", r.ID)
		}
		if !slices.IsSorted(r.Labels) {
			t.Errorf("issue %d labels = %v, want them sorted", r.ID, r.Labels)
		}
		if r.Title == "both" && !slices.Equal(r.Labels, []string{"backend", "ui"}) {
			t.Errorf("labels = %v, want [backend ui]", r.Labels)
		}
		if r.Title == "none" && len(r.Labels) != 0 {
			t.Errorf("labels = %v, want empty", r.Labels)
		}
	}

	// -l is ANDed: two labels means both, not either.
	filtered, err := svc.List(ctx, query.ListParams{
		Project: "tt", Labels: []string{"backend", "ui"},
	})
	if err != nil {
		t.Fatalf("List(-l): %v", err)
	}
	if got, want := ids(filtered), []int{both.ID}; !slices.Equal(got, want) {
		t.Errorf("-l backend -l ui ids = %v, want %v", got, want)
	}

	// A label nothing carries filters everything out rather than erroring.
	none, err := svc.List(ctx, query.ListParams{Project: "tt", Labels: []string{"bug"}})
	if err != nil {
		t.Fatalf("List(-l bug): %v", err)
	}
	if len(none) != 0 {
		t.Errorf("-l bug rows = %v, want none", ids(none))
	}
}

func TestListMilestone(t *testing.T) {
	t.Parallel()
	ctx := context.Background()
	client, svc := newService(t)

	proj := client.Project.Create().SetSlug("tt").SaveX(ctx)
	v1 := client.Milestone.Create().SetName("v1").SetProject(proj).SaveX(ctx)
	inV1 := client.Issue.Create().SetTitle("in v1").SetProject(proj).SetMilestone(v1).SaveX(ctx)
	client.Issue.Create().SetTitle("unscheduled").SetProject(proj).SaveX(ctx)

	rows, err := svc.List(ctx, query.ListParams{Project: "tt"})
	if err != nil {
		t.Fatalf("List: %v", err)
	}
	for _, r := range rows {
		want := ""
		if r.ID == inV1.ID {
			want = "v1"
		}
		if r.Milestone != want {
			t.Errorf("issue %d milestone = %q, want %q", r.ID, r.Milestone, want)
		}
	}

	filtered, err := svc.List(ctx, query.ListParams{Project: "tt", Milestone: "v1"})
	if err != nil {
		t.Fatalf("List(-M): %v", err)
	}
	if got, want := ids(filtered), []int{inV1.ID}; !slices.Equal(got, want) {
		t.Errorf("-M v1 ids = %v, want %v", got, want)
	}
}

func TestListSubtaskRollup(t *testing.T) {
	t.Parallel()
	ctx := context.Background()
	client, svc := newService(t)

	proj := client.Project.Create().SetSlug("tt").SaveX(ctx)
	mk := func(title string, st entissue.Status) *ent.Issue {
		return client.Issue.Create().SetTitle(title).SetProject(proj).SetStatus(st).SaveX(ctx)
	}
	link := func(parent, child *ent.Issue, kind entref.Kind) {
		client.Ref.Create().SetBlocked(parent).SetBlocker(child).SetKind(kind).SaveX(ctx)
	}

	bare := mk("no children", entissue.StatusTodo)

	three := mk("three children", entissue.StatusTodo)
	link(three, mk("c1", entissue.StatusDone), entref.KindSubtask)
	link(three, mk("c2", entissue.StatusTodo), entref.KindSubtask)
	link(three, mk("c3", entissue.StatusDoing), entref.KindSubtask)

	// A dep is not a subtask, so it must not show up in the badge even though
	// it blocks identically.
	depOnly := mk("blocked by a dep", entissue.StatusTodo)
	link(depOnly, mk("prerequisite", entissue.StatusTodo), entref.KindDep)

	// One child under two parents counts toward both — the DAG behaviour a
	// tree could not express.
	parentA := mk("parent a", entissue.StatusTodo)
	parentB := mk("parent b", entissue.StatusTodo)
	shared := mk("shared child", entissue.StatusDone)
	link(parentA, shared, entref.KindSubtask)
	link(parentB, shared, entref.KindSubtask)

	rows, err := svc.List(ctx, query.ListParams{
		Project:  "tt",
		Statuses: []issue.Status{issue.StatusTodo, issue.StatusDoing, issue.StatusDone},
	})
	if err != nil {
		t.Fatalf("List: %v", err)
	}
	got := make(map[int]issue.Rollup, len(rows))
	for _, r := range rows {
		got[r.ID] = r.Subtasks
	}

	tests := []struct {
		name string
		id   int
		want issue.Rollup
	}{
		{"no children", bare.ID, issue.Rollup{}},
		{"three children, one done", three.ID, issue.Rollup{Done: 1, Total: 3}},
		{"a dep child is not a subtask", depOnly.ID, issue.Rollup{}},
		{"shared child counts for parent a", parentA.ID, issue.Rollup{Done: 1, Total: 1}},
		{"shared child counts for parent b", parentB.ID, issue.Rollup{Done: 1, Total: 1}},
		{"a child is not its own parent", shared.ID, issue.Rollup{}},
	}
	for _, tt := range tests {
		if got[tt.id] != tt.want {
			t.Errorf("%s: rollup = %+v, want %+v", tt.name, got[tt.id], tt.want)
		}
	}
}

func TestListBlocked(t *testing.T) {
	t.Parallel()
	ctx := context.Background()
	client, svc := newService(t)

	proj := client.Project.Create().SetSlug("tt").SaveX(ctx)
	mk := func(title string, st entissue.Status) *ent.Issue {
		return client.Issue.Create().SetTitle(title).SetProject(proj).SetStatus(st).SaveX(ctx)
	}
	link := func(blocked, blocker *ent.Issue, kind entref.Kind) {
		client.Ref.Create().SetBlocked(blocked).SetBlocker(blocker).SetKind(kind).SaveX(ctx)
	}

	free := mk("nothing in the way", entissue.StatusTodo)

	waiting := mk("waiting on a dep", entissue.StatusTodo)
	link(waiting, mk("open prerequisite", entissue.StatusDoing), entref.KindDep)

	cleared := mk("its blocker finished", entissue.StatusTodo)
	link(cleared, mk("finished prerequisite", entissue.StatusDone), entref.KindDep)

	// A subtask blocks its parent exactly as a dep does.
	decomposed := mk("has an open child", entissue.StatusTodo)
	link(decomposed, mk("open child", entissue.StatusTodo), entref.KindSubtask)

	rows, err := svc.List(ctx, query.ListParams{Project: "tt"})
	if err != nil {
		t.Fatalf("List: %v", err)
	}
	blocked := make(map[int]bool, len(rows))
	for _, r := range rows {
		blocked[r.ID] = r.Blocked
	}

	tests := []struct {
		name string
		id   int
		want bool
	}{
		{"no refs at all", free.ID, false},
		{"open dep blocks", waiting.ID, true},
		{"done dep does not block", cleared.ID, false},
		{"open subtask blocks", decomposed.ID, true},
	}
	for _, tt := range tests {
		if blocked[tt.id] != tt.want {
			t.Errorf("%s: blocked = %v, want %v", tt.name, blocked[tt.id], tt.want)
		}
	}

	// --blocked and the column are one predicate, so they must agree exactly.
	only, err := svc.List(ctx, query.ListParams{Project: "tt", BlockedOnly: true})
	if err != nil {
		t.Fatalf("List(--blocked): %v", err)
	}
	var wantIDs []int
	for _, r := range rows {
		if r.Blocked {
			wantIDs = append(wantIDs, r.ID)
		}
	}
	if got := ids(only); !slices.Equal(got, wantIDs) {
		t.Errorf("--blocked ids = %v, want %v (the rows whose column is set)", got, wantIDs)
	}
	for _, r := range only {
		if !r.Blocked {
			t.Errorf("issue %d came back from --blocked with Blocked = false", r.ID)
		}
	}
}

func TestListSearch(t *testing.T) {
	t.Parallel()
	ctx := context.Background()
	client, svc := newService(t)

	proj := client.Project.Create().SetSlug("tt").SaveX(ctx)
	wanted := client.Issue.Create().
		SetTitle("Wire The Schema").
		SetBody("this body mentions widgets").
		SetProject(proj).SaveX(ctx)
	client.Issue.Create().SetTitle("something else").SetProject(proj).SaveX(ctx)

	for _, term := range []string{"wire", "WIRE THE", "the schema"} {
		rows, err := svc.List(ctx, query.ListParams{Project: "tt", Search: term})
		if err != nil {
			t.Fatalf("List(%q): %v", term, err)
		}
		if got, want := ids(rows), []int{wanted.ID}; !slices.Equal(got, want) {
			t.Errorf("search %q ids = %v, want %v", term, got, want)
		}
	}

	// Title only: a body match is not a hit, because Row does not carry the
	// body and a substring inside a paragraph is not what a two-word search
	// means.
	rows, err := svc.List(ctx, query.ListParams{Project: "tt", Search: "widgets"})
	if err != nil {
		t.Fatalf("List(widgets): %v", err)
	}
	if len(rows) != 0 {
		t.Errorf("body search matched %v, want no rows", ids(rows))
	}
}

func TestListLimit(t *testing.T) {
	t.Parallel()
	ctx := context.Background()
	client, svc := newService(t)

	proj := client.Project.Create().SetSlug("tt").SaveX(ctx)
	at := func(day int) time.Time { return time.Date(2026, 1, day, 0, 0, 0, 0, time.UTC) }
	var all []int
	for i, title := range []string{"first", "second", "third"} {
		e := client.Issue.Create().
			SetTitle(title).SetProject(proj).SetCreatedAt(at(i + 1)).SaveX(ctx)
		all = append(all, e.ID)
	}

	// The limit takes the head of the pick order, not an arbitrary page.
	rows, err := svc.List(ctx, query.ListParams{Project: "tt", Limit: 2})
	if err != nil {
		t.Fatalf("List(limit): %v", err)
	}
	if got := ids(rows); !slices.Equal(got, all[:2]) {
		t.Errorf("limited ids = %v, want %v", got, all[:2])
	}

	unlimited, err := svc.List(ctx, query.ListParams{Project: "tt", Limit: 0})
	if err != nil {
		t.Fatalf("List(no limit): %v", err)
	}
	if got := ids(unlimited); !slices.Equal(got, all) {
		t.Errorf("unlimited ids = %v, want %v", got, all)
	}
}

// TestListEmptyIsNotNil: `tt ls --json` on a fresh database must print [].
func TestListEmptyIsNotNil(t *testing.T) {
	t.Parallel()
	ctx := context.Background()
	_, svc := newService(t)

	rows, err := svc.List(ctx, query.ListParams{Project: "nothing-here"})
	if err != nil {
		t.Fatalf("List: %v", err)
	}
	if rows == nil {
		t.Error("rows = nil, want an empty non-nil slice")
	}
	if len(rows) != 0 {
		t.Errorf("rows = %v, want none", ids(rows))
	}
}
