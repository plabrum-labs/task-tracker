package comments

import (
	"github.com/Plabrum/tt/backend/contract"
	"github.com/Plabrum/tt/backend/internal/ent"
)

// Actions is the menu for one comment.
//
// Pure: a loaded row in, a menu out. No context, no client, no error, no
// queries.
//
// Both entries are unconditional. A comment has no status and no relations, so
// there is nothing about one that can refuse an edit or a delete. The row is
// still the parameter, so that a rule arriving later has somewhere to go.
func Actions(_ *ent.Comment) []contract.Action[contract.CommentKey] {
	return []contract.Action[contract.CommentKey]{
		{Key: contract.KeyCommentEdit, Label: "edit"},
		{Key: contract.KeyCommentDelete, Label: "delete"},
	}
}
