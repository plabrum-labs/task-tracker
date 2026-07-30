package project

import (
	"context"

	"github.com/Plabrum/tt/ent"
	"github.com/Plabrum/tt/internal/errs"
)

// Ensure returns the id of the project with this slug, creating it if needed.
func Ensure(ctx context.Context, cl *ent.Client, slug string) (int, error) {
	// Slugified again rather than trusted: Ensure is reachable from callers that
	// never went through Resolver, and one unnormalised caller is all it takes
	// to end up with `My-Repo` and `my-repo` as separate projects.
	slug = Slugify(slug)
	if slug == "" {
		return 0, errs.Invalidf("project is required")
	}
	return upsert(ctx, cl, slug)
}
