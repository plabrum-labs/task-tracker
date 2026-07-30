package projects

import (
	"github.com/Plabrum/tt/backend/internal/ent"
	entproject "github.com/Plabrum/tt/backend/internal/ent/project"
	"github.com/Plabrum/tt/backend/models"
)

// Actions is the menu for one project.
//
// Pure: a loaded row in, a menu out. No context, no client, no error, no
// queries. A rule that needs a query is not a menu rule and belongs in
// service.go.
func Actions(e *ent.Project) []models.Action[models.ProjectKey] {
	out := make([]models.Action[models.ProjectKey], 0, 2)
	add := func(key models.ProjectKey, label, reason string) {
		out = append(out, models.Action[models.ProjectKey]{Key: key, Label: label, Reason: reason})
	}

	add(models.KeyProjectEdit, "edit", "")

	// Archiving and restoring are the two directions of one transition, so
	// exactly one of them applies at a time.
	switch e.Status {
	case entproject.StatusActive:
		add(models.KeyProjectArchive, "archive", "")
	case entproject.StatusArchived:
		add(models.KeyProjectRestore, "restore", "")
	}
	return out
}
