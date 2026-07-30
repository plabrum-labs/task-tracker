// Package milestones owns the project-scoped groupings work can be filed
// under. A milestone is not an object: it has no menu, so this package has no
// actions.go.
//
// Taxonomy never depends on work, so nothing here imports the issues domain.
package milestones

import (
	"context"
	"fmt"

	"github.com/Plabrum/tt/backend/contract"
	"github.com/Plabrum/tt/backend/internal/ent"
	entmilestone "github.com/Plabrum/tt/backend/internal/ent/milestone"
	entproject "github.com/Plabrum/tt/backend/internal/ent/project"
)

// ListForProject returns the milestones of one project, by name.
func ListForProject(ctx context.Context, cl *ent.Client, slug string) ([]contract.Milestone, error) {
	found, err := cl.Milestone.Query().
		Where(entmilestone.HasProjectWith(entproject.SlugEQ(slug))).
		Order(entmilestone.ByName()).
		All(ctx)
	if err != nil {
		return nil, fmt.Errorf("listing milestones of project %q: %w", slug, err)
	}
	out := make([]contract.Milestone, 0, len(found))
	for _, e := range found {
		out = append(out, Convert(e))
	}
	return out, nil
}

// Convert flattens an ent row into the contract type.
func Convert(e *ent.Milestone) contract.Milestone {
	m := contract.Milestone{
		ID:          e.ID,
		Name:        e.Name,
		Description: e.Description,
	}
	if e.Due != nil {
		// Copied, not aliased: the ent row is discarded here and nothing above
		// should hold a pointer into it.
		due := *e.Due
		m.Due = &due
	}
	return m
}
