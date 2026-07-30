package dbtest_test

import (
	"testing"
	"time"

	"github.com/Plabrum/tt/backend/internal/dbtest"
)

// TimeMixin's create hook is what makes "created and never touched since" a
// question the two stamps can answer. It lives on the mixin, so it is tested
// against every entity carrying the mixin rather than in whichever domain
// happened to need it first — backend/contract's Comment.Edited is the current
// caller, but the invariant is not comments'.
//
// The bug this covers is a race, not a constant: without the hook the skew is
// sub-microsecond and occasionally zero, so a version of this test that ran one
// entity once would pass most of the time.
func TestCreatedAndUpdatedMatchOnCreate(t *testing.T) {
	t.Parallel()
	cl := dbtest.Client(t)

	// Rows are written straight through the client rather than through the
	// domains: the hook belongs to the schema, and a domain that set the stamps
	// itself would hide its absence.
	project, err := cl.Project.Create().SetSlug("tt").Save(t.Context())
	if err != nil {
		t.Fatalf("creating project: %v", err)
	}
	issue, err := cl.Issue.Create().SetTitle("work").SetProjectID(project.ID).Save(t.Context())
	if err != nil {
		t.Fatalf("creating issue: %v", err)
	}
	blocker, err := cl.Issue.Create().SetTitle("blocker").SetProjectID(project.ID).Save(t.Context())
	if err != nil {
		t.Fatalf("creating blocker: %v", err)
	}
	comment, err := cl.Comment.Create().SetIssueID(issue.ID).SetAuthor("me").SetBody("x").Save(t.Context())
	if err != nil {
		t.Fatalf("creating comment: %v", err)
	}
	label, err := cl.Label.Create().SetName("bug").Save(t.Context())
	if err != nil {
		t.Fatalf("creating label: %v", err)
	}
	milestone, err := cl.Milestone.Create().SetName("v1").SetProjectID(project.ID).Save(t.Context())
	if err != nil {
		t.Fatalf("creating milestone: %v", err)
	}
	ref, err := cl.Ref.Create().SetBlockedID(issue.ID).SetBlockerID(blocker.ID).Save(t.Context())
	if err != nil {
		t.Fatalf("creating ref: %v", err)
	}

	stamps := []struct {
		entity           string
		created, updated time.Time
	}{
		{"project", project.CreatedAt, project.UpdatedAt},
		{"issue", issue.CreatedAt, issue.UpdatedAt},
		{"comment", comment.CreatedAt, comment.UpdatedAt},
		{"label", label.CreatedAt, label.UpdatedAt},
		{"milestone", milestone.CreatedAt, milestone.UpdatedAt},
		{"ref", ref.CreatedAt, ref.UpdatedAt},
	}
	for _, s := range stamps {
		if !s.updated.Equal(s.created) {
			t.Errorf("%s: updated_at %v is %v off created_at %v, want the same instant",
				s.entity, s.updated, s.updated.Sub(s.created), s.created)
		}
	}
}

// The other half: an update has to move updated_at forward, or the pair says
// nothing.
func TestUpdateMovesUpdatedAt(t *testing.T) {
	t.Parallel()
	cl := dbtest.Client(t)

	project, err := cl.Project.Create().SetSlug("tt").Save(t.Context())
	if err != nil {
		t.Fatalf("creating project: %v", err)
	}
	updated, err := project.Update().SetTitle("Task tracker").Save(t.Context())
	if err != nil {
		t.Fatalf("updating project: %v", err)
	}

	if !updated.UpdatedAt.After(updated.CreatedAt) {
		t.Errorf("updated_at %v is not after created_at %v", updated.UpdatedAt, updated.CreatedAt)
	}
	if !updated.CreatedAt.Equal(project.CreatedAt) {
		t.Errorf("created_at moved from %v to %v", project.CreatedAt, updated.CreatedAt)
	}
}

// A create that names its own created_at keeps it, and still comes out
// consistent — the hook copies whatever the row is being written with rather
// than stamping a fresh clock read over it.
func TestCreateWithAnExplicitCreatedAt(t *testing.T) {
	t.Parallel()
	cl := dbtest.Client(t)

	backdated := time.Date(2020, 1, 2, 3, 4, 5, 0, time.UTC)
	project, err := cl.Project.Create().
		SetSlug("tt").
		SetCreatedAt(backdated).
		Save(t.Context())
	if err != nil {
		t.Fatalf("creating project: %v", err)
	}

	if !project.CreatedAt.Equal(backdated) {
		t.Errorf("created_at = %v, want %v", project.CreatedAt, backdated)
	}
	if !project.UpdatedAt.Equal(backdated) {
		t.Errorf("updated_at = %v, want %v", project.UpdatedAt, backdated)
	}
}

// The client is what the hook has to reach, so a row read back carries the same
// answer the create returned.
func TestStampsSurviveAReadBack(t *testing.T) {
	t.Parallel()
	cl := dbtest.Client(t)

	created, err := cl.Label.Create().SetName("bug").Save(t.Context())
	if err != nil {
		t.Fatalf("creating label: %v", err)
	}
	reloaded, err := cl.Label.Get(t.Context(), created.ID)
	if err != nil {
		t.Fatalf("reading label: %v", err)
	}
	if !reloaded.UpdatedAt.Equal(reloaded.CreatedAt) {
		t.Errorf("read back: updated_at %v, created_at %v, want the same instant",
			reloaded.UpdatedAt, reloaded.CreatedAt)
	}
}
