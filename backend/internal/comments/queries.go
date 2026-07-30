// Package comments owns the issue log.
//
// There is no list or show entry point beyond one issue's comments: a list of
// comments is only ever the comments on one issue, and the issues domain
// already returns them.
package comments

import (
	"context"
	"fmt"

	"github.com/Plabrum/tt/backend/contract"
	"github.com/Plabrum/tt/backend/errs"
	"github.com/Plabrum/tt/backend/internal/ent"
	entcomment "github.com/Plabrum/tt/backend/internal/ent/comment"
	entissue "github.com/Plabrum/tt/backend/internal/ent/issue"
)

// ListForIssue returns one issue's comments, oldest first.
func ListForIssue(ctx context.Context, cl *ent.Client, issueID int) ([]contract.Comment, error) {
	found, err := cl.Comment.Query().
		Where(entcomment.HasIssueWith(entissue.IDEQ(issueID))).
		Order(entcomment.ByCreatedAt(), entcomment.ByID()).
		All(ctx)
	if err != nil {
		return nil, fmt.Errorf("listing comments of issue %d: %w", issueID, err)
	}
	return ConvertAll(found), nil
}

// Load returns one comment. A missing id wraps errs.ErrNotFound.
func Load(ctx context.Context, cl *ent.Client, id int) (contract.Comment, error) {
	e, err := cl.Comment.Get(ctx, id)
	if err != nil {
		if ent.IsNotFound(err) {
			return contract.Comment{}, errs.NotFoundf("comment %d", id)
		}
		return contract.Comment{}, fmt.Errorf("loading comment %d: %w", id, err)
	}
	return Convert(e), nil
}

// ConvertAll flattens ent rows into contract types. The result is never nil, so
// --json prints [] rather than null.
func ConvertAll(rows []*ent.Comment) []contract.Comment {
	out := make([]contract.Comment, 0, len(rows))
	for _, e := range rows {
		out = append(out, Convert(e))
	}
	return out
}

// Convert flattens an ent row into the contract type, actions included.
func Convert(e *ent.Comment) contract.Comment {
	return contract.Comment{
		ID:        e.ID,
		CreatedAt: e.CreatedAt,
		UpdatedAt: e.UpdatedAt,
		Author:    e.Author,
		Body:      e.Body,
		Actions:   Actions(e),
	}
}
