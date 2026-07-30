package milestones

import (
	"context"
	"fmt"
	"strings"

	"github.com/Plabrum/tt/backend/errs"
	"github.com/Plabrum/tt/backend/internal/db"
	"github.com/Plabrum/tt/backend/internal/ent"
	entmilestone "github.com/Plabrum/tt/backend/internal/ent/milestone"
	entproject "github.com/Plabrum/tt/backend/internal/ent/project"
)

// Ensure returns the id of the milestone with this name in this project,
// creating it if needed.
func Ensure(ctx context.Context, cl *ent.Client, projectID int, name string) (int, error) {
	var id int
	err := db.WithTx(ctx, cl, func(tx *ent.Client) error {
		var err error
		id, err = EnsureIn(ctx, tx, projectID, name)
		return err
	})
	return id, err
}

// EnsureIn is Ensure inside a caller's transaction.
//
// Milestones are unique on (project, name), so the lookup is scoped to the
// project. Two projects each having a "v1" is the point of the scoping.
func EnsureIn(ctx context.Context, tx *ent.Client, projectID int, name string) (int, error) {
	name = Normalise(name)
	if name == "" {
		return 0, errs.Invalidf("milestone name is required")
	}
	id, err := tx.Milestone.Query().
		Where(entmilestone.NameEQ(name), entmilestone.HasProjectWith(entproject.IDEQ(projectID))).
		OnlyID(ctx)
	if err == nil {
		return id, nil
	}
	if !ent.IsNotFound(err) {
		return 0, fmt.Errorf("looking up milestone %q: %w", name, err)
	}
	created, err := tx.Milestone.Create().
		SetName(name).
		SetProjectID(projectID).
		Save(ctx)
	if err != nil {
		return 0, fmt.Errorf("creating milestone %q: %w", name, err)
	}
	return created.ID, nil
}

// LookupIn returns the id of an existing milestone in this project, or
// ErrNotFound.
func LookupIn(ctx context.Context, tx *ent.Client, projectID int, name string) (int, error) {
	name = Normalise(name)
	if name == "" {
		return 0, errs.Invalidf("milestone name is required")
	}
	id, err := tx.Milestone.Query().
		Where(entmilestone.NameEQ(name), entmilestone.HasProjectWith(entproject.IDEQ(projectID))).
		OnlyID(ctx)
	if err != nil {
		if ent.IsNotFound(err) {
			return 0, errs.NotFoundf("milestone %q", name)
		}
		return 0, fmt.Errorf("looking up milestone %q: %w", name, err)
	}
	return id, nil
}

// Normalise is the one spelling rule, so `-M V1` and `-M v1` cannot become two
// milestones in the same project.
func Normalise(name string) string {
	return strings.ToLower(strings.TrimSpace(name))
}
