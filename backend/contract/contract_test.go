package contract_test

import (
	"encoding/json"
	"testing"
	"time"

	"github.com/Plabrum/tt/backend/contract"
)

// The --json keys are the agent-facing API, not an implementation detail. Each
// case marshals a literal and compares it against a string written out by hand,
// so changing a key fails here rather than surprising somebody downstream.
//
// There are no fixture files and no regeneration step on purpose: changing the
// contract means editing the expected string by hand, which is the point at
// which someone notices they are changing it.

var stamp = time.Date(2026, 1, 2, 3, 4, 5, 0, time.UTC)

func TestContractJSON(t *testing.T) {
	t.Parallel()

	due := time.Date(2026, 3, 1, 0, 0, 0, 0, time.UTC)
	closed := time.Date(2026, 2, 1, 0, 0, 0, 0, time.UTC)

	project := contract.Project{
		ID:          7,
		Slug:        "tt",
		Title:       "Task tracker",
		Description: "the tracker",
		Status:      contract.ProjectActive,
		CreatedAt:   stamp,
		UpdatedAt:   stamp,
		Actions: []contract.Action[contract.ProjectKey]{
			{Key: contract.KeyProjectArchive, Label: "archive"},
		},
	}

	tests := []struct {
		name  string
		value any
		want  string
	}{
		{
			name:  "label",
			value: contract.Label{Name: "bug", Description: "a defect"},
			want:  `{"name":"bug","description":"a defect"}`,
		},
		{
			name:  "milestone",
			value: contract.Milestone{ID: 3, Name: "v1", Description: "first cut", Due: &due},
			want:  `{"id":3,"name":"v1","description":"first cut","due":"2026-03-01T00:00:00Z"}`,
		},
		{
			name:  "milestone without a due date",
			value: contract.Milestone{ID: 3, Name: "v1"},
			want:  `{"id":3,"name":"v1","description":"","due":null}`,
		},
		{
			name: "comment",
			value: contract.Comment{
				ID:        11,
				CreatedAt: stamp,
				UpdatedAt: stamp,
				Author:    "me",
				Body:      "a note",
				Actions: []contract.Action[contract.CommentKey]{
					{Key: contract.KeyCommentEdit, Label: "edit"},
				},
			},
			want: `{"id":11,"created_at":"2026-01-02T03:04:05Z",` +
				`"updated_at":"2026-01-02T03:04:05Z","author":"me","body":"a note"}`,
		},
		{
			name:  "project",
			value: project,
			want: `{"id":7,"slug":"tt","title":"Task tracker","description":"the tracker",` +
				`"status":"active","created_at":"2026-01-02T03:04:05Z",` +
				`"updated_at":"2026-01-02T03:04:05Z"}`,
		},
		{
			name: "issue",
			value: contract.Issue{
				ID:        12,
				Title:     "ship it",
				Body:      "the body",
				Status:    contract.IssueDoing,
				Priority:  contract.PriorityHi,
				CreatedAt: stamp,
				UpdatedAt: stamp,
				ClosedAt:  &closed,
				Project:   project,
				Milestone: &contract.Milestone{ID: 3, Name: "v1"},
				Labels:    []contract.Label{{Name: "bug"}},
				Parent:    nil,
				Subtasks:  []contract.Issue{},
				Blockers:  []contract.Issue{},
				Blocks:    []contract.Issue{},
				Comments:  []contract.Comment{},
				Blocked:   true,
				Actions: []contract.Action[contract.IssueKey]{
					{Key: contract.KeyIssueClose, Label: "close"},
				},
			},
			want: `{"id":12,"title":"ship it","body":"the body","status":"doing","priority":"hi",` +
				`"created_at":"2026-01-02T03:04:05Z","updated_at":"2026-01-02T03:04:05Z",` +
				`"closed_at":"2026-02-01T00:00:00Z",` +
				`"project":{"id":7,"slug":"tt","title":"Task tracker","description":"the tracker",` +
				`"status":"active","created_at":"2026-01-02T03:04:05Z",` +
				`"updated_at":"2026-01-02T03:04:05Z"},` +
				`"milestone":{"id":3,"name":"v1","description":"","due":null},` +
				`"labels":[{"name":"bug","description":""}],` +
				`"parent":null,"subtasks":[],"blockers":[],"blocks":[],"comments":[],` +
				`"blocked":true}`,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			got, err := json.Marshal(tt.value)
			if err != nil {
				t.Fatalf("marshalling: %v", err)
			}
			if string(got) != tt.want {
				t.Errorf("json mismatch\n got: %s\nwant: %s", got, tt.want)
			}
		})
	}
}

// Actions is json:"-" on every object, so an action change can never move a --json
// key. The cases above already assert the absence; this one says why, against
// the type parameter each object uses.
func TestActionsAreNotSerialised(t *testing.T) {
	t.Parallel()

	got, err := json.Marshal(contract.Comment{
		ID:      1,
		Actions: []contract.Action[contract.CommentKey]{{Key: contract.KeyCommentDelete, Label: "delete"}},
	})
	if err != nil {
		t.Fatalf("marshalling: %v", err)
	}
	want := `{"id":1,"created_at":"0001-01-01T00:00:00Z",` +
		`"updated_at":"0001-01-01T00:00:00Z","author":"","body":""}`
	if string(got) != want {
		t.Errorf("json mismatch\n got: %s\nwant: %s", got, want)
	}
}

// An Action is serialised when a caller reaches for one directly, so its own
// keys are contract too.
func TestActionJSON(t *testing.T) {
	t.Parallel()

	got, err := json.Marshal(contract.Action[contract.IssueKey]{
		Key:    contract.KeyIssueClose,
		Label:  "close",
		Reason: "open sub-issues",
	})
	if err != nil {
		t.Fatalf("marshalling: %v", err)
	}
	want := `{"key":"close","label":"close","reason":"open sub-issues"}`
	if string(got) != want {
		t.Errorf("json mismatch\n got: %s\nwant: %s", got, want)
	}
}

func TestRunnable(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name   string
		action contract.Action[contract.IssueKey]
		want   bool
	}{
		{"no reason runs", contract.Action[contract.IssueKey]{Key: contract.KeyIssueStart}, true},
		{"a reason refuses", contract.Action[contract.IssueKey]{Key: contract.KeyIssueStart, Reason: "blocked"}, false},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			if got := tt.action.Runnable(); got != tt.want {
				t.Errorf("Runnable() = %v, want %v", got, tt.want)
			}
		})
	}
}

func TestEdited(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name    string
		comment contract.Comment
		want    bool
	}{
		{"untouched", contract.Comment{CreatedAt: stamp, UpdatedAt: stamp}, false},
		{"edited", contract.Comment{CreatedAt: stamp, UpdatedAt: stamp.Add(time.Minute)}, true},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			if got := tt.comment.Edited(); got != tt.want {
				t.Errorf("Edited() = %v, want %v", got, tt.want)
			}
		})
	}
}
