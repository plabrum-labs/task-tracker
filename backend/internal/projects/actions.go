package projects

import (
	"context"
	"fmt"
	"strings"

	"github.com/Plabrum/tt/backend/contract"
	"github.com/Plabrum/tt/backend/errs"
	"github.com/Plabrum/tt/backend/internal/actions"
	"github.com/Plabrum/tt/backend/internal/ent"
	entproject "github.com/Plabrum/tt/backend/internal/ent/project"
)

// edit ----------------------------------------------------------------------

type editProject struct{ actions.Default[*ent.Project] }

func (editProject) Key() contract.ProjectKey { return contract.KeyProjectEdit }
func (editProject) Label() string            { return "edit" }

func (editProject) Execute(
	ctx context.Context, tx *ent.Client, e *ent.Project, p contract.ProjectEditParams,
) (contract.Project, error) {
	if p.Title == nil && p.Description == nil {
		return contract.Project{}, errs.Invalidf("nothing to change")
	}

	u := e.Update()
	if p.Title != nil {
		u = u.SetTitle(strings.TrimSpace(*p.Title))
	}
	if p.Description != nil {
		u = u.SetDescription(*p.Description)
	}
	if _, err := u.Save(ctx); err != nil {
		return contract.Project{}, fmt.Errorf("editing project %q: %w", e.Slug, err)
	}
	return LoadByID(ctx, tx, e.ID)
}

// archive -------------------------------------------------------------------

// Archiving and restoring are the two directions of one transition, so exactly
// one of them is on the menu at a time — which one is ProjectStateMachine's to
// say, not this file's.

type archiveProject struct{ actions.Default[*ent.Project] }

func (archiveProject) Key() contract.ProjectKey { return contract.KeyProjectArchive }
func (archiveProject) Label() string            { return "archive" }

func (archiveProject) IsAvailable(e *ent.Project) bool {
	return ProjectStateMachine.Can(e, entproject.StatusArchived)
}

func (archiveProject) Execute(
	ctx context.Context, tx *ent.Client, e *ent.Project, _ actions.None,
) (contract.Project, error) {
	return transition(ctx, tx, e, entproject.StatusArchived)
}

// restore -------------------------------------------------------------------

type restoreProject struct{ actions.Default[*ent.Project] }

func (restoreProject) Key() contract.ProjectKey { return contract.KeyProjectRestore }
func (restoreProject) Label() string            { return "restore" }

func (restoreProject) IsAvailable(e *ent.Project) bool {
	return ProjectStateMachine.Can(e, entproject.StatusActive)
}

func (restoreProject) Execute(
	ctx context.Context, tx *ent.Client, e *ent.Project, _ actions.None,
) (contract.Project, error) {
	return transition(ctx, tx, e, entproject.StatusActive)
}

// the menu ------------------------------------------------------------------

// The actions a project offers, as handles that keep their payload type.
var (
	Edit    = actions.Bind(editProject{})
	Archive = actions.Bind(archiveProject{})
	Restore = actions.Bind(restoreProject{})
)

// menu is every action above, in the order a frontend shows them.
//
// Assigned in init rather than at its declaration: an action's Execute reloads
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
