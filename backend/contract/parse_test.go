package contract_test

import (
	"errors"
	"testing"

	"github.com/Plabrum/tt/backend/contract"
	"github.com/Plabrum/tt/backend/errs"
)

// The parsers are shared by the CLI's flags and the TUI's filters, so the two
// cannot accept different spellings of the same value.

func TestParseIssueStatus(t *testing.T) {
	t.Parallel()

	tests := []struct {
		in      string
		want    contract.IssueStatus
		wantErr bool
	}{
		{in: "todo", want: contract.IssueTodo},
		{in: "DOING", want: contract.IssueDoing},
		{in: "  done  ", want: contract.IssueDone},
		{in: "closed", wantErr: true},
		{in: "", wantErr: true},
	}
	for _, tt := range tests {
		t.Run(tt.in, func(t *testing.T) {
			t.Parallel()
			got, err := contract.ParseIssueStatus(tt.in)
			if tt.wantErr {
				if !errors.Is(err, errs.ErrInvalid) {
					t.Errorf("error = %v, want ErrInvalid", err)
				}
				return
			}
			if err != nil {
				t.Fatalf("ParseIssueStatus(%q): %v", tt.in, err)
			}
			if got != tt.want {
				t.Errorf("= %q, want %q", got, tt.want)
			}
		})
	}
}

func TestParsePriority(t *testing.T) {
	t.Parallel()

	tests := []struct {
		in      string
		want    contract.Priority
		wantErr bool
	}{
		{in: "normal", want: contract.PriorityNormal},
		{in: "HI", want: contract.PriorityHi},
		{in: "urgent", wantErr: true},
	}
	for _, tt := range tests {
		t.Run(tt.in, func(t *testing.T) {
			t.Parallel()
			got, err := contract.ParsePriority(tt.in)
			if tt.wantErr {
				if !errors.Is(err, errs.ErrInvalid) {
					t.Errorf("error = %v, want ErrInvalid", err)
				}
				return
			}
			if err != nil {
				t.Fatalf("ParsePriority(%q): %v", tt.in, err)
			}
			if got != tt.want {
				t.Errorf("= %q, want %q", got, tt.want)
			}
		})
	}
}

func TestParseProjectStatus(t *testing.T) {
	t.Parallel()

	tests := []struct {
		in      string
		want    contract.ProjectStatus
		wantErr bool
	}{
		{in: "active", want: contract.ProjectActive},
		{in: "ARCHIVED", want: contract.ProjectArchived},
		{in: "paused", wantErr: true},
	}
	for _, tt := range tests {
		t.Run(tt.in, func(t *testing.T) {
			t.Parallel()
			got, err := contract.ParseProjectStatus(tt.in)
			if tt.wantErr {
				if !errors.Is(err, errs.ErrInvalid) {
					t.Errorf("error = %v, want ErrInvalid", err)
				}
				return
			}
			if err != nil {
				t.Fatalf("ParseProjectStatus(%q): %v", tt.in, err)
			}
			if got != tt.want {
				t.Errorf("= %q, want %q", got, tt.want)
			}
		})
	}
}

func TestIssueAddParamsValidate(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name    string
		params  contract.IssueAddParams
		wantErr bool
	}{
		{
			name:   "the minimum",
			params: contract.IssueAddParams{Project: "tt", Title: "work"},
		},
		{
			name:   "an empty priority normalises later",
			params: contract.IssueAddParams{Project: "tt", Title: "work", Priority: ""},
		},
		{
			name:    "no project",
			params:  contract.IssueAddParams{Title: "work"},
			wantErr: true,
		},
		{
			name:    "a blank title",
			params:  contract.IssueAddParams{Project: "tt", Title: "   "},
			wantErr: true,
		},
		{
			name:    "an unknown priority",
			params:  contract.IssueAddParams{Project: "tt", Title: "work", Priority: "urgent"},
			wantErr: true,
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			err := tt.params.Validate()
			if tt.wantErr {
				if !errors.Is(err, errs.ErrInvalid) {
					t.Errorf("error = %v, want ErrInvalid", err)
				}
				return
			}
			if err != nil {
				t.Errorf("Validate: %v", err)
			}
		})
	}
}
