package comments

import (
	"context"
	"fmt"
	"strings"

	"github.com/Plabrum/tt/backend/contract"
	"github.com/Plabrum/tt/backend/errs"
	"github.com/Plabrum/tt/backend/internal/actions"
	"github.com/Plabrum/tt/backend/internal/db"
	"github.com/Plabrum/tt/backend/internal/ent"
)

// DefaultAuthor is who a comment belongs to when nothing says otherwise. There
// is one user, so there is one name.
const DefaultAuthor = "me"

// Action is an action on a comment, taking a payload of type P.
type Action[P any] = actions.Bound[contract.CommentKey, *ent.Comment, P, contract.Comment]

// Run performs an action on the comment with this id.
func Run[P any](ctx context.Context, cl *ent.Client, id int, a *Action[P], p P) (contract.Comment, error) {
	var out contract.Comment
	err := db.WithTx(ctx, cl, func(tx *ent.Client) error {
		var err error
		out, err = RunIn(ctx, tx, id, a, p)
		return err
	})
	return out, err
}

// RunIn is Run inside a caller's transaction.
func RunIn[P any](ctx context.Context, tx *ent.Client, id int, a *Action[P], p P) (contract.Comment, error) {
	e, err := row(ctx, tx, id)
	if err != nil {
		return contract.Comment{}, err
	}
	return actions.Invoke(ctx, menu, tx, e, a, p)
}

// Dispatch performs the action key names on the comment with this id.
func Dispatch(
	ctx context.Context,
	cl *ent.Client,
	id int,
	key contract.CommentKey,
	payload any,
) (contract.Comment, error) {
	var out contract.Comment
	err := db.WithTx(ctx, cl, func(tx *ent.Client) error {
		e, err := row(ctx, tx, id)
		if err != nil {
			return err
		}
		out, err = menu.Dispatch(ctx, tx, e, key, payload)
		return err
	})
	return out, err
}

// Add appends a comment to an issue.
//
// It has no subject of its own — the subject is the issue — so it is not on the
// comment menu and is not an Action.
func Add(ctx context.Context, cl *ent.Client, issueID int, body string) (contract.Comment, error) {
	var out contract.Comment
	err := db.WithTx(ctx, cl, func(tx *ent.Client) error {
		var err error
		out, err = AddIn(ctx, tx, issueID, body)
		return err
	})
	return out, err
}

// AddIn is Add inside a caller's transaction.
func AddIn(ctx context.Context, tx *ent.Client, issueID int, body string) (contract.Comment, error) {
	body = strings.TrimSpace(body)
	if body == "" {
		return contract.Comment{}, errs.Invalidf("comment body is required")
	}
	created, err := tx.Comment.Create().
		SetIssueID(issueID).
		SetAuthor(DefaultAuthor).
		SetBody(body).
		Save(ctx)
	if err != nil {
		return contract.Comment{}, fmt.Errorf("commenting on issue %d: %w", issueID, err)
	}
	return Convert(created), nil
}

// row reads the ent row a write is about to change.
func row(ctx context.Context, tx *ent.Client, id int) (*ent.Comment, error) {
	e, err := tx.Comment.Get(ctx, id)
	if err != nil {
		if ent.IsNotFound(err) {
			return nil, errs.NotFoundf("comment %d", id)
		}
		return nil, fmt.Errorf("loading comment %d: %w", id, err)
	}
	return e, nil
}
