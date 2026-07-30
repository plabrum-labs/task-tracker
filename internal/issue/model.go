// Package issue owns the unit of work: the view types every frontend renders,
// and the service that creates and reads them.
//
// The types in this file are the contract. They deliberately import no ent, so
// cli/ and ui/ can write `row.Status == issue.StatusDone` without depending on
// the storage layer, and so a schema change cannot silently reshape --json.
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

// Priority is the pick-order bump. Two levels, because a third would need a
// rule for when to use it and there isn't one.
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

// Issue is one task, flattened for display. Every field is resolved: Project
// and Milestone are names, not ids, because no renderer should have to make a
// second call to print a line.
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
	ClosedAt  *time.Time `json:"closed_at"` // the only pointer: genuinely absent
}

// Link is one end of a ref, flattened for display: enough to print a line
// without a second lookup, and no more.
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

// Rollup is the 3/5 subtask badge. Total == 0 means "render nothing", which is
// exactly what a zero value gives you.
type Rollup struct {
	Done  int `json:"done"`
	Total int `json:"total"`
}

// Detail is one issue with everything hanging off it.
//
// Issue is embedded rather than nested so the JSON stays flat: `tt show 12
// --json | jq .title` reads the same as `tt ls --json | jq .[0].title`, which
// is the point of having a machine-readable mode at all.
//
// No field is omitempty anywhere in this package. The --json keys are the agent
// contract, and a key that vanishes when its value is empty forces every
// consumer to distinguish "absent" from "empty" by hand. Absence is null via a
// pointer; everything else is "" or [].
type Detail struct {
	Issue                // embedded → flat JSON
	Blockers   []Link    `json:"blockers"`   // kind = dep
	Subtasks   []Link    `json:"subtasks"`   // kind = subtask; show lists these separately
	Dependents []Link    `json:"dependents"` // reverse edge, both kinds
	Comments   []Comment `json:"comments"`
	Rollup     Rollup    `json:"rollup"`
}

// AddParams is the shape `tt add`'s flags and the TUI's prompt both fill in.
// It is not a JSON type — it is an input, and inputs get to change.
type AddParams struct {
	Project   string // resolved slug; required. Services never read the cwd.
	Title     string
	Body      string   // -b, or the $EDITOR buffer from -e
	Priority  Priority // -! ; the zero value normalises to PriorityNormal
	Labels    []string // -l, repeatable
	Milestone string   // -M
	SubOf     *int     // --sub-of; nil = none
}

// ErrNotFound is returned for an id that does not exist.
var ErrNotFound = errs.ErrNotFound

// ParseStatus converts user input to a Status. Shared by the CLI's flag parsing
// and the TUI's filter so the two cannot accept different spellings.
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
//
// It does not check Labels/Milestone/SubOf against the not-yet-wired guards in
// AddIn: those are a statement about this phase, not about the input, and they
// live next to the code that will delete them.
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
