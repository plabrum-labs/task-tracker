package comments

import (
	"context"
	"fmt"
	"strings"
	"time"

	"github.com/Plabrum/tt/backend/contract"
	"github.com/Plabrum/tt/backend/errs"
	"github.com/Plabrum/tt/backend/internal/db"
	"github.com/Plabrum/tt/backend/internal/ent"
)

// DefaultAuthor is who a comment belongs to when nothing says otherwise. There
// is one user, so there is one name.
const DefaultAuthor = "me"

// Add appends a comment to an issue.
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
	// Both stamps are set from one clock read rather than left to the schema.
	// TimeMixin defaults each of them to time.Now separately, so a comment
	// written that way lands with updated_at a few microseconds after
	// created_at — and Edited(), which is exactly that comparison, would report
	// every fresh comment as edited.
	now := time.Now()
	created, err := tx.Comment.Create().
		SetIssueID(issueID).
		SetAuthor(DefaultAuthor).
		SetBody(body).
		SetCreatedAt(now).
		SetUpdatedAt(now).
		Save(ctx)
	if err != nil {
		return contract.Comment{}, fmt.Errorf("commenting on issue %d: %w", issueID, err)
	}
	return Convert(created), nil
}

// Edit replaces a comment's body.
func Edit(ctx context.Context, cl *ent.Client, id int, body string) (contract.Comment, error) {
	var out contract.Comment
	err := db.WithTx(ctx, cl, func(tx *ent.Client) error {
		var err error
		out, err = EditIn(ctx, tx, id, body)
		return err
	})
	return out, err
}

// EditIn is Edit inside a caller's transaction.
//
// updated_at moves off created_at here, which is the whole record that an edit
// happened — there is no separate edited-at stamp.
func EditIn(ctx context.Context, tx *ent.Client, id int, body string) (contract.Comment, error) {
	body = strings.TrimSpace(body)
	if body == "" {
		return contract.Comment{}, errs.Invalidf("comment body is required")
	}
	e, err := row(ctx, tx, id)
	if err != nil {
		return contract.Comment{}, err
	}
	if !offers(e, contract.KeyCommentEdit) {
		return contract.Comment{}, errs.Conflictf("comment %d cannot be edited", id)
	}
	updated, err := e.Update().SetBody(body).Save(ctx)
	if err != nil {
		return contract.Comment{}, fmt.Errorf("editing comment %d: %w", id, err)
	}
	return Convert(updated), nil
}

// Delete removes a comment.
func Delete(ctx context.Context, cl *ent.Client, id int) error {
	return db.WithTx(ctx, cl, func(tx *ent.Client) error {
		return DeleteIn(ctx, tx, id)
	})
}

// DeleteIn is Delete inside a caller's transaction.
func DeleteIn(ctx context.Context, tx *ent.Client, id int) error {
	e, err := row(ctx, tx, id)
	if err != nil {
		return err
	}
	if !offers(e, contract.KeyCommentDelete) {
		return errs.Conflictf("comment %d cannot be deleted", id)
	}
	if err := tx.Comment.DeleteOne(e).Exec(ctx); err != nil {
		return fmt.Errorf("deleting comment %d: %w", id, err)
	}
	return nil
}

// offers reports whether the menu for this row carries key without a refusal.
// Going through Actions is what keeps the write and the menu from drifting.
func offers(e *ent.Comment, key contract.CommentKey) bool {
	for _, a := range Actions(e) {
		if a.Key == key {
			return a.Runnable()
		}
	}
	return false
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
