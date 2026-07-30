// Package dbtest hands tests a throwaway database with the schema in place.
//
// The schema comes from Ent's auto-migrate, not from replaying migrations/:
// tests are about the code, and coupling every package's tests to the
// migration history would make an unrelated schema change fail everywhere at
// once. `just db-check` is what keeps migrations/ honest instead.
//
// dbtest imports db, so db's own tests reach it from an external test package
// rather than an in-package one.
package dbtest

import (
	"testing"

	"github.com/Plabrum/tt/ent"
	"github.com/Plabrum/tt/internal/app"
	"github.com/Plabrum/tt/internal/db"
)

// Client returns a fresh in-memory client, closed when the test finishes.
//
// Each call gets its own database: SetMaxOpenConns(1) means one connection and
// therefore one private `:memory:`, with no shared-cache lifetime games
// between parallel tests.
func Client(t *testing.T) *ent.Client {
	t.Helper()
	client, err := db.Open(t.Context(), db.DSN(":memory:"))
	if err != nil {
		t.Fatalf("opening test client: %v", err)
	}
	if err := client.Schema.Create(t.Context()); err != nil {
		t.Fatalf("creating test schema: %v", err)
	}
	t.Cleanup(func() {
		if err := client.Close(); err != nil {
			t.Errorf("closing test client: %v", err)
		}
	})
	return client
}

// API returns an API over a fresh client, plus the client itself so a test can
// seed rows and check what landed without going back through the API.
func API(t *testing.T) (*ent.Client, *app.API) {
	t.Helper()
	client := Client(t)
	return client, app.New(client)
}
