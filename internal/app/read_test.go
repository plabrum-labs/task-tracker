package app_test

import (
	"errors"
	"testing"

	"github.com/Plabrum/tt/internal/dbtest"
	"github.com/Plabrum/tt/internal/errs"
	"github.com/Plabrum/tt/internal/issue"
	"github.com/Plabrum/tt/internal/query"
)

// TestAddIsVisibleToReads: an issue captured through one method must be
// readable through the others, which only holds if all three run against the
// same client.
func TestAddIsVisibleToReads(t *testing.T) {
	t.Parallel()
	ctx := t.Context()
	_, api := dbtest.API(t)

	added, err := api.Add(ctx, issue.AddParams{Project: "tt", Title: "wired"})
	if err != nil {
		t.Fatalf("Add: %v", err)
	}

	rows, err := api.List(ctx, query.ListParams{Project: "tt"})
	if err != nil {
		t.Fatalf("List: %v", err)
	}
	if len(rows) != 1 || rows[0].ID != added.ID {
		t.Errorf("rows = %+v, want just the issue %d that was added", rows, added.ID)
	}

	shown, err := api.Show(ctx, added.ID)
	if err != nil {
		t.Fatalf("Show: %v", err)
	}
	if shown.ID != added.ID || shown.Title != "wired" {
		t.Errorf("shown = %d/%q, want %d/%q", shown.ID, shown.Title, added.ID, "wired")
	}
}

func TestShowNotFound(t *testing.T) {
	t.Parallel()
	_, api := dbtest.API(t)

	_, err := api.Show(t.Context(), 404)
	if !errors.Is(err, errs.ErrNotFound) {
		t.Errorf("err = %v, want it to wrap errs.ErrNotFound", err)
	}
}
