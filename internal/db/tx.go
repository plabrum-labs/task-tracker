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
// The deferred recover is not defensive tidiness. Open pins the pool to a
// single connection, so a transaction that is neither committed nor rolled back
// holds the only connection there is: the next query blocks forever rather than
// failing. A panic escaping fn would therefore turn a crash — which a caller can
// see and fix — into a hang, which looks like the tool is broken. Rolling back
// and re-panicking preserves the original stack while freeing the connection.
//
// Transactions do not nest. Only the outermost service method calls WithTx;
// everything it composes takes an *ent.Client and inherits whichever one it is
// handed, which is what lets a composed Add and a standalone Add run the same
// code.
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
