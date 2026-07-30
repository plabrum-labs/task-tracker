package project

import (
	"context"

	"github.com/Plabrum/tt/ent"
	"github.com/Plabrum/tt/internal/db"
	"github.com/Plabrum/tt/internal/errs"
)

// Service creates and looks up projects.
//
// It follows the pattern every service in this codebase uses: the exported
// frontend method owns the transaction boundary and has an ent-free signature,
// while the ...In variant takes a client explicitly and never begins a
// transaction. A composed Ensure and a standalone Ensure therefore run the same
// code, and only the outermost caller decides what is atomic.
type Service struct {
	client *ent.Client
	repo   repository
}

func NewService(client *ent.Client) *Service {
	return &Service{client: client}
}

// Ensure returns the id of the project with this slug, creating it if needed.
func (s *Service) Ensure(ctx context.Context, slug string) (int, error) {
	var id int
	err := db.WithTx(ctx, s.client, func(tx *ent.Client) error {
		var err error
		id, err = s.EnsureIn(ctx, tx, slug)
		return err
	})
	return id, err
}

// EnsureIn is Ensure inside a caller's transaction. `tt add` uses it so a
// failure later in the same command leaves no orphan project behind.
func (s *Service) EnsureIn(ctx context.Context, cl *ent.Client, slug string) (int, error) {
	// Slugified again rather than trusted: EnsureIn is reachable from services
	// that never went through Resolver, and one unnormalised caller is all it
	// takes to end up with `My-Repo` and `my-repo` as separate projects.
	slug = Slugify(slug)
	if slug == "" {
		return 0, errs.Invalidf("project is required")
	}
	return s.repo.upsert(ctx, cl, slug)
}
