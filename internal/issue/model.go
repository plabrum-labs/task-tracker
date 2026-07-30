// Package issue owns the unit of work: the view types every frontend renders,
// and the functions that create and read them.
//
// The types in this file import no ent, so a schema change cannot reshape
// --json without going through the conversions in enum.go.
package issue

import (
	"strings"
	"time"

	"github.com/Plabrum/tt/internal/errs"
)

// Status is the lifecycle position of an issue.
type Status string

const (
	StatusTodo  Status = "todo"
	StatusDoing Status = "doing"
	StatusDone  Status = "done"
)

// Priority is the pick-order bump.
type Priority string

const (
	PriorityNormal Priority = "normal"
	PriorityHi     Priority = "hi"
)

// Kind distinguishes a plain prerequisite from a decomposition. Both block
// identically; the difference is only in how they render.
type Kind string

const (
	KindDep     Kind = "dep"
	KindSubtask Kind = "subtask"
)

// Issue is one task, flattened for display. Project and Milestone are names
// rather than ids, so no renderer needs a second call to print a line.
type Issue struct {
	ID        int        `json:"id"`
	Title     string     `json:"title"`
	Body      string     `json:"body"`
	Status    Status     `json:"status"`
	Priority  Priority   `json:"priority"`
	Project   string     `json:"project"`   // slug; -A lists across projects
	Milestone string     `json:"milestone"` // name, "" when none
	Labels    []string   `json:"labels"`    // sorted, never nil
	CreatedAt time.Time  `json:"created_at"`
	UpdatedAt time.Time  `json:"updated_at"`
	ClosedAt  *time.Time `json:"closed_at"` // nil when open
}

// Link is one end of a ref, flattened for display.
type Link struct {
	ID     int    `json:"id"`
	Title  string `json:"title"`
	Status Status `json:"status"`
	Kind   Kind   `json:"kind"`
}

// Comment is one entry in an issue's log.
type Comment struct {
	ID     int       `json:"id"`
	At     time.Time `json:"at"`
	Author string    `json:"author"`
	Body   string    `json:"body"`
}

// Rollup is the 3/5 subtask badge. Total == 0 renders nothing.
type Rollup struct {
	Done  int `json:"done"`
	Total int `json:"total"`
}

// Detail is one issue with everything hanging off it.
//
// Issue is embedded rather than nested so that `tt show 12 --json | jq .title`
// and `tt ls --json | jq .[0].title` read the same.
type Detail struct {
	Issue
	Blockers   []Link    `json:"blockers"`   // kind = dep
	Subtasks   []Link    `json:"subtasks"`   // kind = subtask; show lists these separately
	Dependents []Link    `json:"dependents"` // reverse edge, both kinds
	Comments   []Comment `json:"comments"`
	Rollup     Rollup    `json:"rollup"`
}

// AddParams is the shape `tt add`'s flags and the TUI's prompt both fill in.
// It is not a JSON type — it is an input, and inputs get to change.
type AddParams struct {
	Project   string // resolved slug; required. Nothing below the frontends reads the cwd.
	Title     string
	Body      string   // -b, or the $EDITOR buffer from -e
	Priority  Priority // -! ; the zero value normalises to PriorityNormal
	Labels    []string // -l, repeatable
	Milestone string   // -M
	SubOf     *int     // --sub-of; nil = none
}

// ParseStatus converts user input to a Status. Shared by the CLI's flags and
// the TUI's filter so the two cannot accept different spellings.
func ParseStatus(s string) (Status, error) {
	switch Status(strings.ToLower(strings.TrimSpace(s))) {
	case StatusTodo:
		return StatusTodo, nil
	case StatusDoing:
		return StatusDoing, nil
	case StatusDone:
		return StatusDone, nil
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

// validate checks what the caller supplied, before anything is written.
func (p AddParams) validate() error {
	if strings.TrimSpace(p.Project) == "" {
		return errs.Invalidf("project is required")
	}
	if strings.TrimSpace(p.Title) == "" {
		return errs.Invalidf("title is required")
	}
	switch p.Priority {
	case "", PriorityNormal, PriorityHi:
	default:
		return errs.Invalidf("unknown priority %q: want normal or hi", p.Priority)
	}
	return nil
}
