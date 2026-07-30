package app_test

import (
	"reflect"
	"testing"

	"github.com/Plabrum/tt/internal/app"
	"github.com/Plabrum/tt/internal/dbtest"
	"github.com/Plabrum/tt/internal/issue"
	"github.com/Plabrum/tt/internal/query"
)

// TestNewWiresEverything is the test that fails when a lane adds a field to
// Services and forgets the line in New. Written reflectively rather than as a
// list of nil checks so it covers the field that has not been written yet.
func TestNewWiresEverything(t *testing.T) {
	t.Parallel()
	services := app.New(dbtest.Client(t))

	v := reflect.ValueOf(services).Elem()
	typ := v.Type()
	for i := range typ.NumField() {
		f := typ.Field(i)
		if !f.IsExported() {
			continue
		}
		if v.Field(i).IsZero() {
			t.Errorf("Services.%s is unset — add it to app.New", f.Name)
		}
	}
}

// TestServicesHidesTheClient: no frontend may reach past the services to the
// database, so the client must not be reachable through an exported field.
func TestServicesHidesTheClient(t *testing.T) {
	t.Parallel()

	typ := reflect.TypeOf(app.Services{})
	for i := range typ.NumField() {
		f := typ.Field(i)
		if !f.IsExported() {
			continue
		}
		if f.Type.String() == "*ent.Client" {
			t.Errorf("Services.%s exposes the ent client", f.Name)
		}
	}
}

// TestServicesUsable is the smoke test for the wiring: an issue added through
// one service is visible through another, which only holds if New handed them
// the same client.
func TestServicesUsable(t *testing.T) {
	t.Parallel()
	ctx := t.Context()
	services := app.New(dbtest.Client(t))

	added, err := services.Issue.Add(ctx, issue.AddParams{Project: "tt", Title: "wired"})
	if err != nil {
		t.Fatalf("Add: %v", err)
	}
	rows, err := services.Query.List(ctx, query.ListParams{Project: "tt"})
	if err != nil {
		t.Fatalf("List: %v", err)
	}
	if len(rows) != 1 || rows[0].ID != added.ID {
		t.Errorf("rows = %+v, want just the issue %d that was added", rows, added.ID)
	}
}
