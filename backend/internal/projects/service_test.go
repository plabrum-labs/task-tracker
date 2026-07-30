package projects_test

import (
	"errors"
	"slices"
	"testing"

	"github.com/Plabrum/tt/backend/contract"
	"github.com/Plabrum/tt/backend/errs"
	"github.com/Plabrum/tt/backend/internal/dbtest"
	"github.com/Plabrum/tt/backend/internal/ent"
	entproject "github.com/Plabrum/tt/backend/internal/ent/project"
	"github.com/Plabrum/tt/backend/internal/projects"
)

func find(menu []contract.Action[contract.ProjectKey], key contract.ProjectKey) (contract.Action[contract.ProjectKey], bool) {
	i := slices.IndexFunc(menu, func(a contract.Action[contract.ProjectKey]) bool { return a.Key == key })
	if i < 0 {
		return contract.Action[contract.ProjectKey]{}, false
	}
	return menu[i], true
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
			menu := projects.Actions(&ent.Project{ID: 1, Slug: "tt", Status: tt.status})
			for _, key := range tt.present {
				action, ok := find(menu, key)
				if !ok {
					t.Errorf("menu withholds %q from a %s project", key, tt.status)
					continue
				}
				if !action.Runnable() {
					t.Errorf("%q refused on a %s project: %s", key, tt.status, action.Reason)
				}
			}
			for _, key := range tt.absent {
				if _, ok := find(menu, key); ok {
					t.Errorf("menu offers %q on a %s project", key, tt.status)
				}
			}
		})
	}
}

func ensure(t *testing.T, cl *ent.Client, slug string) int {
	t.Helper()
	id, err := projects.Ensure(t.Context(), cl, slug)
	if err != nil {
		t.Fatalf("ensuring project %q: %v", slug, err)
	}
	return id
}

func TestEnsureIsIdempotentAndNormalising(t *testing.T) {
	t.Parallel()
	cl := dbtest.Client(t)

	first := ensure(t, cl, "My Repo")
	// The same project under a different spelling is the same project.
	second := ensure(t, cl, "my-repo")
	if first != second {
		t.Errorf("ids %d and %d, want the same project", first, second)
	}

	n, err := cl.Project.Query().Count(t.Context())
	if err != nil {
		t.Fatalf("counting projects: %v", err)
	}
	if n != 1 {
		t.Errorf("projects = %d, want 1", n)
	}
}

// Ensure must not blank a title someone set, nor un-archive a project.
func TestEnsurePreservesExistingFields(t *testing.T) {
	t.Parallel()
	cl := dbtest.Client(t)

	ensure(t, cl, "tt")
	title := "Task tracker"
	if _, err := projects.Edit(t.Context(), cl, "tt", contract.ProjectEditParams{Title: &title}); err != nil {
		t.Fatalf("Edit: %v", err)
	}
	if _, err := projects.Archive(t.Context(), cl, "tt"); err != nil {
		t.Fatalf("Archive: %v", err)
	}

	ensure(t, cl, "tt")

	loaded, err := projects.Load(t.Context(), cl, "tt")
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if loaded.Title != title {
		t.Errorf("Title = %q, want %q", loaded.Title, title)
	}
	if loaded.Status != contract.ProjectArchived {
		t.Errorf("Status = %q, want %q", loaded.Status, contract.ProjectArchived)
	}
}

func TestArchiveAndRestore(t *testing.T) {
	t.Parallel()
	cl := dbtest.Client(t)

	ensure(t, cl, "tt")

	archived, err := projects.Archive(t.Context(), cl, "tt")
	if err != nil {
		t.Fatalf("Archive: %v", err)
	}
	if archived.Status != contract.ProjectArchived {
		t.Errorf("Status = %q, want %q", archived.Status, contract.ProjectArchived)
	}
	// The menu the write returns already reflects the write.
	if _, ok := find(archived.Actions, contract.KeyProjectRestore); !ok {
		t.Error("an archived project does not offer restore")
	}

	// Archiving twice is refused, by the same rule that withholds the menu entry.
	if _, err := projects.Archive(t.Context(), cl, "tt"); !errors.Is(err, errs.ErrConflict) {
		t.Errorf("Archive twice = %v, want ErrConflict", err)
	}

	restored, err := projects.Restore(t.Context(), cl, "tt")
	if err != nil {
		t.Fatalf("Restore: %v", err)
	}
	if restored.Status != contract.ProjectActive {
		t.Errorf("Status = %q, want %q", restored.Status, contract.ProjectActive)
	}
	if _, err := projects.Restore(t.Context(), cl, "tt"); !errors.Is(err, errs.ErrConflict) {
		t.Errorf("Restore twice = %v, want ErrConflict", err)
	}
}

func TestListDefaultsToActive(t *testing.T) {
	t.Parallel()
	cl := dbtest.Client(t)

	ensure(t, cl, "kept")
	ensure(t, cl, "old")
	if _, err := projects.Archive(t.Context(), cl, "old"); err != nil {
		t.Fatalf("Archive: %v", err)
	}

	active, err := projects.List(t.Context(), cl, contract.ProjectListParams{})
	if err != nil {
		t.Fatalf("List: %v", err)
	}
	if len(active) != 1 || active[0].Slug != "kept" {
		t.Errorf("default list = %+v, want just kept", active)
	}

	both, err := projects.List(t.Context(), cl, contract.ProjectListParams{
		Statuses: []contract.ProjectStatus{contract.ProjectActive, contract.ProjectArchived},
	})
	if err != nil {
		t.Fatalf("List: %v", err)
	}
	if len(both) != 2 {
		t.Errorf("list of every status = %+v, want both", both)
	}
}

func TestEdit(t *testing.T) {
	t.Parallel()
	cl := dbtest.Client(t)

	ensure(t, cl, "tt")
	title := "  Task tracker  "
	description := "the tracker"
	edited, err := projects.Edit(t.Context(), cl, "tt", contract.ProjectEditParams{
		Title:       &title,
		Description: &description,
	})
	if err != nil {
		t.Fatalf("Edit: %v", err)
	}
	if edited.Title != "Task tracker" {
		t.Errorf("Title = %q, want %q", edited.Title, "Task tracker")
	}
	if edited.Description != description {
		t.Errorf("Description = %q, want %q", edited.Description, description)
	}

	if _, err := projects.Edit(t.Context(), cl, "tt", contract.ProjectEditParams{}); !errors.Is(err, errs.ErrInvalid) {
		t.Errorf("Edit with nothing set = %v, want ErrInvalid", err)
	}
}

func TestMissingSlugsAreNotFound(t *testing.T) {
	t.Parallel()
	cl := dbtest.Client(t)

	if _, err := projects.Load(t.Context(), cl, "nope"); !errors.Is(err, errs.ErrNotFound) {
		t.Errorf("Load = %v, want ErrNotFound", err)
	}
	if _, err := projects.Archive(t.Context(), cl, "nope"); !errors.Is(err, errs.ErrNotFound) {
		t.Errorf("Archive = %v, want ErrNotFound", err)
	}
	if _, err := projects.Edit(t.Context(), cl, "nope", contract.ProjectEditParams{}); !errors.Is(err, errs.ErrNotFound) {
		t.Errorf("Edit = %v, want ErrNotFound", err)
	}
}
