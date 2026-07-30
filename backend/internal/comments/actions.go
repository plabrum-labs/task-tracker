package comments

import (
	"context"
	"fmt"
	"strings"

	"github.com/Plabrum/tt/backend/contract"
	"github.com/Plabrum/tt/backend/errs"
	"github.com/Plabrum/tt/backend/internal/actions"
	"github.com/Plabrum/tt/backend/internal/ent"
)

// action is an action on a comment taking a payload of type P.
type action[P any] = actions.Action[contract.CommentKey, *ent.Comment, P, contract.Comment]

// commentActions is every action a comment has. It is the only place a rule is
// checked: a converted row asks it what to carry, and every write goes
// through it.
var commentActions = actions.NewGroup[contract.CommentKey, *ent.Comment, contract.Comment](
	func(e *ent.Comment) string { return fmt.Sprintf("comment %d", e.ID) },
)

// Neither action declares a rule, and there is no machine: a comment has no
// status and no relations, so there is nothing about one that can withhold an
// edit or a delete.

// Edit replaces a comment's body.
var Edit = actions.Register(commentActions, action[string]{
	Key:      contract.KeyCommentEdit,
	Label:    "edit",
	Priority: 10,
	Execute:  edit,
})

// edit replaces a comment's body.
//
// updated_at moves off created_at here, which is the whole record that an edit
// happened — there is no separate edited-at stamp.
func edit(ctx context.Context, _ *ent.Client, e *ent.Comment, body string) (contract.Comment, error) {
	body = strings.TrimSpace(body)
	if body == "" {
		return contract.Comment{}, errs.Invalidf("comment body is required")
	}
	updated, err := e.Update().SetBody(body).Save(ctx)
	if err != nil {
		return contract.Comment{}, fmt.Errorf("editing comment %d: %w", e.ID, err)
	}
	return Convert(updated), nil
}

// Delete removes a comment.
var Delete = actions.Register(commentActions, action[actions.None]{
	Key:      contract.KeyCommentDelete,
	Label:    "delete",
	Priority: 20,
	Execute:  deleteComment,
})

// deleteComment drops a comment. The zero comment comes back because there is
// none left, and every action in a group returns the same type.
func deleteComment(ctx context.Context, tx *ent.Client, e *ent.Comment, _ actions.None) (contract.Comment, error) {
	if err := tx.Comment.DeleteOne(e).Exec(ctx); err != nil {
		return contract.Comment{}, fmt.Errorf("deleting comment %d: %w", e.ID, err)
	}
	return contract.Comment{}, nil
}
