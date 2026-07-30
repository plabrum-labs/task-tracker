package projects

import (
	"context"
	"fmt"
	"strings"

	"github.com/Plabrum/tt/backend/errs"
	"github.com/Plabrum/tt/backend/internal/db"
	"github.com/Plabrum/tt/backend/internal/ent"
	entproject "github.com/Plabrum/tt/backend/internal/ent/project"
	"github.com/Plabrum/tt/backend/models"
)

// Ensure returns the id of the project with this slug, creating it if needed.
func Ensure(ctx context.Context, cl *ent.Client, slug string) (int, error) {
	var id int
	err := db.WithTx(ctx, cl, func(tx *ent.Client) error {
		var err error
		id, err = EnsureIn(ctx, tx, slug)
		return err
	})
	return id, err
}

// EnsureIn is Ensure inside a caller's transaction.
//
// The slug is slugified again rather than trusted: EnsureIn is reachable from
// callers that never went through Resolver, and one unnormalised caller is all
// it takes to end up with `My-Repo` and `my-repo` as separate projects.
//
// The conflict action is a self-write — it sets slug to the value it already
// has. That looks pointless and is load-bearing, twice over:
//
//   - DO NOTHING would return no row, and ent's insertLastID reads the id out
//     of RETURNING. An existing project would come back as "no rows in result
//     set" on the very path that is supposed to be idempotent.
//   - UpdateNewValues() would return the id, but it copies *every* column from
//     the proposed insert, which here means the create builder's empty title
//     and description, and its default `active` status. Adding an issue to an
//     existing project would blank the title someone set and silently
//     un-archive it.
//
// Setting one column to itself is the smallest DO UPDATE that yields a row,
// which is exactly what is wanted: no field is touched, and an id comes back.
func EnsureIn(ctx context.Context, tx *ent.Client, slug string) (int, error) {
	slug = Slugify(slug)
	if slug == "" {
		return 0, errs.Invalidf("project is required")
	}
	id, err := tx.Project.Create().
		SetSlug(slug).
		OnConflictColumns(entproject.FieldSlug).
		Update(func(u *ent.ProjectUpsert) { u.SetSlug(slug) }).
		ID(ctx)
	if err != nil {
		return 0, fmt.Errorf("upserting project %q: %w", slug, err)
	}
	return id, nil
}

// Edit changes a project's title or description.
func Edit(ctx context.Context, cl *ent.Client, slug string, p models.ProjectEditParams) (models.Project, error) {
	var out models.Project
	err := db.WithTx(ctx, cl, func(tx *ent.Client) error {
		var err error
		out, err = EditIn(ctx, tx, slug, p)
		return err
	})
	return out, err
}

// EditIn is Edit inside a caller's transaction.
func EditIn(ctx context.Context, tx *ent.Client, slug string, p models.ProjectEditParams) (models.Project, error) {
	e, err := row(ctx, tx, slug)
	if err != nil {
		return models.Project{}, err
	}
	if p.Title == nil && p.Description == nil {
		return models.Project{}, errs.Invalidf("nothing to change")
	}

	u := e.Update()
	if p.Title != nil {
		u = u.SetTitle(strings.TrimSpace(*p.Title))
	}
	if p.Description != nil {
		u = u.SetDescription(*p.Description)
	}
	if _, err := u.Save(ctx); err != nil {
		return models.Project{}, fmt.Errorf("editing project %q: %w", slug, err)
	}
	return reload(ctx, tx, e.ID)
}

// Archive takes a project's work out of the way.
func Archive(ctx context.Context, cl *ent.Client, slug string) (models.Project, error) {
	var out models.Project
	err := db.WithTx(ctx, cl, func(tx *ent.Client) error {
		var err error
		out, err = ArchiveIn(ctx, tx, slug)
		return err
	})
	return out, err
}

// ArchiveIn is Archive inside a caller's transaction.
func ArchiveIn(ctx context.Context, tx *ent.Client, slug string) (models.Project, error) {
	return setStatus(ctx, tx, slug, entproject.StatusArchived, models.KeyProjectArchive)
}

// Restore brings an archived project back.
func Restore(ctx context.Context, cl *ent.Client, slug string) (models.Project, error) {
	var out models.Project
	err := db.WithTx(ctx, cl, func(tx *ent.Client) error {
		var err error
		out, err = RestoreIn(ctx, tx, slug)
		return err
	})
	return out, err
}

// RestoreIn is Restore inside a caller's transaction.
func RestoreIn(ctx context.Context, tx *ent.Client, slug string) (models.Project, error) {
	return setStatus(ctx, tx, slug, entproject.StatusActive, models.KeyProjectRestore)
}

// setStatus enforces the same transition the menu offers, against the live row.
// The menu is a snapshot; this is what actually decides.
func setStatus(
	ctx context.Context,
	tx *ent.Client,
	slug string,
	want entproject.Status,
	key models.ProjectKey,
) (models.Project, error) {
	e, err := row(ctx, tx, slug)
	if err != nil {
		return models.Project{}, err
	}
	if !offers(e, key) {
		return models.Project{}, errs.Conflictf("project %q is already %s", e.Slug, want)
	}
	if _, err := e.Update().SetStatus(want).Save(ctx); err != nil {
		return models.Project{}, fmt.Errorf("setting project %q to %s: %w", slug, want, err)
	}
	return reload(ctx, tx, e.ID)
}

// offers reports whether the menu for this row carries key without a refusal.
// Going through Actions is what keeps the write and the menu from drifting.
func offers(e *ent.Project, key models.ProjectKey) bool {
	for _, a := range Actions(e) {
		if a.Key == key {
			return a.Runnable()
		}
	}
	return false
}

// row reads the ent row a write is about to change.
func row(ctx context.Context, tx *ent.Client, slug string) (*ent.Project, error) {
	e, err := tx.Project.Query().Where(entproject.SlugEQ(Slugify(slug))).Only(ctx)
	if err != nil {
		if ent.IsNotFound(err) {
			return nil, errs.NotFoundf("project %q", slug)
		}
		return nil, fmt.Errorf("loading project %q: %w", slug, err)
	}
	return e, nil
}

// reload re-reads through the same loader the reads use, so a write and a read
// can never disagree about what a project is.
func reload(ctx context.Context, tx *ent.Client, id int) (models.Project, error) {
	return LoadByID(ctx, tx, id)
}
