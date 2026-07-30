package issues

import (
	"context"
	"fmt"
	"strings"

	"github.com/Plabrum/tt/backend/contract"
	"github.com/Plabrum/tt/backend/errs"
	"github.com/Plabrum/tt/backend/internal/actions"
	"github.com/Plabrum/tt/backend/internal/comments"
	"github.com/Plabrum/tt/backend/internal/ent"
	entissue "github.com/Plabrum/tt/backend/internal/ent/issue"
	entlabel "github.com/Plabrum/tt/backend/internal/ent/label"
	entref "github.com/Plabrum/tt/backend/internal/ent/ref"
	"github.com/Plabrum/tt/backend/internal/labels"
	"github.com/Plabrum/tt/backend/internal/milestones"
)

// The refusals a menu can carry. Each is also what the matching write returns
// wrapped in errs.ErrConflict, so a frontend never sees two wordings for one
// rule.
const (
	reasonBlocked      = "blocked by open issues"
	reasonOpenSubtasks = "open sub-issues"
)

// Every fact a rule below reads comes off an edge withMenuEdges eager-loads. An
// unloaded edge is an empty one as far as a rule can tell, so a row that did not
// come through that loader gets a menu that is wrong rather than a query that
// fails. A transition asks IssueStateMachine rather than restating its topology.

// start ---------------------------------------------------------------------

type startIssue struct{ actions.Default[*ent.Issue] }

func (startIssue) Key() contract.IssueKey { return contract.KeyIssueStart }
func (startIssue) Label() string          { return "start" }

func (startIssue) IsAvailable(e *ent.Issue) bool {
	return IssueStateMachine.Can(e, entissue.StatusDoing)
}

// Work something else has to come before cannot be picked up.
func (startIssue) IsDisabled(e *ent.Issue) string {
	if blocked(e) {
		return reasonBlocked
	}
	return ""
}

func (startIssue) Execute(
	ctx context.Context, tx *ent.Client, e *ent.Issue, _ actions.None,
) (contract.Issue, error) {
	return transition(ctx, tx, e, entissue.StatusDoing)
}

// close ---------------------------------------------------------------------

type closeIssue struct{ actions.Default[*ent.Issue] }

func (closeIssue) Key() contract.IssueKey { return contract.KeyIssueClose }
func (closeIssue) Label() string          { return "close" }

func (closeIssue) IsAvailable(e *ent.Issue) bool {
	return IssueStateMachine.Can(e, entissue.StatusDone)
}

// A parent cannot be closed out from over its children.
func (closeIssue) IsDisabled(e *ent.Issue) string {
	for _, s := range e.Edges.Subtasks {
		if s.Status != entissue.StatusDone {
			return reasonOpenSubtasks
		}
	}
	return ""
}

func (closeIssue) Execute(
	ctx context.Context, tx *ent.Client, e *ent.Issue, _ actions.None,
) (contract.Issue, error) {
	return transition(ctx, tx, e, entissue.StatusDone)
}

// reopen --------------------------------------------------------------------

type reopenIssue struct{ actions.Default[*ent.Issue] }

func (reopenIssue) Key() contract.IssueKey { return contract.KeyIssueReopen }
func (reopenIssue) Label() string          { return "reopen" }

func (reopenIssue) IsAvailable(e *ent.Issue) bool {
	return IssueStateMachine.Can(e, entissue.StatusTodo)
}

func (reopenIssue) Execute(
	ctx context.Context, tx *ent.Client, e *ent.Issue, _ actions.None,
) (contract.Issue, error) {
	return transition(ctx, tx, e, entissue.StatusTodo)
}

// edit ----------------------------------------------------------------------

type editIssue struct{ actions.Default[*ent.Issue] }

func (editIssue) Key() contract.IssueKey { return contract.KeyIssueEdit }
func (editIssue) Label() string          { return "edit" }

func (editIssue) Execute(
	ctx context.Context, tx *ent.Client, e *ent.Issue, p contract.IssueEditParams,
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

// set priority --------------------------------------------------------------

type setIssuePriority struct{ actions.Default[*ent.Issue] }

func (setIssuePriority) Key() contract.IssueKey { return contract.KeyIssueSetPriority }
func (setIssuePriority) Label() string          { return "set priority" }

func (setIssuePriority) Execute(
	ctx context.Context, tx *ent.Client, e *ent.Issue, p contract.Priority,
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

// set milestone -------------------------------------------------------------

type setIssueMilestone struct{ actions.Default[*ent.Issue] }

func (setIssueMilestone) Key() contract.IssueKey { return contract.KeyIssueSetMilestone }
func (setIssueMilestone) Label() string          { return "set milestone" }

// An empty name clears the milestone; anything else creates it on first use.
func (setIssueMilestone) Execute(
	ctx context.Context, tx *ent.Client, e *ent.Issue, name string,
) (contract.Issue, error) {
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

// add label -----------------------------------------------------------------

type addIssueLabel struct{ actions.Default[*ent.Issue] }

func (addIssueLabel) Key() contract.IssueKey { return contract.KeyIssueAddLabel }
func (addIssueLabel) Label() string          { return "add label" }

func (addIssueLabel) Execute(
	ctx context.Context, tx *ent.Client, e *ent.Issue, name string,
) (contract.Issue, error) {
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

// remove label --------------------------------------------------------------

type removeIssueLabel struct{ actions.Default[*ent.Issue] }

func (removeIssueLabel) Key() contract.IssueKey { return contract.KeyIssueRemoveLabel }
func (removeIssueLabel) Label() string          { return "remove label" }

// Absent rather than refused: with no labels on the issue there is nothing the
// action could even prompt for.
func (removeIssueLabel) IsAvailable(e *ent.Issue) bool {
	return len(e.Edges.Labels) > 0
}

func (removeIssueLabel) Execute(
	ctx context.Context, tx *ent.Client, e *ent.Issue, name string,
) (contract.Issue, error) {
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

// add sub-issue -------------------------------------------------------------

type addSubIssue struct{ actions.Default[*ent.Issue] }

func (addSubIssue) Key() contract.IssueKey { return contract.KeyIssueAddSubIssue }
func (addSubIssue) Label() string          { return "add sub-issue" }

// The child lands in its parent's project whatever the params say: a sub-issue
// filed somewhere else would leave the parent's project showing a rollup over
// work it does not contain.
func (addSubIssue) Execute(
	ctx context.Context, tx *ent.Client, e *ent.Issue, p contract.IssueAddParams,
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

// add dependency ------------------------------------------------------------

type addIssueDep struct{ actions.Default[*ent.Issue] }

func (addIssueDep) Key() contract.IssueKey { return contract.KeyIssueAddDep }
func (addIssueDep) Label() string          { return "add dependency" }

// The cycle check walks the dependency graph, so it is not a rule: a rule reads
// one level down and this needs however many the chain is deep.
func (addIssueDep) Execute(
	ctx context.Context, tx *ent.Client, e *ent.Issue, blockerID int,
) (contract.Issue, error) {
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

// remove dependency ---------------------------------------------------------

type removeIssueDep struct{ actions.Default[*ent.Issue] }

func (removeIssueDep) Key() contract.IssueKey { return contract.KeyIssueRemoveDep }
func (removeIssueDep) Label() string          { return "remove dependency" }

// Absent when there are none, for the same reason remove label is.
func (removeIssueDep) IsAvailable(e *ent.Issue) bool {
	return len(e.Edges.BlockedBy) > 0
}

func (removeIssueDep) Execute(
	ctx context.Context, tx *ent.Client, e *ent.Issue, blockerID int,
) (contract.Issue, error) {
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

// comment -------------------------------------------------------------------

type commentOnIssue struct{ actions.Default[*ent.Issue] }

func (commentOnIssue) Key() contract.IssueKey { return contract.KeyIssueComment }
func (commentOnIssue) Label() string          { return "comment" }

func (commentOnIssue) Execute(
	ctx context.Context, tx *ent.Client, e *ent.Issue, body string,
) (contract.Issue, error) {
	if _, err := comments.AddIn(ctx, tx, e.ID, body); err != nil {
		return contract.Issue{}, err
	}
	return Load(ctx, tx, e.ID)
}

// delete --------------------------------------------------------------------

type deleteIssue struct{ actions.Default[*ent.Issue] }

func (deleteIssue) Key() contract.IssueKey { return contract.KeyIssueDelete }
func (deleteIssue) Label() string          { return "delete" }

// Comments and refs go by cascade, and sub-issues survive with their parent_id
// cleared: deleting a parent should not delete work nobody asked to lose.
//
// The zero issue comes back because there is none left, and every action in a
// group returns the same type.
func (deleteIssue) Execute(
	ctx context.Context, tx *ent.Client, e *ent.Issue, _ actions.None,
) (contract.Issue, error) {
	if err := tx.Issue.DeleteOne(e).Exec(ctx); err != nil {
		return contract.Issue{}, fmt.Errorf("deleting issue %d: %w", e.ID, err)
	}
	return contract.Issue{}, nil
}

// the menu ------------------------------------------------------------------

// The actions an issue offers, as handles that keep their payload type.
var (
	Start        = actions.Bind(startIssue{})
	Close        = actions.Bind(closeIssue{})
	Reopen       = actions.Bind(reopenIssue{})
	Edit         = actions.Bind(editIssue{})
	SetPriority  = actions.Bind(setIssuePriority{})
	SetMilestone = actions.Bind(setIssueMilestone{})
	AddLabel     = actions.Bind(addIssueLabel{})
	RemoveLabel  = actions.Bind(removeIssueLabel{})
	AddSubIssue  = actions.Bind(addSubIssue{})
	AddDep       = actions.Bind(addIssueDep{})
	RemoveDep    = actions.Bind(removeIssueDep{})
	Comment      = actions.Bind(commentOnIssue{})
	Delete       = actions.Bind(deleteIssue{})
)

// menu is every action above, in the order a frontend shows them. It is the
// only place a rule is checked: Actions renders it and every write goes through
// it.
//
// Assigned in init rather than at its declaration: an action's Execute reloads
// through Load, which converts, which asks for the menu. Nothing reads menu
// while the package is initialising, but the initializer analysis sees the loop
// and rejects it, so the assignment has to sit where that analysis does not run.
var menu *actions.Group[contract.IssueKey, *ent.Issue, contract.Issue]

func init() {
	menu = actions.NewGroup(
		func(e *ent.Issue) string { return fmt.Sprintf("issue %d", e.ID) },
		Start.Entry(),
		Close.Entry(),
		Reopen.Entry(),
		Edit.Entry(),
		SetPriority.Entry(),
		SetMilestone.Entry(),
		AddLabel.Entry(),
		RemoveLabel.Entry(),
		AddSubIssue.Entry(),
		AddDep.Entry(),
		RemoveDep.Entry(),
		Comment.Entry(),
		Delete.Entry(),
	)
}

// Actions is the menu for one issue: a loaded row in, a menu out.
func Actions(e *ent.Issue) []contract.Action[contract.IssueKey] {
	return menu.Menu(e)
}

// blocked reports whether any of this issue's blockers is still open.
func blocked(e *ent.Issue) bool {
	for _, b := range e.Edges.BlockedBy {
		if b.Status != entissue.StatusDone {
			return true
		}
	}
	return false
}
