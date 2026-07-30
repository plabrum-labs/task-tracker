package models

import (
	"strings"
	"time"

	"github.com/Plabrum/tt/backend/errs"
)

// ProjectStatus is whether a project is still in the way.
type ProjectStatus string

// The statuses a project moves between.
const (
	ProjectActive   ProjectStatus = "active"
	ProjectArchived ProjectStatus = "archived"
)

// ProjectKey names one entry in a project's menu.
type ProjectKey string

// The actions a project can offer.
const (
	KeyProjectEdit    ProjectKey = "edit"
	KeyProjectArchive ProjectKey = "archive"
	KeyProjectRestore ProjectKey = "restore"
)

// Project is the container an issue lands in.
//
// It does not carry its issues: that is ListIssues with Project set, which a
// detail pane wants anyway for filtering and limits.
type Project struct {
	ID          int           `json:"id"`
	Slug        string        `json:"slug"`
	Title       string        `json:"title"`
	Description string        `json:"description"`
	Status      ProjectStatus `json:"status"`
	CreatedAt   time.Time     `json:"created_at"`
	UpdatedAt   time.Time     `json:"updated_at"`

	Actions []Action[ProjectKey] `json:"-"`
}

// ProjectListParams is every filter a project list can apply.
type ProjectListParams struct {
	Statuses []ProjectStatus // empty means active only
	Limit    int             // 0 = no limit
}

// ProjectEditParams is a partial update. A nil field is left unchanged.
type ProjectEditParams struct {
	Title       *string
	Description *string
}

// ParseProjectStatus converts user input to a ProjectStatus.
func ParseProjectStatus(s string) (ProjectStatus, error) {
	switch ProjectStatus(strings.ToLower(strings.TrimSpace(s))) {
	case ProjectActive:
		return ProjectActive, nil
	case ProjectArchived:
		return ProjectArchived, nil
	default:
		return "", errs.Invalidf("unknown project status %q: want active or archived", s)
	}
}
