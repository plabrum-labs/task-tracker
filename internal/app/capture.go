package app

import (
	"context"

	"github.com/Plabrum/tt/ent"
	"github.com/Plabrum/tt/internal/db"
	"github.com/Plabrum/tt/internal/errs"
	"github.com/Plabrum/tt/internal/issue"
	"github.com/Plabrum/tt/internal/project"
)

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
