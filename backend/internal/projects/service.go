package projects

import (
	"context"
	"fmt"

	"github.com/Plabrum/tt/backend/contract"
	"github.com/Plabrum/tt/backend/errs"
	"github.com/Plabrum/tt/backend/internal/actions"
	"github.com/Plabrum/tt/backend/internal/db"
	"github.com/Plabrum/tt/backend/internal/ent"
	entproject "github.com/Plabrum/tt/backend/internal/ent/project"
)

// Action is an action on a project, taking a payload of type P.
type Action[P any] = actions.Bound[contract.ProjectKey, *ent.Project, P, contract.Project]

// Run performs an action on the project with this slug.
func Run[P any](ctx context.Context, cl *ent.Client, slug string, a *Action[P], p P) (contract.Project, error) {
	var out contract.Project
	err := db.WithTx(ctx, cl, func(tx *ent.Client) error {
		var err error
		out, err = RunIn(ctx, tx, slug, a, p)
		return err
	})
	return out, err
}

// RunIn is Run inside a caller's transaction.
func RunIn[P any](ctx context.Context, tx *ent.Client, slug string, a *Action[P], p P) (contract.Project, error) {
	e, err := row(ctx, tx, slug)
	if err != nil {
		return contract.Project{}, err
	}
	return actions.Invoke(ctx, projectActions, tx, e, a, p)
}

// Dispatch performs the action key names on the project with this slug.
func Dispatch(
	ctx context.Context,
	cl *ent.Client,
	slug string,
	key contract.ProjectKey,
	payload any,
) (contract.Project, error) {
	var out contract.Project
	err := db.WithTx(ctx, cl, func(tx *ent.Client) error {
		e, err := row(ctx, tx, slug)
		if err != nil {
			return err
		}
		out, err = projectActions.Dispatch(ctx, tx, e, key, payload)
		return err
	})
	return out, err
}

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
