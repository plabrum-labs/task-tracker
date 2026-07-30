package comments

import (
	"fmt"

	"github.com/Plabrum/tt/backend/contract"
	"github.com/Plabrum/tt/backend/internal/actions"
	"github.com/Plabrum/tt/backend/internal/ent"
)

// The actions a comment offers.
//
// Both are unconditional, and there is no machine: a comment has no status and
// no relations, so there is nothing about one that can refuse an edit or a
// delete. The Spec is still where a rule would go.
var (
	// Edit replaces a comment's body.
	Edit = actions.Define(actions.Spec[contract.CommentKey, *ent.Comment]{
		Key:   contract.KeyCommentEdit,
		Label: "edit",
	}, edit)

	// Delete removes a comment.
	Delete = actions.Define(actions.Spec[contract.CommentKey, *ent.Comment]{
		Key:   contract.KeyCommentDelete,
		Label: "delete",
	}, remove)
)

// menu is every action above, in the order a frontend shows them.
//
// Assigned in init rather than at its declaration: an action's write converts
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
