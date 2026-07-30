// Package labels owns the global label vocabulary. A label is not an object:
// it has no menu, so this package has no actions.go.
//
// Taxonomy never depends on work, so nothing here imports the issues domain.
package labels

import (
	"context"
	"fmt"

	"github.com/Plabrum/tt/backend/internal/ent"
	entlabel "github.com/Plabrum/tt/backend/internal/ent/label"
	"github.com/Plabrum/tt/backend/models"
)

// List returns every label, by name.
func List(ctx context.Context, cl *ent.Client) ([]models.Label, error) {
	found, err := cl.Label.Query().Order(entlabel.ByName()).All(ctx)
	if err != nil {
		return nil, fmt.Errorf("listing labels: %w", err)
	}
	out := make([]models.Label, 0, len(found))
	for _, e := range found {
		out = append(out, Convert(e))
	}
	return out, nil
}

// Convert flattens an ent row into the contract type.
func Convert(e *ent.Label) models.Label {
	return models.Label{Name: e.Name, Description: e.Description}
}
