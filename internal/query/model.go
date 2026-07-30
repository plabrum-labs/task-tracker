// Package query owns the cross-feature reads: the list every frontend renders
// and, later, the aggregates behind `next` and milestone progress.
//
// It exists because a list row needs labels, a milestone name, a blocked flag
// and a subtask count — four features' data on one line. Putting that read in
// any one feature would make the other three import it.
//
// query importing issue is fine and deliberate: the acyclic rules ban taxonomy
// from importing work, and ref from importing issue. issue must never import
// query.
package query

import (
	"time"

	"github.com/Plabrum/tt/internal/issue"
)

// Row is one line of `ls`, one row of the TUI list, one card in a board cell.
//
// Body is deliberately absent. No list renders it, and loading three hundred
// bodies to print three hundred titles is the kind of waste that only shows up
// once the database is big enough to be worth keeping.
type Row struct {
	ID        int            `json:"id"`
	Title     string         `json:"title"`
	Status    issue.Status   `json:"status"`   // the board's GroupBy key; ls -a shows it
	Priority  issue.Priority `json:"priority"` // the ! flag
	Project   string         `json:"project"`  // for -A
	Milestone string         `json:"milestone"`
	Labels    []string       `json:"labels"`
	Blocked   bool           `json:"blocked"`  // the ⊘ flag; derived on every read
	Subtasks  issue.Rollup   `json:"subtasks"` // the 3/5 badge
	CreatedAt time.Time      `json:"created_at"`
	UpdatedAt time.Time      `json:"updated_at"`
}

// ListParams is every filter `ls` and the TUI can apply.
//
// All of them work in Phase 1. A flag that parses and then silently does
// nothing is worse than a flag that does not exist, so the filters land with
// the type. What later phases add here are functions — Eligible, Next,
// MilestoneProgress, LabelCounts — not fields.
type ListParams struct {
	Project     string         // "" means every project (-A)
	Statuses    []issue.Status // empty means todo+doing; -a passes all three
	Labels      []string       // -l, ANDed
	Milestone   string         // -M
	Search      string         // positional arg: case-insensitive substring of the title
	BlockedOnly bool           // --blocked
	Limit       int            // 0 = no limit
}
