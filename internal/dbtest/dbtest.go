// Package dbtest hands tests a migrated, throwaway database.
//
// It goes through the real db.OpenAndMigrate, so a broken migration fails in
// whichever package's tests run first rather than on someone's real database.
//
// It cannot be used from package db itself — dbtest imports db, so that would
// be an import cycle. internal/db keeps its own newTestClient helper.
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
	client, err := db.OpenAndMigrate(t.Context(), db.DSN(":memory:"))
	if err != nil {
		t.Fatalf("opening test client: %v", err)
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
