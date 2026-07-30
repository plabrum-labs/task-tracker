package issues

import (
	"github.com/Plabrum/tt/backend/contract"
	"github.com/Plabrum/tt/backend/internal/ent"
	entissue "github.com/Plabrum/tt/backend/internal/ent/issue"
)

// The refusals a menu can carry. Each is also what the matching write returns
// wrapped in errs.ErrConflict, so a frontend never sees two wordings for one
// rule.
const (
	reasonBlocked      = "blocked by open issues"
	reasonOpenSubtasks = "open sub-issues"
)

// Actions is the menu for one issue.
//
// Pure: a loaded row in, a menu out. No context, no client, no error, no
// queries. A rule that needs a query is not a menu rule and belongs in
// service.go — the cycle check on AddDep is the example.
//
// Every fact read here comes off an edge that withMenuEdges eager-loads. An
// unloaded edge is an empty one as far as this file can tell, so a row that did
// not come through that loader gets a menu that is wrong rather than a query
// that fails.
func Actions(e *ent.Issue) []contract.Action[contract.IssueKey] {
	out := make([]contract.Action[contract.IssueKey], 0, 13)
	add := func(key contract.IssueKey, label, reason string) {
		out = append(out, contract.Action[contract.IssueKey]{Key: key, Label: label, Reason: reason})
	}

	// The transition topology, as a switch on the current value rather than a
	// map: every status says exactly which moves exist from it.
	switch e.Status {
	case entissue.StatusTodo:
		add(contract.KeyIssueStart, "start", blockedReason(e))
		add(contract.KeyIssueClose, "close", openSubtaskReason(e))
	case entissue.StatusDoing:
		add(contract.KeyIssueClose, "close", openSubtaskReason(e))
	case entissue.StatusDone:
		add(contract.KeyIssueReopen, "reopen", "")
	}

	add(contract.KeyIssueEdit, "edit", "")
	add(contract.KeyIssueSetPriority, "set priority", "")
	add(contract.KeyIssueSetMilestone, "set milestone", "")
	add(contract.KeyIssueAddLabel, "add label", "")
	// Absent rather than refused: with no labels on the issue there is nothing
	// the action could even prompt for.
	if len(e.Edges.Labels) > 0 {
		add(contract.KeyIssueRemoveLabel, "remove label", "")
	}
	add(contract.KeyIssueAddSubIssue, "add sub-issue", "")
	add(contract.KeyIssueAddDep, "add dependency", "")
	if len(e.Edges.BlockedBy) > 0 {
		add(contract.KeyIssueRemoveDep, "remove dependency", "")
	}
	add(contract.KeyIssueComment, "comment", "")
	add(contract.KeyIssueDelete, "delete", "")

	return out
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
