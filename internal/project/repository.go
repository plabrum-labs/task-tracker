package project

import (
	"context"
	"fmt"

	"github.com/Plabrum/tt/ent"
	entproject "github.com/Plabrum/tt/ent/project"
)

// upsert returns the id of the project with this slug, creating it if it is
// not there.
//
// The conflict action is a self-write — it sets slug to the value it already
// has. That looks pointless and is load-bearing, twice over:
//
//   - DO NOTHING would return no row, and ent's insertLastID reads the id out
//     of RETURNING. An existing project would come back as "no rows in result
//     set" on the very path that is supposed to be idempotent.
//   - UpdateNewValues() would return the id, but it copies *every* column from
//     the proposed insert, which here means the create builder's empty title
//     and description. Adding an issue to an existing project would blank the
//     title someone set on it.
//
// Setting one column to itself is the smallest DO UPDATE that yields a row,
// which is exactly what is wanted: no field is touched, and an id comes back.
func upsert(ctx context.Context, cl *ent.Client, slug string) (int, error) {
	id, err := cl.Project.Create().
		SetSlug(slug).
		OnConflictColumns(entproject.FieldSlug).
		Update(func(u *ent.ProjectUpsert) { u.SetSlug(slug) }).
		ID(ctx)
	if err != nil {
		return 0, fmt.Errorf("upserting project %q: %w", slug, err)
	}
	return id, nil
}
