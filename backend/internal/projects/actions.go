package projects

import (
	"github.com/Plabrum/tt/backend/contract"
	"github.com/Plabrum/tt/backend/internal/ent"
	entproject "github.com/Plabrum/tt/backend/internal/ent/project"
)

// Actions is the menu for one project.
//
// Pure: a loaded row in, a menu out. No context, no client, no error, no
// queries. A rule that needs a query is not a menu rule and belongs in
// service.go.
func Actions(e *ent.Project) []contract.Action[contract.ProjectKey] {
	out := make([]contract.Action[contract.ProjectKey], 0, 2)
	add := func(key contract.ProjectKey, label, reason string) {
		out = append(out, contract.Action[contract.ProjectKey]{Key: key, Label: label, Reason: reason})
	}

	add(contract.KeyProjectEdit, "edit", "")

	// Archiving and restoring are the two directions of one transition, so
	// exactly one of them applies at a time.
	switch e.Status {
	case entproject.StatusActive:
		add(contract.KeyProjectArchive, "archive", "")
	case entproject.StatusArchived:
		add(contract.KeyProjectRestore, "restore", "")
	}
	return out
}
