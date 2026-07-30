package milestones_test

import (
	"errors"
	"testing"

	"github.com/Plabrum/tt/backend/errs"
	"github.com/Plabrum/tt/backend/internal/dbtest"
	"github.com/Plabrum/tt/backend/internal/ent"
	"github.com/Plabrum/tt/backend/internal/milestones"
	"github.com/Plabrum/tt/backend/internal/projects"
)

func project(t *testing.T, cl *ent.Client, slug string) int {
	t.Helper()
	id, err := projects.Ensure(t.Context(), cl, slug)
	if err != nil {
		t.Fatalf("ensuring project %q: %v", slug, err)
	}
	return id
}

func TestNormalise(t *testing.T) {
	t.Parallel()

	tests := []struct {
		in, want string
	}{
		{"v1", "v1"},
		{"  V1  ", "v1"},
		{"   ", ""},
	}
	for _, tt := range tests {
		t.Run(tt.in, func(t *testing.T) {
			t.Parallel()
			if got := milestones.Normalise(tt.in); got != tt.want {
				t.Errorf("Normalise(%q) = %q, want %q", tt.in, got, tt.want)
			}
		})
	}
}

func TestEnsureIsIdempotentAndNormalising(t *testing.T) {
	t.Parallel()
	cl := dbtest.Client(t)
	id := project(t, cl, "tt")

	first, err := milestones.Ensure(t.Context(), cl, id, "V1")
	if err != nil {
		t.Fatalf("Ensure: %v", err)
	}
	second, err := milestones.Ensure(t.Context(), cl, id, "  v1  ")
	if err != nil {
		t.Fatalf("Ensure: %v", err)
	}
	if first != second {
		t.Errorf("ids %d and %d, want the same milestone", first, second)
	}
}

// Milestones are scoped to their project: two projects each having a "v1" is
// the point of the scoping.
func TestMilestonesAreProjectScoped(t *testing.T) {
	t.Parallel()
	cl := dbtest.Client(t)

	one := project(t, cl, "one")
	two := project(t, cl, "two")

	first, err := milestones.Ensure(t.Context(), cl, one, "v1")
	if err != nil {
		t.Fatalf("Ensure: %v", err)
	}
	second, err := milestones.Ensure(t.Context(), cl, two, "v1")
	if err != nil {
		t.Fatalf("Ensure: %v", err)
	}
	if first == second {
		t.Error("two projects share one milestone")
	}

	found, err := milestones.ListForProject(t.Context(), cl, "one")
	if err != nil {
		t.Fatalf("ListForProject: %v", err)
	}
	if len(found) != 1 || found[0].ID != first {
		t.Errorf("milestones of project one = %+v, want just %d", found, first)
	}
}

func TestEnsureRejectsABlankName(t *testing.T) {
	t.Parallel()
	cl := dbtest.Client(t)
	id := project(t, cl, "tt")

	if _, err := milestones.Ensure(t.Context(), cl, id, "  "); !errors.Is(err, errs.ErrInvalid) {
		t.Errorf("Ensure = %v, want ErrInvalid", err)
	}
}

func TestListIsNeverNil(t *testing.T) {
	t.Parallel()
	cl := dbtest.Client(t)

	found, err := milestones.ListForProject(t.Context(), cl, "nothing")
	if err != nil {
		t.Fatalf("ListForProject: %v", err)
	}
	if found == nil {
		t.Error("an empty list is nil, want an empty slice")
	}
}
