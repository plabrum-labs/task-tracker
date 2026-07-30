package issue

import (
	"encoding/json"
	"reflect"
	"strings"
	"testing"
	"time"
)

func TestValidate(t *testing.T) {
	ok := AddParams{Project: "tt", Title: "do the thing"}

	tests := []struct {
		name    string
		mutate  func(*AddParams)
		wantErr string
	}{
		{"valid", func(*AddParams) {}, ""},
		{"zero priority is allowed", func(p *AddParams) { p.Priority = "" }, ""},
		{"hi priority", func(p *AddParams) { p.Priority = PriorityHi }, ""},
		{"no project", func(p *AddParams) { p.Project = "" }, "project is required"},
		{"whitespace project", func(p *AddParams) { p.Project = "  " }, "project is required"},
		{"no title", func(p *AddParams) { p.Title = "" }, "title is required"},
		{"whitespace title", func(p *AddParams) { p.Title = " \t\n " }, "title is required"},
		{"bad priority", func(p *AddParams) { p.Priority = "urgent" }, "unknown priority"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			p := ok
			tt.mutate(&p)
			err := p.validate()
			switch {
			case tt.wantErr == "" && err != nil:
				t.Errorf("validate() = %v, want nil", err)
			case tt.wantErr != "" && err == nil:
				t.Errorf("validate() = nil, want %q", tt.wantErr)
			case tt.wantErr != "" && err != nil && !strings.Contains(err.Error(), tt.wantErr):
				t.Errorf("validate() = %q, want it to contain %q", err, tt.wantErr)
			}
		})
	}
}

func TestParseStatus(t *testing.T) {
	tests := []struct {
		in      string
		want    Status
		wantErr bool
	}{
		{"todo", StatusTodo, false},
		{"doing", StatusDoing, false},
		{"done", StatusDone, false},
		{"DONE", StatusDone, false},
		{"  doing  ", StatusDoing, false},
		{"", "", true},
		{"closed", "", true},
	}
	for _, tt := range tests {
		got, err := ParseStatus(tt.in)
		if (err != nil) != tt.wantErr {
			t.Errorf("ParseStatus(%q) err = %v, wantErr %v", tt.in, err, tt.wantErr)
			continue
		}
		if got != tt.want {
			t.Errorf("ParseStatus(%q) = %q, want %q", tt.in, got, tt.want)
		}
	}
}

func TestParsePriority(t *testing.T) {
	tests := []struct {
		in      string
		want    Priority
		wantErr bool
	}{
		{"normal", PriorityNormal, false},
		{"hi", PriorityHi, false},
		{"HI", PriorityHi, false},
		{" hi ", PriorityHi, false},
		{"", "", true},
		{"high", "", true},
	}
	for _, tt := range tests {
		got, err := ParsePriority(tt.in)
		if (err != nil) != tt.wantErr {
			t.Errorf("ParsePriority(%q) err = %v, wantErr %v", tt.in, err, tt.wantErr)
			continue
		}
		if got != tt.want {
			t.Errorf("ParsePriority(%q) = %q, want %q", tt.in, got, tt.want)
		}
	}
}

// TestJSONContract is an alarm, not a behaviour test. The --json keys are what
// an agent parses, so renaming one, adding omitempty, or un-embedding Issue is
// a breaking change that should require deliberately editing this literal.
func TestJSONContract(t *testing.T) {
	created := time.Date(2026, 1, 2, 3, 4, 5, 0, time.UTC)
	updated := time.Date(2026, 1, 2, 4, 4, 5, 0, time.UTC)
	closed := time.Date(2026, 1, 3, 3, 4, 5, 0, time.UTC)

	d := Detail{
		Issue: Issue{
			ID:        12,
			Title:     "wire the contract",
			Body:      "the long form",
			Status:    StatusDoing,
			Priority:  PriorityHi,
			Project:   "tt",
			Milestone: "v1",
			Labels:    []string{"backend", "cli"},
			CreatedAt: created,
			UpdatedAt: updated,
			ClosedAt:  &closed,
		},
		Blockers: []Link{
			{ID: 7, Title: "pick a driver", Status: StatusDone, Kind: KindDep},
		},
		Subtasks: []Link{
			{ID: 13, Title: "model.go", Status: StatusDone, Kind: KindSubtask},
			{ID: 14, Title: "service.go", Status: StatusTodo, Kind: KindSubtask},
		},
		Dependents: []Link{
			{ID: 20, Title: "ship it", Status: StatusTodo, Kind: KindDep},
		},
		Comments: []Comment{
			{ID: 1, At: created, Author: "phil", Body: "started"},
		},
		Rollup: Rollup{Done: 1, Total: 2},
	}

	const want = `{"id":12,"title":"wire the contract","body":"the long form",` +
		`"status":"doing","priority":"hi","project":"tt","milestone":"v1",` +
		`"labels":["backend","cli"],"created_at":"2026-01-02T03:04:05Z",` +
		`"updated_at":"2026-01-02T04:04:05Z","closed_at":"2026-01-03T03:04:05Z",` +
		`"blockers":[{"id":7,"title":"pick a driver","status":"done","kind":"dep"}],` +
		`"subtasks":[{"id":13,"title":"model.go","status":"done","kind":"subtask"},` +
		`{"id":14,"title":"service.go","status":"todo","kind":"subtask"}],` +
		`"dependents":[{"id":20,"title":"ship it","status":"todo","kind":"dep"}],` +
		`"comments":[{"id":1,"at":"2026-01-02T03:04:05Z","author":"phil","body":"started"}],` +
		`"rollup":{"done":1,"total":2}}`

	got, err := json.Marshal(d)
	if err != nil {
		t.Fatalf("marshalling detail: %v", err)
	}
	if string(got) != want {
		t.Errorf("Detail JSON =\n\t%s\nwant\n\t%s", got, want)
	}
}

// TestJSONAbsenceIsNull is the other half of the no-omitempty rule: an empty
// Detail still prints every key, so a consumer never has to tell "missing"
// from "empty". Only ClosedAt is null.
func TestJSONAbsenceIsNull(t *testing.T) {
	d := Detail{
		Issue:      Issue{Labels: []string{}},
		Blockers:   []Link{},
		Subtasks:   []Link{},
		Dependents: []Link{},
		Comments:   []Comment{},
	}
	got, err := json.Marshal(d)
	if err != nil {
		t.Fatalf("marshalling detail: %v", err)
	}
	for _, key := range []string{
		`"milestone":""`, `"labels":[]`, `"closed_at":null`, `"blockers":[]`,
		`"subtasks":[]`, `"dependents":[]`, `"comments":[]`, `"rollup":{"done":0,"total":0}`,
	} {
		if !strings.Contains(string(got), key) {
			t.Errorf("marshalled detail is missing %s:\n\t%s", key, got)
		}
	}
}

// TestNoEntTypesExposed is Phase 1's "done when", spelled as a test: no
// exported type in this package may reach an ent type, at any depth. Written
// reflectively rather than as a grep so a type that arrives through an
// embedded struct or a slice element is caught too.
func TestNoEntTypesExposed(t *testing.T) {
	const entPkg = "github.com/Plabrum/tt/ent"

	roots := map[string]reflect.Type{
		"Issue":     reflect.TypeOf(Issue{}),
		"Link":      reflect.TypeOf(Link{}),
		"Comment":   reflect.TypeOf(Comment{}),
		"Rollup":    reflect.TypeOf(Rollup{}),
		"Detail":    reflect.TypeOf(Detail{}),
		"AddParams": reflect.TypeOf(AddParams{}),
	}

	seen := map[reflect.Type]bool{}
	var walk func(typ reflect.Type, path string)
	walk = func(typ reflect.Type, path string) {
		if seen[typ] {
			return
		}
		seen[typ] = true

		if pkg := typ.PkgPath(); pkg == entPkg || strings.HasPrefix(pkg, entPkg+"/") {
			t.Errorf("%s exposes ent type %s", path, typ)
			return
		}
		switch typ.Kind() {
		case reflect.Pointer, reflect.Slice, reflect.Array:
			walk(typ.Elem(), path+"[]")
		case reflect.Map:
			walk(typ.Key(), path+"{key}")
			walk(typ.Elem(), path+"{}")
		case reflect.Struct:
			for i := range typ.NumField() {
				f := typ.Field(i)
				walk(f.Type, path+"."+f.Name)
			}
		}
	}
	for name, typ := range roots {
		walk(typ, name)
	}
}
