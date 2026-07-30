package issue

import (
	"context"
	"errors"

	"github.com/Plabrum/tt/ent"
	"github.com/Plabrum/tt/internal/db"
	"github.com/Plabrum/tt/internal/project"
)

// Service creates and reads issues.
//
// Exported methods come in pairs: the plain one is frontend-facing, has an
// ent-free signature, and owns the transaction boundary; the ...In one takes a
// client and composes into someone else's transaction. The plain one is a thin
// wrapper over the ...In one, so a composed Add and a standalone Add cannot
// drift apart.
type Service struct {
	client   *ent.Client
	projects *project.Service
	repo     repository
}

func NewService(client *ent.Client, projects *project.Service) *Service {
	return &Service{client: client, projects: projects}
}

// Add creates an issue, creating its project if that is the first one.
func (s *Service) Add(ctx context.Context, p AddParams) (Issue, error) {
	var created Issue
	err := db.WithTx(ctx, s.client, func(tx *ent.Client) error {
		var err error
		created, err = s.AddIn(ctx, tx, p)
		return err
	})
	if err != nil {
		return Issue{}, err
	}
	return created, nil
}

// AddIn is Add inside a caller's transaction.
func (s *Service) AddIn(ctx context.Context, cl *ent.Client, p AddParams) (Issue, error) {
	if err := p.validate(); err != nil {
		return Issue{}, err
	}

	// Auto-create runs in the caller's transaction, so a failure anywhere below
	// takes the project with it. Capture should never leave behind an empty
	// container for an issue that was never written.
	projectID, err := s.projects.EnsureIn(ctx, cl, p.Project)
	if err != nil {
		return Issue{}, err
	}

	// These three fields parse today and do nothing, which is worse than not
	// existing — a flag that is silently dropped is a bug report. Each guard is
	// deleted by the lane that implements the field.
	if len(p.Labels) > 0 {
		return Issue{}, errors.New("labels are not wired yet")
	}
	if p.Milestone != "" {
		return Issue{}, errors.New("milestones are not wired yet")
	}
	if p.SubOf != nil {
		return Issue{}, errors.New("--sub-of is not wired yet")
	}

	created, err := s.repo.insert(ctx, cl, projectID, p)
	if err != nil {
		return Issue{}, err
	}

	// Re-read through the same view Get uses, rather than assembling an Issue
	// from what was just written. It costs one query and buys the guarantee
	// that `tt add` and `tt show` can never disagree about what an issue is.
	d, err := s.repo.view(ctx, cl, created.ID)
	if err != nil {
		return Issue{}, err
	}
	return d.Issue, nil
}

// Get returns one issue with its refs and comments. A missing id wraps
// ErrNotFound, which the CLI turns into exit code 3.
func (s *Service) Get(ctx context.Context, id int) (Detail, error) {
	// A read needs no transaction: Detail comes out of a single eager-loaded
	// query, so there is nothing for a second statement to see torn.
	return s.GetIn(ctx, s.client, id)
}

// GetIn is Get against a caller's client.
func (s *Service) GetIn(ctx context.Context, cl *ent.Client, id int) (Detail, error) {
	return s.repo.view(ctx, cl, id)
}
