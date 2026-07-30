package issues

import (
	"context"
	"fmt"
	"strings"

	"github.com/Plabrum/tt/backend/contract"
	"github.com/Plabrum/tt/backend/errs"
	"github.com/Plabrum/tt/backend/internal/actions"
	"github.com/Plabrum/tt/backend/internal/db"
	"github.com/Plabrum/tt/backend/internal/ent"
	entissue "github.com/Plabrum/tt/backend/internal/ent/issue"
	entproject "github.com/Plabrum/tt/backend/internal/ent/project"
	entref "github.com/Plabrum/tt/backend/internal/ent/ref"
	"github.com/Plabrum/tt/backend/internal/labels"
	"github.com/Plabrum/tt/backend/internal/milestones"
	"github.com/Plabrum/tt/backend/internal/projects"
)

// Action is an action on an issue, taking a payload of type P.
type Action[P any] = actions.Bound[contract.IssueKey, *ent.Issue, P, contract.Issue]

// Run performs an action on the issue with this id.
//
// The pair is the one transaction boundary the domain has: Run owns the
// transaction and RunIn composes into a caller's, so no action declares one.
func Run[P any](ctx context.Context, cl *ent.Client, id int, a *Action[P], p P) (contract.Issue, error) {
	var out contract.Issue
	err := db.WithTx(ctx, cl, func(tx *ent.Client) error {
		var err error
		out, err = RunIn(ctx, tx, id, a, p)
		return err
	})
	return out, err
}

// RunIn is Run inside a caller's transaction.
//
// The row goes through the loader that carries the action edges, so the check
// Invoke makes is against the same facts a frontend's actions were built from.
func RunIn[P any](ctx context.Context, tx *ent.Client, id int, a *Action[P], p P) (contract.Issue, error) {
	e, err := row(ctx, tx, id)
	if err != nil {
		return contract.Issue{}, err
	}
	return actions.Invoke(ctx, issueActions, tx, e, a, p)
}

// Dispatch performs the action key names on the issue with this id.
func Dispatch(
	ctx context.Context,
	cl *ent.Client,
	id int,
	key contract.IssueKey,
	payload any,
) (contract.Issue, error) {
	var out contract.Issue
	err := db.WithTx(ctx, cl, func(tx *ent.Client) error {
		e, err := row(ctx, tx, id)
		if err != nil {
			return err
		}
		out, err = issueActions.Dispatch(ctx, tx, e, key, payload)
		return err
	})
	return out, err
}

// Create captures a new issue, creating its project on first use.
//
// It has no subject, so it is in no group and is not an action: there is no
// issue yet for a rule to read.
func Create(ctx context.Context, cl *ent.Client, p contract.IssueAddParams) (contract.Issue, error) {
	var out contract.Issue
	err := db.WithTx(ctx, cl, func(tx *ent.Client) error {
		var err error
		out, err = CreateIn(ctx, tx, p)
		return err
	})
	return out, err
}

// CreateIn is Create inside a caller's transaction.
//
// The project auto-create runs in the same transaction as the insert, so a
// rejected issue takes its container with it. Capture should never leave behind
// an empty project for work that was never written.
func CreateIn(ctx context.Context, tx *ent.Client, p contract.IssueAddParams) (contract.Issue, error) {
	if err := p.Validate(); err != nil {
		return contract.Issue{}, err
	}
	projectID, err := projects.EnsureIn(ctx, tx, p.Project)
	if err != nil {
		return contract.Issue{}, err
	}
	if err := refuseArchived(ctx, tx, projectID); err != nil {
		return contract.Issue{}, err
	}
	return insert(ctx, tx, projectID, nil, p)
}

// insert writes a new issue and reloads it. Status and the timestamps come from
// schema defaults, so a second write path cannot set them differently.
func insert(
	ctx context.Context,
	tx *ent.Client,
	projectID int,
	parentID *int,
	p contract.IssueAddParams,
) (contract.Issue, error) {
	priority := p.Priority
	if priority == "" {
		priority = contract.PriorityNormal
	}
	stored, err := priorityToEnt(priority)
	if err != nil {
		return contract.Issue{}, err
	}

	create := tx.Issue.Create().
		SetTitle(strings.TrimSpace(p.Title)).
		SetBody(p.Body).
		SetPriority(stored).
		SetProjectID(projectID)
	if parentID != nil {
		create = create.SetParentID(*parentID)
	}
	if p.Milestone != "" {
		milestoneID, err := milestones.EnsureIn(ctx, tx, projectID, p.Milestone)
		if err != nil {
			return contract.Issue{}, err
		}
		create = create.SetMilestoneID(milestoneID)
	}
	for _, name := range p.Labels {
		labelID, err := labels.EnsureIn(ctx, tx, name)
		if err != nil {
			return contract.Issue{}, err
		}
		create = create.AddLabelIDs(labelID)
	}

	created, err := create.Save(ctx)
	if err != nil {
		return contract.Issue{}, fmt.Errorf("creating issue: %w", err)
	}
	// Re-read through the same loader the reads use, rather than assembling an
	// issue from what was just written. It costs one query and buys the
	// guarantee that a write and a read can never disagree.
	return Load(ctx, tx, created.ID)
}

// refuseCycle rejects a dependency that would close a loop, by walking from the
// proposed blocker to see whether the issue itself is already upstream of it.
func refuseCycle(ctx context.Context, tx *ent.Client, id, blockerID int) error {
	seen := map[int]bool{blockerID: true}
	frontier := []int{blockerID}
	for len(frontier) > 0 {
		next, err := tx.Ref.Query().
			Where(entref.BlockedIDIn(frontier...)).
			Select(entref.FieldBlockerID).
			Ints(ctx)
		if err != nil {
			return fmt.Errorf("walking dependencies of issue %d: %w", blockerID, err)
		}
		frontier = frontier[:0]
		for _, n := range next {
			if n == id {
				return errs.Conflictf("issue %d already waits on issue %d, directly or through others", blockerID, id)
			}
			if !seen[n] {
				seen[n] = true
				frontier = append(frontier, n)
			}
		}
	}
	return nil
}

// refuseArchived keeps new work out of a project that was archived to get it
// out of the way. Restoring the project is what makes capture work again.
func refuseArchived(ctx context.Context, tx *ent.Client, projectID int) error {
	archived, err := tx.Project.Query().
		Where(entproject.IDEQ(projectID), entproject.StatusEQ(entproject.StatusArchived)).
		Exist(ctx)
	if err != nil {
		return fmt.Errorf("checking project %d: %w", projectID, err)
	}
	if archived {
		return errs.Conflictf("project %d is archived", projectID)
	}
	return nil
}

// validateChild checks the params of a sub-issue, whose project comes from its
// parent rather than from the caller.
func validateChild(p contract.IssueAddParams) error {
	if strings.TrimSpace(p.Title) == "" {
		return errs.Invalidf("title is required")
	}
	switch p.Priority {
	case "", contract.PriorityNormal, contract.PriorityHi:
		return nil
	default:
		return errs.Invalidf("unknown priority %q: want normal or hi", p.Priority)
	}
}

// row reads the ent row a write is about to change, with the edges the rules
// need. Going through withActionEdges is what lets the group's Check consult the
// same facts a frontend's actions were built from.
func row(ctx context.Context, tx *ent.Client, id int) (*ent.Issue, error) {
	e, err := withActionEdges(tx.Issue.Query().Where(entissue.IDEQ(id))).Only(ctx)
	if err != nil {
		if ent.IsNotFound(err) {
			return nil, errs.NotFoundf("issue %d", id)
		}
		return nil, fmt.Errorf("loading issue %d: %w", id, err)
	}
	return e, nil
}
