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

// action is an action on a project taking a payload of type P.
type action[P any] = actions.Action[contract.ProjectKey, *ent.Project, P, contract.Project]

// projectActions is every action a project has. It is the only place a rule is
// checked: Actions reads it and every write goes through it.
var projectActions = actions.NewGroup[contract.ProjectKey, *ent.Project, contract.Project](
	func(e *ent.Project) string { return fmt.Sprintf("project %q", e.Slug) },
)

// Actions is what one project offers: a loaded row in, its actions out.
func Actions(e *ent.Project) []contract.Action[contract.ProjectKey] {
	return projectActions.Available(e)
}

// Edit changes a project's title or description.
var Edit = actions.Register(projectActions, action[contract.ProjectEditParams]{
	Key:      contract.KeyProjectEdit,
	Label:    "edit",
	Priority: 10,
	Execute:  edit,
})

// Archive takes a project's work out of the way.
var Archive = actions.Register(projectActions, action[actions.None]{
	Key:         contract.KeyProjectArchive,
	Label:       "archive",
	Priority:    20,
	IsAvailable: canArchive,
	Execute:     toArchived,
})

// Restore brings an archived project back.
var Restore = actions.Register(projectActions, action[actions.None]{
	Key:         contract.KeyProjectRestore,
	Label:       "restore",
	Priority:    30,
	IsAvailable: canRestore,
	Execute:     toActive,
})

// Archiving and restoring are the two directions of one transition, so exactly
// one of them is offered at a time — which one is ProjectStateMachine's to say,
// not this file's.

func canArchive(e *ent.Project) bool {
	return ProjectStateMachine.Can(e, entproject.StatusArchived)
}

func canRestore(e *ent.Project) bool {
	return ProjectStateMachine.Can(e, entproject.StatusActive)
}

func toArchived(ctx context.Context, tx *ent.Client, e *ent.Project, _ actions.None) (contract.Project, error) {
	return transition(ctx, tx, e, entproject.StatusArchived)
}

func toActive(ctx context.Context, tx *ent.Client, e *ent.Project, _ actions.None) (contract.Project, error) {
	return transition(ctx, tx, e, entproject.StatusActive)
}

// transition moves a project and reloads it.
func transition(
	ctx context.Context, tx *ent.Client, e *ent.Project, to entproject.Status,
) (contract.Project, error) {
	if err := ProjectStateMachine.Transition(ctx, tx, e, to); err != nil {
		return contract.Project{}, err
	}
	return LoadByID(ctx, tx, e.ID)
}

func edit(
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
