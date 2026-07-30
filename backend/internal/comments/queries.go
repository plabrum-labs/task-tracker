// Package comments owns the issue log.
//
// There is no list or show entry point beyond one issue's comments: a list of
// comments is only ever the comments on one issue, and the issues domain
// already returns them.
package comments

import (
	"context"
	"fmt"

	"github.com/Plabrum/tt/backend/errs"
	"github.com/Plabrum/tt/backend/internal/ent"
	entcomment "github.com/Plabrum/tt/backend/internal/ent/comment"
	entissue "github.com/Plabrum/tt/backend/internal/ent/issue"
	"github.com/Plabrum/tt/backend/models"
)

// ListForIssue returns one issue's comments, oldest first.
func ListForIssue(ctx context.Context, cl *ent.Client, issueID int) ([]models.Comment, error) {
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
func Load(ctx context.Context, cl *ent.Client, id int) (models.Comment, error) {
	e, err := cl.Comment.Get(ctx, id)
	if err != nil {
		if ent.IsNotFound(err) {
			return models.Comment{}, errs.NotFoundf("comment %d", id)
		}
		return models.Comment{}, fmt.Errorf("loading comment %d: %w", id, err)
	}
	return Convert(e), nil
}

// ConvertAll flattens ent rows into contract types. The result is never nil, so
// --json prints [] rather than null.
func ConvertAll(rows []*ent.Comment) []models.Comment {
	out := make([]models.Comment, 0, len(rows))
	for _, e := range rows {
		out = append(out, Convert(e))
	}
	return out
}

// Convert flattens an ent row into the contract type, menu included.
func Convert(e *ent.Comment) models.Comment {
	return models.Comment{
		ID:        e.ID,
		CreatedAt: e.CreatedAt,
		UpdatedAt: e.UpdatedAt,
		Author:    e.Author,
		Body:      e.Body,
		Actions:   Actions(e),
	}
}
