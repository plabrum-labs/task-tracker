package issues

import (
	"context"
	"fmt"
	"time"

	"github.com/Plabrum/tt/backend/contract"
	"github.com/Plabrum/tt/backend/internal/actions"
	"github.com/Plabrum/tt/backend/internal/ent"
	entissue "github.com/Plabrum/tt/backend/internal/ent/issue"
	"github.com/Plabrum/tt/backend/internal/statemachine"
)

// The refusals a menu can carry. Each is also what the matching write returns
// wrapped in errs.ErrConflict, so a frontend never sees two wordings for one
// rule.
const (
	reasonBlocked      = "blocked by open issues"
	reasonOpenSubtasks = "open sub-issues"
)

// machine is the issue lifecycle:
//
//	todo -[start]-> doing -[close]-> done -[reopen]-> todo
//
// with a close straight from todo, for work finished without ever being picked
// up. closed_at is the state's business rather than each write's: arriving at
// done stamps it and arriving anywhere else clears it, so no write can move an
// issue and leave the stamp saying otherwise.
var machine = statemachine.New(
	func(e *ent.Issue) entissue.Status { return e.Status },
	func(ctx context.Context, tx *ent.Client, e *ent.Issue, to entissue.Status) error {
		if err := tx.Issue.UpdateOne(e).SetStatus(to).Exec(ctx); err != nil {
			return fmt.Errorf("setting status of issue %d to %s: %w", e.ID, to, err)
		}
		return nil
	},
	map[entissue.Status]statemachine.State[entissue.Status, *ent.Issue]{
		entissue.StatusTodo: {
			To:      []entissue.Status{entissue.StatusDoing, entissue.StatusDone},
			OnEnter: clearClosedAt,
		},
		entissue.StatusDoing: {
			To:      []entissue.Status{entissue.StatusDone},
			OnEnter: clearClosedAt,
		},
		entissue.StatusDone: {
			To:      []entissue.Status{entissue.StatusTodo},
			OnEnter: stampClosedAt,
		},
	},
)

// The actions an issue offers.
//
// A transition's Applies is the machine's own topology, asked rather than
// restated, so the states are declared once and both the menu and the write
// read them from there. Every other fact a rule reads comes off an edge
// withMenuEdges eager-loads: an unloaded edge is an empty one as far as these
// can tell, so a row that did not come through that loader gets a menu that is
// wrong rather than a query that fails.
var (
	// Start picks work up.
	Start = actions.Define(actions.Spec[contract.IssueKey, *ent.Issue]{
		Key:     contract.KeyIssueStart,
		Label:   "start",
		Applies: canMoveTo(entissue.StatusDoing),
		Refuse:  blockedReason,
	}, transitionTo(entissue.StatusDoing))

	// Close marks work done.
	Close = actions.Define(actions.Spec[contract.IssueKey, *ent.Issue]{
		Key:     contract.KeyIssueClose,
		Label:   "close",
		Applies: canMoveTo(entissue.StatusDone),
		Refuse:  openSubtaskReason,
	}, transitionTo(entissue.StatusDone))

	// Reopen brings finished work back.
	Reopen = actions.Define(actions.Spec[contract.IssueKey, *ent.Issue]{
		Key:     contract.KeyIssueReopen,
		Label:   "reopen",
		Applies: canMoveTo(entissue.StatusTodo),
	}, transitionTo(entissue.StatusTodo))

	// Edit changes an issue's title or body.
	Edit = actions.Define(actions.Spec[contract.IssueKey, *ent.Issue]{
		Key:   contract.KeyIssueEdit,
		Label: "edit",
	}, edit)

	// SetPriority changes an issue's pick-order bump.
	SetPriority = actions.Define(actions.Spec[contract.IssueKey, *ent.Issue]{
		Key:   contract.KeyIssueSetPriority,
		Label: "set priority",
	}, setPriority)

	// SetMilestone files an issue under a milestone. An empty name clears it.
	SetMilestone = actions.Define(actions.Spec[contract.IssueKey, *ent.Issue]{
		Key:   contract.KeyIssueSetMilestone,
		Label: "set milestone",
	}, setMilestone)

	// AddLabel puts a label on an issue, creating it on first use.
	AddLabel = actions.Define(actions.Spec[contract.IssueKey, *ent.Issue]{
		Key:   contract.KeyIssueAddLabel,
		Label: "add label",
	}, addLabel)

	// RemoveLabel takes a label off an issue. Absent rather than refused when
	// the issue has no labels: there would be nothing for it to prompt for.
	RemoveLabel = actions.Define(actions.Spec[contract.IssueKey, *ent.Issue]{
		Key:     contract.KeyIssueRemoveLabel,
		Label:   "remove label",
		Applies: func(e *ent.Issue) bool { return len(e.Edges.Labels) > 0 },
	}, removeLabel)

	// AddSubIssue captures a new issue as a child of this one.
	AddSubIssue = actions.Define(actions.Spec[contract.IssueKey, *ent.Issue]{
		Key:   contract.KeyIssueAddSubIssue,
		Label: "add sub-issue",
	}, addSubIssue)

	// AddDep records that an issue waits on another.
	AddDep = actions.Define(actions.Spec[contract.IssueKey, *ent.Issue]{
		Key:   contract.KeyIssueAddDep,
		Label: "add dependency",
	}, addDep)

	// RemoveDep drops a dependency. Absent when there are none, for the same
	// reason RemoveLabel is.
	RemoveDep = actions.Define(actions.Spec[contract.IssueKey, *ent.Issue]{
		Key:     contract.KeyIssueRemoveDep,
		Label:   "remove dependency",
		Applies: func(e *ent.Issue) bool { return len(e.Edges.BlockedBy) > 0 },
	}, removeDep)

	// Comment appends a comment.
	Comment = actions.Define(actions.Spec[contract.IssueKey, *ent.Issue]{
		Key:   contract.KeyIssueComment,
		Label: "comment",
	}, comment)

	// Delete removes an issue.
	Delete = actions.Define(actions.Spec[contract.IssueKey, *ent.Issue]{
		Key:   contract.KeyIssueDelete,
		Label: "delete",
	}, remove)
)

// menu is every action above, in the order a frontend shows them. It is the
// only place a rule is checked: Actions renders it and every write goes through
// it.
//
// Assigned in init rather than at its declaration: an action's write reloads
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

// canMoveTo is a transition's Applies: the machine's topology, asked rather
// than restated.
func canMoveTo(to entissue.Status) func(*ent.Issue) bool {
	return func(e *ent.Issue) bool { return machine.Can(e, to) }
}

// transitionTo is the write behind a transition: move, then reload.
func transitionTo(
	to entissue.Status,
) func(context.Context, *ent.Client, *ent.Issue, actions.None) (contract.Issue, error) {
	return func(ctx context.Context, tx *ent.Client, e *ent.Issue, _ actions.None) (contract.Issue, error) {
		if err := machine.Transition(ctx, tx, e, to); err != nil {
			return contract.Issue{}, err
		}
		return Load(ctx, tx, e.ID)
	}
}

// stampClosedAt records when an issue reached done.
func stampClosedAt(ctx context.Context, tx *ent.Client, e *ent.Issue, _ entissue.Status) error {
	if err := tx.Issue.UpdateOne(e).SetClosedAt(time.Now()).Exec(ctx); err != nil {
		return fmt.Errorf("stamping issue %d closed: %w", e.ID, err)
	}
	return nil
}

// clearClosedAt drops the stamp off an issue that is open again.
func clearClosedAt(ctx context.Context, tx *ent.Client, e *ent.Issue, _ entissue.Status) error {
	if err := tx.Issue.UpdateOne(e).ClearClosedAt().Exec(ctx); err != nil {
		return fmt.Errorf("clearing the closed stamp on issue %d: %w", e.ID, err)
	}
	return nil
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
