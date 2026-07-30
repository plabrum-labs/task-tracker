package issue

import (
	"context"

	"github.com/Plabrum/tt/ent"
)

// Create writes an issue into an existing project.
func Create(ctx context.Context, cl *ent.Client, projectID int, p AddParams) (Issue, error) {
	if err := p.validate(); err != nil {
		return Issue{}, err
	}

	created, err := insert(ctx, cl, projectID, p)
	if err != nil {
		return Issue{}, err
	}

	// Re-read through the same view Get uses, rather than assembling an Issue
	// from what was just written. It costs one query and buys the guarantee
	// that `tt add` and `tt show` can never disagree about what an issue is.
	d, err := view(ctx, cl, created.ID)
	if err != nil {
		return Issue{}, err
	}
	return d.Issue, nil
}

// Get returns one issue with its refs and comments. A missing id wraps
// errs.ErrNotFound.
func Get(ctx context.Context, cl *ent.Client, id int) (Detail, error) {
	return view(ctx, cl, id)
}
