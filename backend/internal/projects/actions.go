package projects

import (
	"context"
	"fmt"

	"github.com/Plabrum/tt/backend/contract"
	"github.com/Plabrum/tt/backend/internal/actions"
	"github.com/Plabrum/tt/backend/internal/ent"
	entproject "github.com/Plabrum/tt/backend/internal/ent/project"
	"github.com/Plabrum/tt/backend/internal/statemachine"
)

// machine is the project lifecycle: active and archived, each reachable from
// the other. Archiving is what takes a project's work out of the way, and
// restoring is what brings it back; nothing else moves a project.
var machine = statemachine.New(
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

// The actions a project offers. Archive and Restore are the two directions of
// one transition, so exactly one of them is on the menu at a time — which one
// is the machine's to say, not this file's.
var (
	// Edit changes a project's title or description.
	Edit = actions.Define(actions.Spec[contract.ProjectKey, *ent.Project]{
		Key:   contract.KeyProjectEdit,
		Label: "edit",
	}, edit)

	// Archive takes a project's work out of the way.
	Archive = actions.Define(actions.Spec[contract.ProjectKey, *ent.Project]{
		Key:     contract.KeyProjectArchive,
		Label:   "archive",
		Applies: canMoveTo(entproject.StatusArchived),
	}, transitionTo(entproject.StatusArchived))

	// Restore brings an archived project back.
	Restore = actions.Define(actions.Spec[contract.ProjectKey, *ent.Project]{
		Key:     contract.KeyProjectRestore,
		Label:   "restore",
		Applies: canMoveTo(entproject.StatusActive),
	}, transitionTo(entproject.StatusActive))
)

// menu is every action above, in the order a frontend shows them.
//
// Assigned in init rather than at its declaration: an action's write reloads
// through LoadByID, which converts, which asks for the menu. Nothing reads menu
// while the package is initialising, but the initializer analysis sees the loop
// and rejects it, so the assignment has to sit where that analysis does not run.
var menu *actions.Group[contract.ProjectKey, *ent.Project, contract.Project]

func init() {
	menu = actions.NewGroup(
		func(e *ent.Project) string { return fmt.Sprintf("project %q", e.Slug) },
		Edit.Entry(),
		Archive.Entry(),
		Restore.Entry(),
	)
}

// Actions is the menu for one project: a loaded row in, a menu out.
func Actions(e *ent.Project) []contract.Action[contract.ProjectKey] {
	return menu.Menu(e)
}

// canMoveTo is a transition's Applies: the machine's topology, asked rather
// than restated.
func canMoveTo(to entproject.Status) func(*ent.Project) bool {
	return func(e *ent.Project) bool { return machine.Can(e, to) }
}

// transitionTo is the write behind a transition: move, then reload.
func transitionTo(
	to entproject.Status,
) func(context.Context, *ent.Client, *ent.Project, actions.None) (contract.Project, error) {
	return func(ctx context.Context, tx *ent.Client, e *ent.Project, _ actions.None) (contract.Project, error) {
		if err := machine.Transition(ctx, tx, e, to); err != nil {
			return contract.Project{}, err
		}
		return LoadByID(ctx, tx, e.ID)
	}
}
