package models

import "time"

// CommentKey names one entry in a comment's menu.
type CommentKey string

// The actions a comment can offer.
const (
	KeyCommentEdit   CommentKey = "edit"
	KeyCommentDelete CommentKey = "delete"
)

// Comment is one entry in an issue's log.
//
// There is no list or show method for comments. A list of comments is only ever
// the comments on one issue, and ShowIssue already returns them.
//
// An edited comment is one whose UpdatedAt has moved off its CreatedAt; there is
// no separate edited-at stamp.
type Comment struct {
	ID        int       `json:"id"`
	CreatedAt time.Time `json:"created_at"`
	UpdatedAt time.Time `json:"updated_at"`
	Author    string    `json:"author"`
	Body      string    `json:"body"`

	Actions []Action[CommentKey] `json:"-"`
}

// Edited reports whether the body has changed since it was written.
func (c Comment) Edited() bool {
	return c.UpdatedAt.After(c.CreatedAt)
}
