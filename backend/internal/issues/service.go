package issues

import (
	"context"
	"fmt"
	"strings"

	"github.com/Plabrum/tt/backend/contract"
	"github.com/Plabrum/tt/backend/errs"
	"github.com/Plabrum/tt/backend/internal/actions"
	"github.com/Plabrum/tt/backend/internal/comments"
	"github.com/Plabrum/tt/backend/internal/db"
	"github.com/Plabrum/tt/backend/internal/ent"
	entissue "github.com/Plabrum/tt/backend/internal/ent/issue"
	entlabel "github.com/Plabrum/tt/backend/internal/ent/label"
	entproject "github.com/Plabrum/tt/backend/internal/ent/project"
	entref "github.com/Plabrum/tt/backend/internal/ent/ref"
	"github.com/Plabrum/tt/backend/internal/labels"
	"github.com/Plabrum/tt/backend/internal/milestones"
	"github.com/Plabrum/tt/backend/internal/projects"
)

// Action is an action on an issue, taking a payload of type P.
type Action[P any] = actions.Action[contract.IssueKey, *ent.Issue, P, contract.Issue]

// Run performs an action on the issue with this id.
//
// The pair is the one transaction boundary the domain has: Run owns the
// transaction and RunIn composes into a caller's, so the thirteen writes below
// declare a rule and a payload and nothing else.
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
// The row goes through the loader that carries the menu edges, so the check
// Invoke makes is against the same facts a frontend's menu was rendered from.
func RunIn[P any](ctx context.Context, tx *ent.Client, id int, a *Action[P], p P) (contract.Issue, error) {
	e, err := row(ctx, tx, id)
	if err != nil {
		return contract.Issue{}, err
	}
	return actions.Invoke(ctx, menu, tx, e, a, p)
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
		out, err = menu.Dispatch(ctx, tx, e, key, payload)
		return err
	})
	return out, err
}

// Create captures a new issue, creating its project on first use.
//
// It has no subject, so it is not on any menu and is not an Action: there is no
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

// The writes behind the actions in actions.go. Each is handed a loaded row that
// its Spec has already cleared, so none of them restates a rule; what they do
// restate — an archived project, a dependency cycle — is what a menu cannot
// decide, because it needs a query.

// addSubIssue files a new issue under this one.
//
// The child lands in its parent's project whatever the params say: a sub-issue
// filed somewhere else would leave the parent's project showing a rollup over
// work it does not contain.
func addSubIssue(
	ctx context.Context,
	tx *ent.Client,
	e *ent.Issue,
	p contract.IssueAddParams,
) (contract.Issue, error) {
	projectID, err := e.QueryProject().OnlyID(ctx)
	if err != nil {
		return contract.Issue{}, fmt.Errorf("resolving project of issue %d: %w", e.ID, err)
	}
	if err := refuseArchived(ctx, tx, projectID); err != nil {
		return contract.Issue{}, err
	}

	p.Project = "" // resolved from the parent, not from the caller
	if err := validateChild(p); err != nil {
		return contract.Issue{}, err
	}
	return insert(ctx, tx, projectID, &e.ID, p)
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

// edit changes an issue's title or body.
func edit(
	ctx context.Context,
	tx *ent.Client,
	e *ent.Issue,
	p contract.IssueEditParams,
) (contract.Issue, error) {
	if p.Title == nil && p.Body == nil {
		return contract.Issue{}, errs.Invalidf("nothing to change")
	}

	u := e.Update()
	if p.Title != nil {
		title := strings.TrimSpace(*p.Title)
		if title == "" {
			return contract.Issue{}, errs.Invalidf("title is required")
		}
		u = u.SetTitle(title)
	}
	if p.Body != nil {
		u = u.SetBody(*p.Body)
	}
	if _, err := u.Save(ctx); err != nil {
		return contract.Issue{}, fmt.Errorf("editing issue %d: %w", e.ID, err)
	}
	return Load(ctx, tx, e.ID)
}

// remove deletes an issue.
//
// Its comments and refs go with it by cascade, and its sub-issues survive with
// their parent_id cleared: deleting a parent should not delete work nobody
// asked to lose.
//
// It returns the zero issue because there is no issue left to return, and every
// action in a group returns the same type.
func remove(ctx context.Context, tx *ent.Client, e *ent.Issue, _ actions.None) (contract.Issue, error) {
	if err := tx.Issue.DeleteOne(e).Exec(ctx); err != nil {
		return contract.Issue{}, fmt.Errorf("deleting issue %d: %w", e.ID, err)
	}
	return contract.Issue{}, nil
}

// setPriority changes an issue's pick-order bump.
func setPriority(
	ctx context.Context,
	tx *ent.Client,
	e *ent.Issue,
	p contract.Priority,
) (contract.Issue, error) {
	stored, err := priorityToEnt(p)
	if err != nil {
		return contract.Issue{}, err
	}
	if _, err := e.Update().SetPriority(stored).Save(ctx); err != nil {
		return contract.Issue{}, fmt.Errorf("setting priority of issue %d: %w", e.ID, err)
	}
	return Load(ctx, tx, e.ID)
}

// setMilestone files an issue under a milestone, creating it on first use. An
// empty name clears it.
func setMilestone(ctx context.Context, tx *ent.Client, e *ent.Issue, name string) (contract.Issue, error) {
	u := e.Update()
	if strings.TrimSpace(name) == "" {
		u = u.ClearMilestone()
	} else {
		projectID, err := e.QueryProject().OnlyID(ctx)
		if err != nil {
			return contract.Issue{}, fmt.Errorf("resolving project of issue %d: %w", e.ID, err)
		}
		milestoneID, err := milestones.EnsureIn(ctx, tx, projectID, name)
		if err != nil {
			return contract.Issue{}, err
		}
		u = u.SetMilestoneID(milestoneID)
	}
	if _, err := u.Save(ctx); err != nil {
		return contract.Issue{}, fmt.Errorf("setting milestone of issue %d: %w", e.ID, err)
	}
	return Load(ctx, tx, e.ID)
}

// addLabel puts a label on an issue, creating it on first use.
func addLabel(ctx context.Context, tx *ent.Client, e *ent.Issue, name string) (contract.Issue, error) {
	labelID, err := labels.EnsureIn(ctx, tx, name)
	if err != nil {
		return contract.Issue{}, err
	}
	// Already-present is not an error: the label is on the issue either way,
	// and AddLabelIDs on an existing edge violates the join table's key.
	has, err := e.QueryLabels().Where(entlabel.IDEQ(labelID)).Exist(ctx)
	if err != nil {
		return contract.Issue{}, fmt.Errorf("checking labels of issue %d: %w", e.ID, err)
	}
	if !has {
		if _, err := e.Update().AddLabelIDs(labelID).Save(ctx); err != nil {
			return contract.Issue{}, fmt.Errorf("labelling issue %d: %w", e.ID, err)
		}
	}
	return Load(ctx, tx, e.ID)
}

// removeLabel takes a label off an issue.
func removeLabel(ctx context.Context, tx *ent.Client, e *ent.Issue, name string) (contract.Issue, error) {
	labelID, err := labels.Lookup(ctx, tx, name)
	if err != nil {
		return contract.Issue{}, err
	}
	has, err := e.QueryLabels().Where(entlabel.IDEQ(labelID)).Exist(ctx)
	if err != nil {
		return contract.Issue{}, fmt.Errorf("checking labels of issue %d: %w", e.ID, err)
	}
	if !has {
		return contract.Issue{}, errs.Conflictf("issue %d is not labelled %q", e.ID, labels.Normalise(name))
	}
	if _, err := e.Update().RemoveLabelIDs(labelID).Save(ctx); err != nil {
		return contract.Issue{}, fmt.Errorf("unlabelling issue %d: %w", e.ID, err)
	}
	return Load(ctx, tx, e.ID)
}

// addDep records that an issue waits on another.
//
// The cycle check walks the dependency graph, so it is not a menu rule: a menu
// reads one level down and this needs however many the chain is deep.
func addDep(ctx context.Context, tx *ent.Client, e *ent.Issue, blockerID int) (contract.Issue, error) {
	if e.ID == blockerID {
		return contract.Issue{}, errs.Conflictf("issue %d cannot block itself", e.ID)
	}
	if _, err := row(ctx, tx, blockerID); err != nil {
		return contract.Issue{}, err
	}

	exists, err := tx.Ref.Query().
		Where(entref.BlockedIDEQ(e.ID), entref.BlockerIDEQ(blockerID)).
		Exist(ctx)
	if err != nil {
		return contract.Issue{}, fmt.Errorf("checking dependencies of issue %d: %w", e.ID, err)
	}
	if exists {
		return contract.Issue{}, errs.Conflictf("issue %d already waits on issue %d", e.ID, blockerID)
	}
	if err := refuseCycle(ctx, tx, e.ID, blockerID); err != nil {
		return contract.Issue{}, err
	}

	if _, err := tx.Ref.Create().SetBlockedID(e.ID).SetBlockerID(blockerID).Save(ctx); err != nil {
		return contract.Issue{}, fmt.Errorf("adding dependency %d -> %d: %w", e.ID, blockerID, err)
	}
	return Load(ctx, tx, e.ID)
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

// removeDep drops a dependency.
func removeDep(ctx context.Context, tx *ent.Client, e *ent.Issue, blockerID int) (contract.Issue, error) {
	deleted, err := tx.Ref.Delete().
		Where(entref.BlockedIDEQ(e.ID), entref.BlockerIDEQ(blockerID)).
		Exec(ctx)
	if err != nil {
		return contract.Issue{}, fmt.Errorf("removing dependency %d -> %d: %w", e.ID, blockerID, err)
	}
	if deleted == 0 {
		return contract.Issue{}, errs.Conflictf("issue %d does not wait on issue %d", e.ID, blockerID)
	}
	return Load(ctx, tx, e.ID)
}

// comment appends a comment and returns the issue it landed on.
func comment(ctx context.Context, tx *ent.Client, e *ent.Issue, body string) (contract.Issue, error) {
	if _, err := comments.AddIn(ctx, tx, e.ID, body); err != nil {
		return contract.Issue{}, err
	}
	return Load(ctx, tx, e.ID)
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

// row reads the ent row a write is about to change, with the edges the menu
// rules need. Going through withMenuEdges is what lets the group's Check
// consult the same Actions a frontend rendered.
func row(ctx context.Context, tx *ent.Client, id int) (*ent.Issue, error) {
	e, err := withMenuEdges(tx.Issue.Query().Where(entissue.IDEQ(id))).Only(ctx)
	if err != nil {
		if ent.IsNotFound(err) {
			return nil, errs.NotFoundf("issue %d", id)
		}
		return nil, fmt.Errorf("loading issue %d: %w", id, err)
	}
	return e, nil
}
