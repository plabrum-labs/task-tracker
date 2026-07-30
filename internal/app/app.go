// Package app is the API the frontends call. One method per verb, and one
// method is one unit of work: creating an issue with labels, a milestone and a
// parent link commits or rolls back as a whole, however many features it
// touches.
//
// Feature packages are leaves — they take a client and never open a
// transaction. Composing them is this package's job, which is why a rule that
// spans two features is enforced here and a rule that belongs to one is
// enforced inside it. Either way a frontend cannot route around it, because the
// API is all a frontend can see.
package app

import (
	"context"

	"github.com/Plabrum/tt/ent"
	"github.com/Plabrum/tt/internal/db"
	"github.com/Plabrum/tt/internal/errs"
	"github.com/Plabrum/tt/internal/issue"
	"github.com/Plabrum/tt/internal/project"
	"github.com/Plabrum/tt/internal/query"
)

// API is what cli/ and ui/ are handed.
//
// The ent client is unexported. A frontend that can reach the client can write
// a query, and the moment one does, the view types stop being the contract — a
// schema change starts breaking the CLI directly instead of breaking the one
// method that was supposed to absorb it.
type API struct {
	client *ent.Client
}

// New returns an API over one client.
func New(client *ent.Client) *API {
	return &API{client: client}
}

// Add creates an issue, creating its project if this is the first one in it.
func (a *API) Add(ctx context.Context, p issue.AddParams) (issue.Issue, error) {
	// These three fields parse today and do nothing, which is worse than not
	// existing — a flag that is silently dropped is a bug report. Each guard is
	// deleted by the lane that adds the call replacing it, in this method.
	if len(p.Labels) > 0 {
		return issue.Issue{}, errs.Conflictf("labels are not wired yet")
	}
	if p.Milestone != "" {
		return issue.Issue{}, errs.Conflictf("milestones are not wired yet")
	}
	if p.SubOf != nil {
		return issue.Issue{}, errs.Conflictf("--sub-of is not wired yet")
	}

	var created issue.Issue
	err := db.WithTx(ctx, a.client, func(tx *ent.Client) error {
		// The project auto-create runs in the same transaction as the insert,
		// so a rejected issue takes its container with it. Capture should never
		// leave behind an empty project for work that was never written.
		projectID, err := project.Ensure(ctx, tx, p.Project)
		if err != nil {
			return err
		}
		created, err = issue.Create(ctx, tx, projectID, p)
		return err
	})
	if err != nil {
		return issue.Issue{}, err
	}
	return created, nil
}

// Reads pass the client straight through rather than opening a transaction.
// The pool is pinned to one connection, so a BEGIN here would serialise the
// whole process behind itself and buy nothing in-process; the only exposure is
// a cross-process writer leaving a derived flag one refresh stale.

// Show returns one issue with its refs and comments.
func (a *API) Show(ctx context.Context, id int) (issue.Detail, error) {
	return issue.Get(ctx, a.client, id)
}

// List returns the rows matching p, in pick order.
func (a *API) List(ctx context.Context, p query.ListParams) ([]query.Row, error) {
	return query.List(ctx, a.client, p)
}
