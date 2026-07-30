package contract

import "time"

// Milestone is a project-scoped grouping, optionally with a deadline.
//
// Milestones and labels are not objects: they carry no Key and no Actions, so
// nothing offers actions on them.
type Milestone struct {
	ID          int        `json:"id"`
	Name        string     `json:"name"`
	Description string     `json:"description"`
	Due         *time.Time `json:"due"` // nil when none
}

// Label is global rather than project-scoped: "bug" means "bug" everywhere.
type Label struct {
	Name        string `json:"name"`
	Description string `json:"description"`
}
