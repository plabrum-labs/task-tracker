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

// action is an action on an issue taking a payload of type P. The alias is what
// keeps the four type parameters off every declaration in this file.
type action[P any] = actions.Action[contract.IssueKey, *ent.Issue, P, contract.Issue]

// issueActions is every action an issue has. It is the only place a rule is
// checked: Actions reads it and every write goes through it.
var issueActions = actions.NewGroup[contract.IssueKey, *ent.Issue, contract.Issue](
	func(e *ent.Issue) string { return fmt.Sprintf("issue %d", e.ID) },
)

// Actions is what one issue offers: a loaded row in, its actions out.
func Actions(e *ent.Issue) []contract.Action[contract.IssueKey] {
	return issueActions.Available(e)
}

// The refusals a rule can carry. Each is also what the matching write returns
// wrapped in errs.ErrConflict, so a frontend never sees two wordings for one
// rule.
const (
	reasonBlocked      = "blocked by open issues"
	reasonOpenSubtasks = "open sub-issues"
)

// Start picks work up.
var Start = actions.Register(issueActions, action[actions.None]{
	Key:         contract.KeyIssueStart,
	Label:       "start",
	Priority:    10,
	IsAvailable: canStart,
	IsDisabled:  blockedReason,
	Execute:     toDoing,
})

// Close marks work done.
var Close = actions.Register(issueActions, action[actions.None]{
	Key:         contract.KeyIssueClose,
	Label:       "close",
	Priority:    20,
	IsAvailable: canClose,
	IsDisabled:  openSubtaskReason,
	Execute:     toDone,
})

// Reopen brings finished work back.
var Reopen = actions.Register(issueActions, action[actions.None]{
	Key:         contract.KeyIssueReopen,
	Label:       "reopen",
	Priority:    30,
	IsAvailable: canReopen,
	Execute:     toTodo,
})

// Edit changes an issue's title or body.
var Edit = actions.Register(issueActions, action[contract.IssueEditParams]{
	Key:      contract.KeyIssueEdit,
	Label:    "edit",
	Priority: 40,
	Execute:  edit,
})

// SetPriority changes an issue's pick-order bump.
var SetPriority = actions.Register(issueActions, action[contract.Priority]{
	Key:      contract.KeyIssueSetPriority,
	Label:    "set priority",
	Priority: 50,
	Execute:  setPriority,
})

// SetMilestone files an issue under a milestone. An empty name clears it.
var SetMilestone = actions.Register(issueActions, action[string]{
	Key:      contract.KeyIssueSetMilestone,
	Label:    "set milestone",
	Priority: 60,
	Execute:  setMilestone,
})

// AddLabel puts a label on an issue, creating it on first use.
var AddLabel = actions.Register(issueActions, action[string]{
	Key:      contract.KeyIssueAddLabel,
	Label:    "add label",
	Priority: 70,
	Execute:  addLabel,
})

// RemoveLabel takes a label off an issue. Absent rather than refused when the
// issue has no labels: there would be nothing for it to prompt for.
var RemoveLabel = actions.Register(issueActions, action[string]{
	Key:         contract.KeyIssueRemoveLabel,
	Label:       "remove label",
	Priority:    80,
	IsAvailable: hasLabels,
	Execute:     removeLabel,
})

// AddSubIssue captures a new issue as a child of this one.
var AddSubIssue = actions.Register(issueActions, action[contract.IssueAddParams]{
	Key:      contract.KeyIssueAddSubIssue,
	Label:    "add sub-issue",
	Priority: 90,
	Execute:  addSubIssue,
})

// AddDep records that an issue waits on another.
var AddDep = actions.Register(issueActions, action[int]{
	Key:      contract.KeyIssueAddDep,
	Label:    "add dependency",
	Priority: 100,
	Execute:  addDep,
})

// RemoveDep drops a dependency. Absent when there are none, for the same reason
// RemoveLabel is.
var RemoveDep = actions.Register(issueActions, action[int]{
	Key:         contract.KeyIssueRemoveDep,
	Label:       "remove dependency",
	Priority:    110,
	IsAvailable: hasBlockers,
	Execute:     removeDep,
})

// Comment appends a comment to an issue.
var Comment = actions.Register(issueActions, action[string]{
	Key:      contract.KeyIssueComment,
	Label:    "comment",
	Priority: 120,
	Execute:  comment,
})

// Delete removes an issue.
var Delete = actions.Register(issueActions, action[actions.None]{
	Key:      contract.KeyIssueDelete,
	Label:    "delete",
	Priority: 130,
	Execute:  deleteIssue,
})

// The rules.
//
// Every fact one reads comes off an edge withActionEdges eager-loads: an
// unloaded edge is an empty one as far as a rule can tell, so a row that did not
// come through that loader gets rules that are wrong rather than a query that
// fails. The three transitions ask IssueStateMachine rather than restating its
// topology.

func canStart(e *ent.Issue) bool  { return IssueStateMachine.Can(e, entissue.StatusDoing) }
func canClose(e *ent.Issue) bool  { return IssueStateMachine.Can(e, entissue.StatusDone) }
func canReopen(e *ent.Issue) bool { return IssueStateMachine.Can(e, entissue.StatusTodo) }

func hasLabels(e *ent.Issue) bool   { return len(e.Edges.Labels) > 0 }
func hasBlockers(e *ent.Issue) bool { return len(e.Edges.BlockedBy) > 0 }

// blocked reports whether any of this issue's blockers is still open. It is
// also the Blocked badge a contract issue carries.
func blocked(e *ent.Issue) bool {
	for _, b := range e.Edges.BlockedBy {
		if b.Status != entissue.StatusDone {
			return true
		}
	}
	return false
}

// blockedReason refuses starting work that something else has to come first.
func blockedReason(e *ent.Issue) string {
	if blocked(e) {
		return reasonBlocked
	}
	return ""
}

// openSubtaskReason refuses closing a parent out from over its children.
func openSubtaskReason(e *ent.Issue) string {
	for _, s := range e.Edges.Subtasks {
		if s.Status != entissue.StatusDone {
			return reasonOpenSubtasks
		}
	}
	return ""
}

// The writes. Each is handed a loaded row its rules have already cleared, so
// none of them restates one; what they do restate — an archived project, a
// dependency cycle — is what a rule cannot decide, because it needs a query.

func toDoing(ctx context.Context, tx *ent.Client, e *ent.Issue, _ actions.None) (contract.Issue, error) {
	return transition(ctx, tx, e, entissue.StatusDoing)
}

func toDone(ctx context.Context, tx *ent.Client, e *ent.Issue, _ actions.None) (contract.Issue, error) {
	return transition(ctx, tx, e, entissue.StatusDone)
}

func toTodo(ctx context.Context, tx *ent.Client, e *ent.Issue, _ actions.None) (contract.Issue, error) {
	return transition(ctx, tx, e, entissue.StatusTodo)
}

// transition moves an issue and reloads it. The machine owns what a move
// implies — the closed_at stamp is its states', not this function's.
func transition(
	ctx context.Context, tx *ent.Client, e *ent.Issue, to entissue.Status,
) (contract.Issue, error) {
	if err := IssueStateMachine.Transition(ctx, tx, e, to); err != nil {
		return contract.Issue{}, err
	}
	return Load(ctx, tx, e.ID)
}

func edit(
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

func setPriority(
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

// addSubIssue files a new issue under e. The child lands in its parent's
// project whatever the params say: a sub-issue filed somewhere else would leave
// the parent's project showing a rollup over work it does not contain.
//
// The issue it returns is the child, not the parent it was filed under.
func addSubIssue(
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

// addDep records that an issue waits on another.
//
// The cycle check walks the dependency graph, so it is not a rule: a rule reads
// one level down and this needs however many the chain is deep.
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

func comment(ctx context.Context, tx *ent.Client, e *ent.Issue, body string) (contract.Issue, error) {
	if _, err := comments.AddIn(ctx, tx, e.ID, body); err != nil {
		return contract.Issue{}, err
	}
	return Load(ctx, tx, e.ID)
}

// deleteIssue drops an issue. Its comments and refs go with it by cascade, and
// its sub-issues survive with their parent_id cleared: deleting a parent should
// not delete work nobody asked to lose.
//
// The zero issue comes back because there is none left, and every action in a
// group returns the same type.
func deleteIssue(ctx context.Context, tx *ent.Client, e *ent.Issue, _ actions.None) (contract.Issue, error) {
	if err := tx.Issue.DeleteOne(e).Exec(ctx); err != nil {
		return contract.Issue{}, fmt.Errorf("deleting issue %d: %w", e.ID, err)
	}
	return contract.Issue{}, nil
}
