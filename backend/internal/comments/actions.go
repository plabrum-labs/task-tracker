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

// Both actions take Default's rules and there is no machine: a comment has no
// status and no relations, so there is nothing about one that can withhold an
// edit or a delete. Overriding either method is where a rule would go.

// edit ----------------------------------------------------------------------

type editComment struct{ actions.Default[*ent.Comment] }

func (editComment) Key() contract.CommentKey { return contract.KeyCommentEdit }
func (editComment) Label() string            { return "edit" }

// updated_at moves off created_at here, which is the whole record that an edit
// happened — there is no separate edited-at stamp.
func (editComment) Execute(
	ctx context.Context, _ *ent.Client, e *ent.Comment, body string,
) (contract.Comment, error) {
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

// delete --------------------------------------------------------------------

type deleteComment struct{ actions.Default[*ent.Comment] }

func (deleteComment) Key() contract.CommentKey { return contract.KeyCommentDelete }
func (deleteComment) Label() string            { return "delete" }

// The zero comment comes back because there is none left, and every action in a
// group returns the same type.
func (deleteComment) Execute(
	ctx context.Context, tx *ent.Client, e *ent.Comment, _ actions.None,
) (contract.Comment, error) {
	if err := tx.Comment.DeleteOne(e).Exec(ctx); err != nil {
		return contract.Comment{}, fmt.Errorf("deleting comment %d: %w", e.ID, err)
	}
	return contract.Comment{}, nil
}

// the menu ------------------------------------------------------------------

// The actions a comment offers, as handles that keep their payload type.
var (
	Edit   = actions.Bind(editComment{})
	Delete = actions.Bind(deleteComment{})
)

// menu is every action above, in the order a frontend shows them.
//
// Assigned in init rather than at its declaration: an action's Execute converts
// its row, and converting asks for the menu. Nothing reads menu while the
// package is initialising, but the initializer analysis sees the loop and
// rejects it, so the assignment has to sit where that analysis does not run.
var menu *actions.Group[contract.CommentKey, *ent.Comment, contract.Comment]

func init() {
	menu = actions.NewGroup(
		func(e *ent.Comment) string { return fmt.Sprintf("comment %d", e.ID) },
		Edit.Entry(),
		Delete.Entry(),
	)
}

// Actions is the menu for one comment: a loaded row in, a menu out.
func Actions(e *ent.Comment) []contract.Action[contract.CommentKey] {
	return menu.Menu(e)
}
