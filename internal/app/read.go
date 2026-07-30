package app

import (
	"context"

	"github.com/Plabrum/tt/internal/issue"
	"github.com/Plabrum/tt/internal/query"
)

// Reads pass the client straight through rather than opening a transaction.
// The pool is pinned to one connection, so a BEGIN here would serialise the
// whole process behind itself and buy nothing in-process; the only exposure is
// a cross-process writer leaving a derived flag one refresh stale.

// Show returns one issue with its refs and comments.
func (a *API) Show(ctx context.Context, id int) (issue.Detail, error) {
	return issue.Get(ctx, a.client, id)
}

// List returns the rows matching p, in pick order.
func (a *API) List(ctx context.Context, p query.ListParams) ([]query.Row, error) {
	return query.List(ctx, a.client, p)
}
