package projects

import (
	"context"
	"fmt"

	"github.com/Plabrum/tt/backend/internal/ent"
	entproject "github.com/Plabrum/tt/backend/internal/ent/project"
	"github.com/Plabrum/tt/backend/internal/statemachine"
)

// ProjectStateMachine is the project lifecycle: active and archived, each reachable from
// the other. Archiving is what takes a project's work out of the way, and
// restoring is what brings it back; nothing else moves a project.
var ProjectStateMachine = statemachine.New(
	func(e *ent.Project) entproject.Status { return e.Status },
	func(ctx context.Context, tx *ent.Client, e *ent.Project, to entproject.Status) error {
		if err := tx.Project.UpdateOne(e).SetStatus(to).Exec(ctx); err != nil {
			return fmt.Errorf("setting project %q to %s: %w", e.Slug, to, err)
		}
		return nil
	},
	map[entproject.Status]statemachine.State[entproject.Status, *ent.Project]{
		entproject.StatusActive:   {To: []entproject.Status{entproject.StatusArchived}},
		entproject.StatusArchived: {To: []entproject.Status{entproject.StatusActive}},
	},
)
