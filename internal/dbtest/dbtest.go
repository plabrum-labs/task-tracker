// Package dbtest hands tests a migrated, throwaway database.
//
// It exists because six packages need the same twelve lines, and six copies is
// six places to change when Open grows a parameter. It goes through the real
// db.Open, so a broken migration fails in whichever package's tests run first
// rather than on someone's actual database.
//
// It cannot be used from package db itself — dbtest imports db, so that would
// be an import cycle. internal/db keeps its own newTestClient helper.
package dbtest

import (
	"testing"

	"github.com/Plabrum/tt/ent"
	"github.com/Plabrum/tt/internal/db"
)

// Client returns a fresh in-memory client, closed when the test finishes.
//
// Each call gets its own database: SetMaxOpenConns(1) inside Open means one
// connection and therefore one private `:memory:`, with no shared-cache
// lifetime games between parallel tests.
func Client(t *testing.T) *ent.Client {
	t.Helper()
	client, err := db.Open(t.Context(), db.DSN(":memory:"))
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
