package comments

import (
	"testing"

	"github.com/Plabrum/tt/backend/contract"
	"github.com/Plabrum/tt/backend/internal/ent"
)

// A comment has no status and no relations, so both entries are unconditional.
func TestActions(t *testing.T) {
	t.Parallel()

	offered := commentActions.Available(&ent.Comment{ID: 1})
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
