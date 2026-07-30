package issues

import (
	"github.com/Plabrum/tt/backend/internal/ent"
	entissue "github.com/Plabrum/tt/backend/internal/ent/issue"
	"github.com/Plabrum/tt/backend/models"
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
func Actions(e *ent.Issue) []models.Action[models.IssueKey] {
	out := make([]models.Action[models.IssueKey], 0, 13)
	add := func(key models.IssueKey, label, reason string) {
		out = append(out, models.Action[models.IssueKey]{Key: key, Label: label, Reason: reason})
	}

	// The transition topology, as a switch on the current value rather than a
	// map: every status says exactly which moves exist from it.
	switch e.Status {
	case entissue.StatusTodo:
		add(models.KeyIssueStart, "start", blockedReason(e))
		add(models.KeyIssueClose, "close", openSubtaskReason(e))
	case entissue.StatusDoing:
		add(models.KeyIssueClose, "close", openSubtaskReason(e))
	case entissue.StatusDone:
		add(models.KeyIssueReopen, "reopen", "")
	}

	add(models.KeyIssueEdit, "edit", "")
	add(models.KeyIssueSetPriority, "set priority", "")
	add(models.KeyIssueSetMilestone, "set milestone", "")
	add(models.KeyIssueAddLabel, "add label", "")
	// Absent rather than refused: with no labels on the issue there is nothing
	// the action could even prompt for.
	if len(e.Edges.Labels) > 0 {
		add(models.KeyIssueRemoveLabel, "remove label", "")
	}
	add(models.KeyIssueAddSubIssue, "add sub-issue", "")
	add(models.KeyIssueAddDep, "add dependency", "")
	if len(e.Edges.BlockedBy) > 0 {
		add(models.KeyIssueRemoveDep, "remove dependency", "")
	}
	add(models.KeyIssueComment, "comment", "")
	add(models.KeyIssueDelete, "delete", "")

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
