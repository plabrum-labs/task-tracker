package labels

import (
	"context"
	"fmt"
	"strings"

	"github.com/Plabrum/tt/backend/errs"
	"github.com/Plabrum/tt/backend/internal/db"
	"github.com/Plabrum/tt/backend/internal/ent"
	entlabel "github.com/Plabrum/tt/backend/internal/ent/label"
)

// Ensure returns the id of the label with this name, creating it if needed.
func Ensure(ctx context.Context, cl *ent.Client, name string) (int, error) {
	var id int
	err := db.WithTx(ctx, cl, func(tx *ent.Client) error {
		var err error
		id, err = EnsureIn(ctx, tx, name)
		return err
	})
	return id, err
}

// EnsureIn is Ensure inside a caller's transaction.
//
// The conflict action sets name to the value it already has. That looks
// pointless and is load-bearing: DO NOTHING returns no row, and ent reads the
// id out of RETURNING, so an existing label would come back as "no rows in
// result set" on the one path that is meant to be idempotent. Copying every
// proposed column instead would blank the description someone set.
func EnsureIn(ctx context.Context, tx *ent.Client, name string) (int, error) {
	name = Normalise(name)
	if name == "" {
		return 0, errs.Invalidf("label name is required")
	}
	id, err := tx.Label.Create().
		SetName(name).
		OnConflictColumns(entlabel.FieldName).
		Update(func(u *ent.LabelUpsert) { u.SetName(name) }).
		ID(ctx)
	if err != nil {
		return 0, fmt.Errorf("upserting label %q: %w", name, err)
	}
	return id, nil
}

// Lookup returns the id of an existing label, or ErrNotFound.
func Lookup(ctx context.Context, cl *ent.Client, name string) (int, error) {
	name = Normalise(name)
	if name == "" {
		return 0, errs.Invalidf("label name is required")
	}
	id, err := cl.Label.Query().Where(entlabel.NameEQ(name)).OnlyID(ctx)
	if err != nil {
		if ent.IsNotFound(err) {
			return 0, errs.NotFoundf("label %q", name)
		}
		return 0, fmt.Errorf("looking up label %q: %w", name, err)
	}
	return id, nil
}

// Normalise is the one spelling rule, so `-l Bug` and `-l bug` cannot become
// two labels.
func Normalise(name string) string {
	return strings.ToLower(strings.TrimSpace(name))
}
