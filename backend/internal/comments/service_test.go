package comments_test

import (
	"errors"
	"testing"

	"github.com/Plabrum/tt/backend/contract"
	"github.com/Plabrum/tt/backend/errs"
	"github.com/Plabrum/tt/backend/internal/actions"
	"github.com/Plabrum/tt/backend/internal/comments"
	"github.com/Plabrum/tt/backend/internal/dbtest"
	"github.com/Plabrum/tt/backend/internal/ent"
	"github.com/Plabrum/tt/backend/internal/issues"
)

// issueID seeds one issue for a comment to hang off.
func issueID(t *testing.T, cl *ent.Client) int {
	t.Helper()
	created, err := issues.Create(t.Context(), cl, contract.IssueAddParams{Project: "tt", Title: "work"})
	if err != nil {
		t.Fatalf("creating issue: %v", err)
	}
	return created.ID
}

// A comment has no status and no relations, so both entries are unconditional.
func TestActions(t *testing.T) {
	t.Parallel()

	offered := comments.Actions(&ent.Comment{ID: 1})
	want := []contract.CommentKey{contract.KeyCommentEdit, contract.KeyCommentDelete}
	if len(offered) != len(want) {
		t.Fatalf("actions = %+v, want %d of them", offered, len(want))
	}
	for i, key := range want {
		if offered[i].Key != key {
			t.Errorf("actions[%d].Key = %q, want %q", i, offered[i].Key, key)
		}
		if !offered[i].Runnable() {
			t.Errorf("%q refused: %s", key, offered[i].Reason)
		}
	}
}

func TestAddAndList(t *testing.T) {
	t.Parallel()
	cl := dbtest.Client(t)
	id := issueID(t, cl)

	if _, err := comments.Add(t.Context(), cl, id, "first"); err != nil {
		t.Fatalf("Add: %v", err)
	}
	if _, err := comments.Add(t.Context(), cl, id, "second"); err != nil {
		t.Fatalf("Add: %v", err)
	}

	found, err := comments.ListForIssue(t.Context(), cl, id)
	if err != nil {
		t.Fatalf("ListForIssue: %v", err)
	}
	if len(found) != 2 {
		t.Fatalf("comments = %+v, want two", found)
	}
	// Oldest first.
	if found[0].Body != "first" || found[1].Body != "second" {
		t.Errorf("order = %q, %q, want first, second", found[0].Body, found[1].Body)
	}
	if found[0].Author != comments.DefaultAuthor {
		t.Errorf("Author = %q, want %q", found[0].Author, comments.DefaultAuthor)
	}
}

func TestAddRejectsABlankBody(t *testing.T) {
	t.Parallel()
	cl := dbtest.Client(t)
	id := issueID(t, cl)

	if _, err := comments.Add(t.Context(), cl, id, "   "); !errors.Is(err, errs.ErrInvalid) {
		t.Errorf("Add = %v, want ErrInvalid", err)
	}
}

// An edit is visible as updated_at moving off created_at. There is no separate
// edited-at stamp, so this is the whole record that one happened.
func TestEditMovesUpdatedAt(t *testing.T) {
	t.Parallel()
	cl := dbtest.Client(t)
	id := issueID(t, cl)

	created, err := comments.Add(t.Context(), cl, id, "before")
	if err != nil {
		t.Fatalf("Add: %v", err)
	}
	if created.Edited() {
		t.Error("a fresh comment reports itself edited")
	}

	edited, err := comments.Run(t.Context(), cl, created.ID, comments.Edit, "  after  ")
	if err != nil {
		t.Fatalf("Edit: %v", err)
	}
	if edited.Body != "after" {
		t.Errorf("Body = %q, want %q", edited.Body, "after")
	}
	if !edited.Edited() {
		t.Error("an edited comment does not report itself edited")
	}
	if !edited.CreatedAt.Equal(created.CreatedAt) {
		t.Errorf("CreatedAt moved from %v to %v", created.CreatedAt, edited.CreatedAt)
	}

	if _, err := comments.Run(t.Context(), cl, created.ID, comments.Edit, ""); !errors.Is(err, errs.ErrInvalid) {
		t.Errorf("Edit to a blank body = %v, want ErrInvalid", err)
	}
}

func TestDelete(t *testing.T) {
	t.Parallel()
	cl := dbtest.Client(t)
	id := issueID(t, cl)

	created, err := comments.Add(t.Context(), cl, id, "a note")
	if err != nil {
		t.Fatalf("Add: %v", err)
	}
	if _, err := comments.Run(t.Context(), cl, created.ID, comments.Delete, actions.None{}); err != nil {
		t.Fatalf("Delete: %v", err)
	}

	found, err := comments.ListForIssue(t.Context(), cl, id)
	if err != nil {
		t.Fatalf("ListForIssue: %v", err)
	}
	if len(found) != 0 {
		t.Errorf("comments = %+v, want none", found)
	}
}

// Deleting an issue takes its comments with it.
func TestCommentsCascadeWithTheirIssue(t *testing.T) {
	t.Parallel()
	cl := dbtest.Client(t)
	id := issueID(t, cl)

	if _, err := comments.Add(t.Context(), cl, id, "a note"); err != nil {
		t.Fatalf("Add: %v", err)
	}
	if _, err := issues.Run(t.Context(), cl, id, issues.Delete, actions.None{}); err != nil {
		t.Fatalf("deleting the issue: %v", err)
	}

	n, err := cl.Comment.Query().Count(t.Context())
	if err != nil {
		t.Fatalf("counting comments: %v", err)
	}
	if n != 0 {
		t.Errorf("comments = %d, want 0", n)
	}
}

func TestMissingIDsAreNotFound(t *testing.T) {
	t.Parallel()
	cl := dbtest.Client(t)

	if _, err := comments.Load(t.Context(), cl, 999); !errors.Is(err, errs.ErrNotFound) {
		t.Errorf("Load = %v, want ErrNotFound", err)
	}
	if _, err := comments.Run(t.Context(), cl, 999, comments.Edit, "x"); !errors.Is(err, errs.ErrNotFound) {
		t.Errorf("Edit = %v, want ErrNotFound", err)
	}
	if _, err := comments.Run(t.Context(), cl, 999, comments.Delete, actions.None{}); !errors.Is(err, errs.ErrNotFound) {
		t.Errorf("Delete = %v, want ErrNotFound", err)
	}
}
