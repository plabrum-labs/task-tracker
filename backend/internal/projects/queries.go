// Package projects owns the container an issue lands in: inferring which
// project the user means from where they are standing, creating it on first
// use, and archiving it when its work is over.
//
// Taxonomy never depends on work, so nothing here imports the issues domain.
package projects

import (
	"context"
	"fmt"

	"github.com/Plabrum/tt/backend/errs"
	"github.com/Plabrum/tt/backend/internal/ent"
	entproject "github.com/Plabrum/tt/backend/internal/ent/project"
	"github.com/Plabrum/tt/backend/models"
)

// Load returns one project by slug. A missing slug wraps errs.ErrNotFound.
func Load(ctx context.Context, cl *ent.Client, slug string) (models.Project, error) {
	e, err := cl.Project.Query().Where(entproject.SlugEQ(Slugify(slug))).Only(ctx)
	if err != nil {
		if ent.IsNotFound(err) {
			return models.Project{}, errs.NotFoundf("project %q", slug)
		}
		return models.Project{}, fmt.Errorf("loading project %q: %w", slug, err)
	}
	return Convert(e)
}

// LoadByID returns one project by id.
func LoadByID(ctx context.Context, cl *ent.Client, id int) (models.Project, error) {
	e, err := cl.Project.Get(ctx, id)
	if err != nil {
		if ent.IsNotFound(err) {
			return models.Project{}, errs.NotFoundf("project %d", id)
		}
		return models.Project{}, fmt.Errorf("loading project %d: %w", id, err)
	}
	return Convert(e)
}

// List returns the projects matching p, by slug.
func List(ctx context.Context, cl *ent.Client, p models.ProjectListParams) ([]models.Project, error) {
	// The default hides archived work; asking for it explicitly is what shows it.
	statuses := p.Statuses
	if len(statuses) == 0 {
		statuses = []models.ProjectStatus{models.ProjectActive}
	}
	want := make([]entproject.Status, 0, len(statuses))
	for _, st := range statuses {
		stored, err := statusToEnt(st)
		if err != nil {
			return nil, err
		}
		want = append(want, stored)
	}

	q := cl.Project.Query().
		Where(entproject.StatusIn(want...)).
		Order(entproject.BySlug())
	if p.Limit > 0 {
		q = q.Limit(p.Limit)
	}

	found, err := q.All(ctx)
	if err != nil {
		return nil, fmt.Errorf("listing projects: %w", err)
	}
	// Non-nil, so --json prints [] rather than null.
	out := make([]models.Project, 0, len(found))
	for _, e := range found {
		converted, err := Convert(e)
		if err != nil {
			return nil, err
		}
		out = append(out, converted)
	}
	return out, nil
}

// ActiveIDs returns the ids of every project that is not archived.
func ActiveIDs(ctx context.Context, cl *ent.Client) ([]int, error) {
	ids, err := cl.Project.Query().
		Where(entproject.StatusEQ(entproject.StatusActive)).
		IDs(ctx)
	if err != nil {
		return nil, fmt.Errorf("listing active projects: %w", err)
	}
	return ids, nil
}

// Convert flattens an ent row into the contract type, menu included.
func Convert(e *ent.Project) (models.Project, error) {
	status, err := statusFromEnt(e.Status)
	if err != nil {
		return models.Project{}, fmt.Errorf("project %d: %w", e.ID, err)
	}
	return models.Project{
		ID:          e.ID,
		Slug:        e.Slug,
		Title:       e.Title,
		Description: e.Description,
		Status:      status,
		CreatedAt:   e.CreatedAt,
		UpdatedAt:   e.UpdatedAt,
		Actions:     Actions(e),
	}, nil
}

// The conversions across the ent/domain boundary. Exhaustive switches rather
// than casts, so a value added to one side and not the other fails at the
// default arm instead of travelling as a constant nothing matches.

func statusFromEnt(s entproject.Status) (models.ProjectStatus, error) {
	switch s {
	case entproject.StatusActive:
		return models.ProjectActive, nil
	case entproject.StatusArchived:
		return models.ProjectArchived, nil
	default:
		return "", errs.Conflictf("stored project status %q is not one this binary knows", s)
	}
}

func statusToEnt(s models.ProjectStatus) (entproject.Status, error) {
	switch s {
	case models.ProjectActive:
		return entproject.StatusActive, nil
	case models.ProjectArchived:
		return entproject.StatusArchived, nil
	default:
		return "", errs.Invalidf("unknown project status %q", s)
	}
}
