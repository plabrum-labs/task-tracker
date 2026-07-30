package models_test

import (
	"errors"
	"testing"

	"github.com/Plabrum/tt/backend/errs"
	"github.com/Plabrum/tt/backend/models"
)

// The parsers are shared by the CLI's flags and the TUI's filters, so the two
// cannot accept different spellings of the same value.

func TestParseIssueStatus(t *testing.T) {
	t.Parallel()

	tests := []struct {
		in      string
		want    models.IssueStatus
		wantErr bool
	}{
		{in: "todo", want: models.IssueTodo},
		{in: "DOING", want: models.IssueDoing},
		{in: "  done  ", want: models.IssueDone},
		{in: "closed", wantErr: true},
		{in: "", wantErr: true},
	}
	for _, tt := range tests {
		t.Run(tt.in, func(t *testing.T) {
			t.Parallel()
			got, err := models.ParseIssueStatus(tt.in)
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
		want    models.Priority
		wantErr bool
	}{
		{in: "normal", want: models.PriorityNormal},
		{in: "HI", want: models.PriorityHi},
		{in: "urgent", wantErr: true},
	}
	for _, tt := range tests {
		t.Run(tt.in, func(t *testing.T) {
			t.Parallel()
			got, err := models.ParsePriority(tt.in)
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
		want    models.ProjectStatus
		wantErr bool
	}{
		{in: "active", want: models.ProjectActive},
		{in: "ARCHIVED", want: models.ProjectArchived},
		{in: "paused", wantErr: true},
	}
	for _, tt := range tests {
		t.Run(tt.in, func(t *testing.T) {
			t.Parallel()
			got, err := models.ParseProjectStatus(tt.in)
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
		params  models.IssueAddParams
		wantErr bool
	}{
		{
			name:   "the minimum",
			params: models.IssueAddParams{Project: "tt", Title: "work"},
		},
		{
			name:   "an empty priority normalises later",
			params: models.IssueAddParams{Project: "tt", Title: "work", Priority: ""},
		},
		{
			name:    "no project",
			params:  models.IssueAddParams{Title: "work"},
			wantErr: true,
		},
		{
			name:    "a blank title",
			params:  models.IssueAddParams{Project: "tt", Title: "   "},
			wantErr: true,
		},
		{
			name:    "an unknown priority",
			params:  models.IssueAddParams{Project: "tt", Title: "work", Priority: "urgent"},
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
