package project_test

import (
	"context"
	"testing"

	entproject "github.com/Plabrum/tt/ent/project"
	"github.com/Plabrum/tt/internal/dbtest"
	"github.com/Plabrum/tt/internal/project"
)

func TestEnsureIsIdempotent(t *testing.T) {
	t.Parallel()
	ctx := context.Background()
	client := dbtest.Client(t)
	svc := project.NewService(client)

	first, err := svc.Ensure(ctx, "tt")
	if err != nil {
		t.Fatalf("first Ensure: %v", err)
	}
	second, err := svc.Ensure(ctx, "tt")
	if err != nil {
		t.Fatalf("second Ensure: %v", err)
	}

	if first != second {
		t.Errorf("ids = %d and %d, want the same project twice", first, second)
	}
	if n := client.Project.Query().CountX(ctx); n != 1 {
		t.Errorf("projects = %d, want 1", n)
	}
}

// TestEnsurePreservesTitle is the regression test for the upsert's conflict
// action. UpdateNewValues() would return the right id and quietly overwrite
// title and description with the create builder's empty defaults, so `tt add`
// in a project you had described would erase the description.
func TestEnsurePreservesTitle(t *testing.T) {
	t.Parallel()
	ctx := context.Background()
	client := dbtest.Client(t)
	svc := project.NewService(client)

	existing := client.Project.Create().
		SetSlug("tt").
		SetTitle("task tracker").
		SetDescription("the one you are reading").
		SaveX(ctx)

	id, err := svc.Ensure(ctx, "tt")
	if err != nil {
		t.Fatalf("Ensure: %v", err)
	}
	if id != existing.ID {
		t.Errorf("id = %d, want the existing %d", id, existing.ID)
	}

	got := client.Project.GetX(ctx, existing.ID)
	if got.Title != "task tracker" {
		t.Errorf("title = %q, want %q", got.Title, "task tracker")
	}
	if got.Description != "the one you are reading" {
		t.Errorf("description = %q, want it preserved", got.Description)
	}
}

// TestEnsureNormalises: two spellings of one project name must not become two
// rows, whichever door they came in through.
func TestEnsureNormalises(t *testing.T) {
	t.Parallel()
	ctx := context.Background()
	client := dbtest.Client(t)
	svc := project.NewService(client)

	first, err := svc.Ensure(ctx, "My Repo")
	if err != nil {
		t.Fatalf("Ensure(%q): %v", "My Repo", err)
	}
	second, err := svc.Ensure(ctx, "my-repo")
	if err != nil {
		t.Fatalf("Ensure(%q): %v", "my-repo", err)
	}

	if first != second {
		t.Errorf("ids = %d and %d, want the same project", first, second)
	}
	if slug := client.Project.GetX(ctx, first).Slug; slug != "my-repo" {
		t.Errorf("slug = %q, want %q", slug, "my-repo")
	}
	if n := client.Project.Query().Where(entproject.SlugEQ("my-repo")).CountX(ctx); n != 1 {
		t.Errorf("rows for my-repo = %d, want 1", n)
	}
}

func TestEnsureRejectsEmptySlug(t *testing.T) {
	t.Parallel()
	ctx := context.Background()
	svc := project.NewService(dbtest.Client(t))

	for _, slug := range []string{"", "   ", "///"} {
		if _, err := svc.Ensure(ctx, slug); err == nil {
			t.Errorf("Ensure(%q) succeeded, want an error", slug)
		}
	}
}
