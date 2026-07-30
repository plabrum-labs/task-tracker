package contract

import (
	"strings"
	"time"

	"github.com/Plabrum/tt/backend/errs"
)

// IssueStatus is the lifecycle position of an issue.
type IssueStatus string

// The statuses an issue moves between.
const (
	IssueTodo  IssueStatus = "todo"
	IssueDoing IssueStatus = "doing"
	IssueDone  IssueStatus = "done"
)

// Priority is the pick-order bump.
type Priority string

// The priorities an issue can carry.
const (
	PriorityNormal Priority = "normal"
	PriorityHi     Priority = "hi"
)

// IssueKey names one of the actions an issue offers.
type IssueKey string

// The actions an issue can offer.
const (
	KeyIssueStart        IssueKey = "start"
	KeyIssueClose        IssueKey = "close"
	KeyIssueReopen       IssueKey = "reopen"
	KeyIssueEdit         IssueKey = "edit"
	KeyIssueDelete       IssueKey = "delete"
	KeyIssueSetPriority  IssueKey = "set-priority"
	KeyIssueSetMilestone IssueKey = "set-milestone"
	KeyIssueAddLabel     IssueKey = "add-label"
	KeyIssueRemoveLabel  IssueKey = "remove-label"
	KeyIssueAddSubIssue  IssueKey = "add-sub-issue"
	KeyIssueAddDep       IssueKey = "add-dep"
	KeyIssueRemoveDep    IssueKey = "remove-dep"
	KeyIssueComment      IssueKey = "comment"
)

// Issue is the unit of work.
//
// The graph is recursive, so loading stops one level down: the Issue values in
// Parent, Subtasks, Blockers and Blocks have their scalars, Project, Milestone,
// Labels, Blocked and Actions populated, and their own relations empty.
type Issue struct {
	ID        int         `json:"id"`
	Title     string      `json:"title"`
	Body      string      `json:"body"`
	Status    IssueStatus `json:"status"`
	Priority  Priority    `json:"priority"`
	CreatedAt time.Time   `json:"created_at"`
	UpdatedAt time.Time   `json:"updated_at"`
	ClosedAt  *time.Time  `json:"closed_at"` // nil when open

	Project   Project    `json:"project"`
	Milestone *Milestone `json:"milestone"` // nil when none
	Labels    []Label    `json:"labels"`    // sorted by name

	Parent   *Issue  `json:"parent"`   // nil unless this is a sub-issue
	Subtasks []Issue `json:"subtasks"` // sub-issues of this one
	Blockers []Issue `json:"blockers"` // must close before this starts
	Blocks   []Issue `json:"blocks"`   // the reverse edge

	Comments []Comment `json:"comments"` // oldest first

	Blocked bool `json:"blocked"` // any blocker not done — the ⊘ badge

	Actions []Action[IssueKey] `json:"-"`
}

// IssueListParams is every filter a list can apply.
//
// Issues in archived projects are excluded unless Project names one: archiving
// is what takes a project's work out of the way.
type IssueListParams struct {
	Project     string        // "" means every active project
	Statuses    []IssueStatus // empty means todo+doing
	Labels      []string      // ANDed
	Milestone   string
	Search      string // case-insensitive substring of the title
	BlockedOnly bool
	Limit       int // 0 = no limit
}

// IssueAddParams is what `tt add` and the TUI's prompt both fill in. It is an
// input, not a contract type, and inputs get to change.
type IssueAddParams struct {
	Project   string // resolved slug, required
	Title     string // required
	Body      string
	Priority  Priority // "" normalises to PriorityNormal
	Labels    []string
	Milestone string
}

// IssueEditParams is a partial update. A nil field is left unchanged.
type IssueEditParams struct {
	Title *string
	Body  *string
}

// ParseIssueStatus converts user input to an IssueStatus. Shared by the CLI's
// flags and the TUI's filter so the two cannot accept different spellings.
func ParseIssueStatus(s string) (IssueStatus, error) {
	switch IssueStatus(strings.ToLower(strings.TrimSpace(s))) {
	case IssueTodo:
		return IssueTodo, nil
	case IssueDoing:
		return IssueDoing, nil
	case IssueDone:
		return IssueDone, nil
	default:
		return "", errs.Invalidf("unknown status %q: want todo, doing or done", s)
	}
}

// ParsePriority converts user input to a Priority.
func ParsePriority(s string) (Priority, error) {
	switch Priority(strings.ToLower(strings.TrimSpace(s))) {
	case PriorityNormal:
		return PriorityNormal, nil
	case PriorityHi:
		return PriorityHi, nil
	default:
		return "", errs.Invalidf("unknown priority %q: want normal or hi", s)
	}
}

// Validate checks what the caller supplied, before anything is written.
func (p IssueAddParams) Validate() error {
	if strings.TrimSpace(p.Project) == "" {
		return errs.Invalidf("project is required")
	}
	if strings.TrimSpace(p.Title) == "" {
		return errs.Invalidf("title is required")
	}
	switch p.Priority {
	case "", PriorityNormal, PriorityHi:
		return nil
	default:
		return errs.Invalidf("unknown priority %q: want normal or hi", p.Priority)
	}
}
