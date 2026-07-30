package projects

import (
	"slices"
	"testing"

	"github.com/Plabrum/tt/backend/contract"
	"github.com/Plabrum/tt/backend/internal/ent"
	entproject "github.com/Plabrum/tt/backend/internal/ent/project"
)

// The rules need no database: a table of literal ent rows is the whole test.
// These live in the package because what they exercise — the group and the
// predicates behind it — is unexported.

func find(offered []contract.Action[contract.ProjectKey], key contract.ProjectKey) (contract.Action[contract.ProjectKey], bool) {
	i := slices.IndexFunc(offered, func(a contract.Action[contract.ProjectKey]) bool { return a.Key == key })
	if i < 0 {
		return contract.Action[contract.ProjectKey]{}, false
	}
	return offered[i], true
}

// Actions needs no database.
func TestActions(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name    string
		status  entproject.Status
		present []contract.ProjectKey
		absent  []contract.ProjectKey
	}{
		{
			name:    "an active project can be archived",
			status:  entproject.StatusActive,
			present: []contract.ProjectKey{contract.KeyProjectEdit, contract.KeyProjectArchive},
			absent:  []contract.ProjectKey{contract.KeyProjectRestore},
		},
		{
			name:    "an archived project can be restored",
			status:  entproject.StatusArchived,
			present: []contract.ProjectKey{contract.KeyProjectEdit, contract.KeyProjectRestore},
			absent:  []contract.ProjectKey{contract.KeyProjectArchive},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			offered := projectActions.Available(&ent.Project{ID: 1, Slug: "tt", Status: tt.status})
			for _, key := range tt.present {
				action, ok := find(offered, key)
				if !ok {
					t.Errorf("a %s project does not offer %q", tt.status, key)
					continue
				}
				if !action.Runnable() {
					t.Errorf("%q refused on a %s project: %s", key, tt.status, action.Reason)
				}
			}
			for _, key := range tt.absent {
				if _, ok := find(offered, key); ok {
					t.Errorf("a %s project offers %q", tt.status, key)
				}
			}
		})
	}
}
