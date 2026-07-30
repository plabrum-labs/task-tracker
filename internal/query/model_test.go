package query

import (
	"encoding/json"
	"strings"
	"testing"
	"time"

	"github.com/Plabrum/tt/internal/issue"
)

// TestRowJSONContract is an alarm, not a behaviour test. `tt ls --json` is what
// an agent parses, so renaming a key or adding omitempty is a breaking change
// that should require deliberately editing this literal.
func TestRowJSONContract(t *testing.T) {
	created := time.Date(2026, 1, 2, 3, 4, 5, 0, time.UTC)
	updated := time.Date(2026, 1, 2, 4, 4, 5, 0, time.UTC)

	r := Row{
		ID:        12,
		Title:     "wire the contract",
		Status:    issue.StatusDoing,
		Priority:  issue.PriorityHi,
		Project:   "tt",
		Milestone: "v1",
		Labels:    []string{"backend", "cli"},
		Blocked:   true,
		Subtasks:  issue.Rollup{Done: 1, Total: 2},
		CreatedAt: created,
		UpdatedAt: updated,
	}

	const want = `{"id":12,"title":"wire the contract","status":"doing",` +
		`"priority":"hi","project":"tt","milestone":"v1",` +
		`"labels":["backend","cli"],"blocked":true,` +
		`"subtasks":{"done":1,"total":2},"created_at":"2026-01-02T03:04:05Z",` +
		`"updated_at":"2026-01-02T04:04:05Z"}`

	got, err := json.Marshal(r)
	if err != nil {
		t.Fatalf("marshalling row: %v", err)
	}
	if string(got) != want {
		t.Errorf("Row JSON =\n\t%s\nwant\n\t%s", got, want)
	}
}

// TestRowAbsenceIsNull is the other half of the no-omitempty rule: an empty Row
// still prints every key, so a consumer never has to tell "missing" from
// "empty". Body is absent from Row by design and must stay absent.
func TestRowAbsenceIsNull(t *testing.T) {
	got, err := json.Marshal(Row{Labels: []string{}})
	if err != nil {
		t.Fatalf("marshalling row: %v", err)
	}
	for _, key := range []string{
		`"milestone":""`, `"labels":[]`, `"blocked":false`,
		`"subtasks":{"done":0,"total":0}`,
	} {
		if !strings.Contains(string(got), key) {
			t.Errorf("marshalled row is missing %s:\n\t%s", key, got)
		}
	}
	if strings.Contains(string(got), `"body"`) {
		t.Errorf("Row must not carry a body:\n\t%s", got)
	}
}
