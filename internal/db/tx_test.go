package db

import (
	"context"
	"errors"
	"testing"

	"github.com/Plabrum/tt/ent"
)

func TestWithTxCommits(t *testing.T) {
	ctx := context.Background()
	client := newTestClient(t)

	err := WithTx(ctx, client, func(tx *ent.Client) error {
		tx.Project.Create().SetSlug("committed").SaveX(ctx)
		return nil
	})
	if err != nil {
		t.Fatalf("WithTx: %v", err)
	}

	if n := client.Project.Query().CountX(ctx); n != 1 {
		t.Errorf("projects = %d, want 1", n)
	}
}

// TestWithTxRollsBackOnError pins both halves of the contract: the write is
// undone, and the caller still gets its own error rather than a rollback report.
func TestWithTxRollsBackOnError(t *testing.T) {
	ctx := context.Background()
	client := newTestClient(t)

	boom := errors.New("boom")
	err := WithTx(ctx, client, func(tx *ent.Client) error {
		tx.Project.Create().SetSlug("doomed").SaveX(ctx)
		return boom
	})
	if !errors.Is(err, boom) {
		t.Errorf("err = %v, want %v", err, boom)
	}

	if n := client.Project.Query().CountX(ctx); n != 0 {
		t.Errorf("projects = %d, want 0", n)
	}
}

// TestWithTxRollsBackOnPanic is the regression test for the hang described on
// WithTx: with SetMaxOpenConns(1), a transaction leaked by a panic makes every
// later query block forever. The final query is the assertion that matters —
// if it returns at all, the connection was released.
func TestWithTxRollsBackOnPanic(t *testing.T) {
	ctx := context.Background()
	client := newTestClient(t)

	func() {
		defer func() {
			p := recover()
			if p == nil {
				t.Error("WithTx swallowed the panic, want it re-raised")
			}
			if got, ok := p.(string); !ok || got != "kaboom" {
				t.Errorf("recovered %v, want %q", p, "kaboom")
			}
		}()
		_ = WithTx(ctx, client, func(tx *ent.Client) error {
			tx.Project.Create().SetSlug("doomed").SaveX(ctx)
			panic("kaboom")
		})
	}()

	if n := client.Project.Query().CountX(ctx); n != 0 {
		t.Errorf("projects = %d, want 0", n)
	}
	// And the client is still usable, not deadlocked behind a leaked tx.
	client.Project.Create().SetSlug("after").SaveX(ctx)
}
