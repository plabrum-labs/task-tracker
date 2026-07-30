package db

import (
	"context"
	"errors"
	"fmt"

	"github.com/Plabrum/tt/ent"
)

// WithTx runs fn inside a transaction, committing if it returns nil and rolling
// back otherwise.
//
// The deferred recover is load-bearing. The pool is pinned to a single
// connection, so a transaction that is neither committed nor rolled back holds
// the only connection there is and the next query blocks forever. Rolling back
// and re-panicking turns that hang back into the crash it was.
//
// Transactions do not nest: only an app method calls WithTx.
func WithTx(ctx context.Context, client *ent.Client, fn func(tx *ent.Client) error) error {
	tx, err := client.Tx(ctx)
	if err != nil {
		return fmt.Errorf("beginning transaction: %w", err)
	}
	defer func() {
		if p := recover(); p != nil {
			_ = tx.Rollback()
			panic(p)
		}
	}()

	if err := fn(tx.Client()); err != nil {
		// Joined rather than wrapped: a rollback failure is a second, separate
		// problem, and losing fn's error to it would hide the actual cause.
		if rerr := tx.Rollback(); rerr != nil {
			return errors.Join(err, fmt.Errorf("rolling back: %w", rerr))
		}
		return err
	}
	if err := tx.Commit(); err != nil {
		return fmt.Errorf("committing transaction: %w", err)
	}
	return nil
}
