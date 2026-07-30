// Package app wires the services together. It is the one place that knows
// every constructor, and the only thing a frontend has to construct.
package app

import (
	"github.com/Plabrum/tt/ent"
	"github.com/Plabrum/tt/internal/issue"
	"github.com/Plabrum/tt/internal/project"
	"github.com/Plabrum/tt/internal/query"
)

// Services is what cli/ and ui/ are handed.
//
// The ent client is deliberately not a field. A frontend that can reach the
// client can write a query, and the moment one does, the view types stop being
// the contract — a change to the schema starts breaking the CLI directly
// instead of breaking the one service that was supposed to absorb it.
//
// Later phases append Ref, Comment, Label and Milestone. Each is one field here
// and one line in New, which is the smallest merge conflict four parallel lanes
// could have agreed to have: resolve it by keeping both sides.
type Services struct {
	Project *project.Service
	Issue   *issue.Service
	Query   *query.Service
}

// New constructs every service over one client.
func New(client *ent.Client) *Services {
	projects := project.NewService(client)
	return &Services{
		Project: projects,
		Issue:   issue.NewService(client, projects),
		Query:   query.NewService(client),
	}
}
