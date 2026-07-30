package labels_test

import (
	"errors"
	"testing"

	"github.com/Plabrum/tt/backend/errs"
	"github.com/Plabrum/tt/backend/internal/dbtest"
	"github.com/Plabrum/tt/backend/internal/labels"
)

func TestNormalise(t *testing.T) {
	t.Parallel()

	tests := []struct {
		in, want string
	}{
		{"bug", "bug"},
		{"  Bug  ", "bug"},
		{"BUG", "bug"},
		{"   ", ""},
	}
	for _, tt := range tests {
		t.Run(tt.in, func(t *testing.T) {
			t.Parallel()
			if got := labels.Normalise(tt.in); got != tt.want {
				t.Errorf("Normalise(%q) = %q, want %q", tt.in, got, tt.want)
			}
		})
	}
}

// One spelling rule, so `-l Bug` and `-l bug` cannot become two labels.
func TestEnsureIsIdempotentAndNormalising(t *testing.T) {
	t.Parallel()
	cl := dbtest.Client(t)

	first, err := labels.Ensure(t.Context(), cl, "Bug")
	if err != nil {
		t.Fatalf("Ensure: %v", err)
	}
	second, err := labels.Ensure(t.Context(), cl, "  bug  ")
	if err != nil {
		t.Fatalf("Ensure: %v", err)
	}
	if first != second {
		t.Errorf("ids %d and %d, want the same label", first, second)
	}

	found, err := labels.List(t.Context(), cl)
	if err != nil {
		t.Fatalf("List: %v", err)
	}
	if len(found) != 1 || found[0].Name != "bug" {
		t.Errorf("labels = %+v, want just bug", found)
	}
}

func TestEnsureRejectsABlankName(t *testing.T) {
	t.Parallel()
	cl := dbtest.Client(t)

	if _, err := labels.Ensure(t.Context(), cl, "   "); !errors.Is(err, errs.ErrInvalid) {
		t.Errorf("Ensure = %v, want ErrInvalid", err)
	}
}

// Lookup is the read that must not create, so an unknown label is not found
// rather than quietly brought into existence.
func TestLookupDoesNotCreate(t *testing.T) {
	t.Parallel()
	cl := dbtest.Client(t)

	if _, err := labels.Lookup(t.Context(), cl, "nope"); !errors.Is(err, errs.ErrNotFound) {
		t.Fatalf("Lookup = %v, want ErrNotFound", err)
	}
	n, err := cl.Label.Query().Count(t.Context())
	if err != nil {
		t.Fatalf("counting labels: %v", err)
	}
	if n != 0 {
		t.Errorf("labels = %d, want 0", n)
	}
}

func TestListIsNeverNil(t *testing.T) {
	t.Parallel()
	cl := dbtest.Client(t)

	found, err := labels.List(t.Context(), cl)
	if err != nil {
		t.Fatalf("List: %v", err)
	}
	if found == nil {
		t.Error("an empty list is nil, want an empty slice")
	}
}
