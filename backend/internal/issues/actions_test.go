package issues_test

import (
	"slices"
	"testing"

	"github.com/Plabrum/tt/backend/contract"
	"github.com/Plabrum/tt/backend/internal/ent"
	entissue "github.com/Plabrum/tt/backend/internal/ent/issue"
	"github.com/Plabrum/tt/backend/internal/issues"
)

// Actions needs no database: a table of literal ent rows is the whole test.
// Every edge a rule reads is one withMenuEdges eager-loads, so setting it here
// is the same thing the loader does.

// find returns the menu entry for key, and whether it is there at all.
func find(menu []contract.Action[contract.IssueKey], key contract.IssueKey) (contract.Action[contract.IssueKey], bool) {
	i := slices.IndexFunc(menu, func(a contract.Action[contract.IssueKey]) bool { return a.Key == key })
	if i < 0 {
		return contract.Action[contract.IssueKey]{}, false
	}
	return menu[i], true
}

func TestActionsByStatus(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name    string
		status  entissue.Status
		present []contract.IssueKey
		absent  []contract.IssueKey
	}{
		{
			name:    "todo can start or close",
			status:  entissue.StatusTodo,
			present: []contract.IssueKey{contract.KeyIssueStart, contract.KeyIssueClose},
			absent:  []contract.IssueKey{contract.KeyIssueReopen},
		},
		{
			name:    "doing can close but not start again",
			status:  entissue.StatusDoing,
			present: []contract.IssueKey{contract.KeyIssueClose},
			absent:  []contract.IssueKey{contract.KeyIssueStart, contract.KeyIssueReopen},
		},
		{
			name:    "done can only reopen",
			status:  entissue.StatusDone,
			present: []contract.IssueKey{contract.KeyIssueReopen},
			absent:  []contract.IssueKey{contract.KeyIssueStart, contract.KeyIssueClose},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			menu := issues.Actions(&ent.Issue{ID: 1, Status: tt.status})
			for _, key := range tt.present {
				action, ok := find(menu, key)
				if !ok {
					t.Errorf("menu withholds %q from a %s issue", key, tt.status)
					continue
				}
				if !action.Runnable() {
					t.Errorf("%q refused on a %s issue: %s", key, tt.status, action.Reason)
				}
			}
			for _, key := range tt.absent {
				if _, ok := find(menu, key); ok {
					t.Errorf("menu offers %q on a %s issue", key, tt.status)
				}
			}
		})
	}
}

func TestActionsRefusals(t *testing.T) {
	t.Parallel()

	open := &ent.Issue{ID: 2, Status: entissue.StatusTodo}
	done := &ent.Issue{ID: 3, Status: entissue.StatusDone}

	tests := []struct {
		name       string
		issue      *ent.Issue
		key        contract.IssueKey
		wantReason string
	}{
		{
			name:       "an open blocker refuses start",
			issue:      &ent.Issue{ID: 1, Status: entissue.StatusTodo, Edges: ent.IssueEdges{BlockedBy: []*ent.Issue{open}}},
			key:        contract.KeyIssueStart,
			wantReason: "blocked by open issues",
		},
		{
			name:       "a finished blocker does not",
			issue:      &ent.Issue{ID: 1, Status: entissue.StatusTodo, Edges: ent.IssueEdges{BlockedBy: []*ent.Issue{done}}},
			key:        contract.KeyIssueStart,
			wantReason: "",
		},
		{
			name:       "an open sub-issue refuses close",
			issue:      &ent.Issue{ID: 1, Status: entissue.StatusDoing, Edges: ent.IssueEdges{Subtasks: []*ent.Issue{open}}},
			key:        contract.KeyIssueClose,
			wantReason: "open sub-issues",
		},
		{
			name:       "a finished sub-issue does not",
			issue:      &ent.Issue{ID: 1, Status: entissue.StatusDoing, Edges: ent.IssueEdges{Subtasks: []*ent.Issue{done}}},
			key:        contract.KeyIssueClose,
			wantReason: "",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			action, ok := find(issues.Actions(tt.issue), tt.key)
			if !ok {
				t.Fatalf("menu withholds %q entirely", tt.key)
			}
			if action.Reason != tt.wantReason {
				t.Errorf("Reason = %q, want %q", action.Reason, tt.wantReason)
			}
		})
	}
}

// Absent means the action does not apply here at all, which is the right answer
// when there is nothing for it to act on: an issue with no labels has nothing to
// unlabel, and one with no dependencies has nothing to drop.
func TestActionsWithheldWhenThereIsNothingToActOn(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name  string
		issue *ent.Issue
		key   contract.IssueKey
		want  bool
	}{
		{
			name:  "no labels withholds remove-label",
			issue: &ent.Issue{ID: 1, Status: entissue.StatusTodo},
			key:   contract.KeyIssueRemoveLabel,
			want:  false,
		},
		{
			name: "a label offers remove-label",
			issue: &ent.Issue{ID: 1, Status: entissue.StatusTodo, Edges: ent.IssueEdges{
				Labels: []*ent.Label{{Name: "bug"}},
			}},
			key:  contract.KeyIssueRemoveLabel,
			want: true,
		},
		{
			name:  "no dependencies withholds remove-dep",
			issue: &ent.Issue{ID: 1, Status: entissue.StatusTodo},
			key:   contract.KeyIssueRemoveDep,
			want:  false,
		},
		{
			name: "a dependency offers remove-dep",
			issue: &ent.Issue{ID: 1, Status: entissue.StatusTodo, Edges: ent.IssueEdges{
				BlockedBy: []*ent.Issue{{ID: 2, Status: entissue.StatusTodo}},
			}},
			key:  contract.KeyIssueRemoveDep,
			want: true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			if _, ok := find(issues.Actions(tt.issue), tt.key); ok != tt.want {
				t.Errorf("menu offers %q = %v, want %v", tt.key, ok, tt.want)
			}
		})
	}
}

// The keys that apply to any issue whatever state it is in.
func TestActionsAlwaysOffered(t *testing.T) {
	t.Parallel()

	always := []contract.IssueKey{
		contract.KeyIssueEdit,
		contract.KeyIssueDelete,
		contract.KeyIssueSetPriority,
		contract.KeyIssueSetMilestone,
		contract.KeyIssueAddLabel,
		contract.KeyIssueAddSubIssue,
		contract.KeyIssueAddDep,
		contract.KeyIssueComment,
	}
	statuses := []entissue.Status{entissue.StatusTodo, entissue.StatusDoing, entissue.StatusDone}

	for _, status := range statuses {
		menu := issues.Actions(&ent.Issue{ID: 1, Status: status})
		for _, key := range always {
			action, ok := find(menu, key)
			if !ok {
				t.Errorf("menu withholds %q from a %s issue", key, status)
				continue
			}
			if !action.Runnable() {
				t.Errorf("%q refused on a %s issue: %s", key, status, action.Reason)
			}
		}
	}
}

// Every key the contract declares is reachable from some state. A key nothing
// can ever offer is a key a frontend switches on and never hits.
func TestEveryKeyIsReachable(t *testing.T) {
	t.Parallel()

	all := []contract.IssueKey{
		contract.KeyIssueStart, contract.KeyIssueClose, contract.KeyIssueReopen,
		contract.KeyIssueEdit, contract.KeyIssueDelete, contract.KeyIssueSetPriority,
		contract.KeyIssueSetMilestone, contract.KeyIssueAddLabel, contract.KeyIssueRemoveLabel,
		contract.KeyIssueAddSubIssue, contract.KeyIssueAddDep, contract.KeyIssueRemoveDep,
		contract.KeyIssueComment,
	}

	// One row per status, plus a row carrying the relations the conditional
	// keys need, is enough to cover every key between them.
	rows := []*ent.Issue{
		{ID: 1, Status: entissue.StatusTodo},
		{ID: 2, Status: entissue.StatusDoing},
		{ID: 3, Status: entissue.StatusDone},
		{ID: 4, Status: entissue.StatusTodo, Edges: ent.IssueEdges{
			Labels:    []*ent.Label{{Name: "bug"}},
			BlockedBy: []*ent.Issue{{ID: 5, Status: entissue.StatusDone}},
		}},
	}

	seen := map[contract.IssueKey]bool{}
	for _, row := range rows {
		for _, a := range issues.Actions(row) {
			seen[a.Key] = true
		}
	}
	for _, key := range all {
		if !seen[key] {
			t.Errorf("no state offers %q", key)
		}
	}
}
