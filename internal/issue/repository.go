package issue

import (
	"context"
	"fmt"
	"slices"
	"strings"

	"github.com/Plabrum/tt/ent"
	entcomment "github.com/Plabrum/tt/ent/comment"
	entissue "github.com/Plabrum/tt/ent/issue"
	entlabel "github.com/Plabrum/tt/ent/label"
	entref "github.com/Plabrum/tt/ent/ref"
	"github.com/Plabrum/tt/internal/errs"
)

// repository is the boundary between ent rows and the view types in model.go.
// Nothing above it sees an ent type, and nothing below it sees a Detail.
type repository struct{}

// insert writes a new issue. Status and the timestamps come from schema
// defaults, so there is nothing here that a second write path could set
// differently.
func (repository) insert(ctx context.Context, cl *ent.Client, projectID int, p AddParams) (*ent.Issue, error) {
	priority := p.Priority
	if priority == "" {
		priority = PriorityNormal
	}
	created, err := cl.Issue.Create().
		SetTitle(strings.TrimSpace(p.Title)).
		SetBody(p.Body).
		SetPriority(entissue.Priority(priority)).
		SetProjectID(projectID).
		Save(ctx)
	if err != nil {
		return nil, fmt.Errorf("creating issue: %w", err)
	}
	return created, nil
}

// view loads one issue with everything Detail needs.
//
// All four collections hang off Issue itself, so this is a single eager-loaded
// query rather than a call into the ref and comment services — which do not
// exist yet, and which this package must not depend on when they do.
func (repository) view(ctx context.Context, cl *ent.Client, id int) (Detail, error) {
	e, err := cl.Issue.Query().
		Where(entissue.IDEQ(id)).
		WithProject().
		WithMilestone().
		WithLabels(func(q *ent.LabelQuery) { q.Order(entlabel.ByName()) }).
		WithComments(func(q *ent.CommentQuery) { q.Order(entcomment.ByAt(), entcomment.ByID()) }).
		// blocked_refs: rows where this issue is the blocked end, so the other
		// end is something it waits on.
		WithBlockedRefs(func(q *ent.RefQuery) { q.WithBlocker() }).
		// blocker_refs: rows where this issue is the blocker, so the other end
		// is something waiting on it.
		WithBlockerRefs(func(q *ent.RefQuery) { q.WithBlocked() }).
		Only(ctx)
	if err != nil {
		if ent.IsNotFound(err) {
			return Detail{}, errs.NotFoundf("issue %d", id)
		}
		return Detail{}, fmt.Errorf("loading issue %d: %w", id, err)
	}
	return toDetail(e), nil
}

// toIssue flattens an ent row and its eager-loaded edges.
//
// The enum casts are safe because the domain constants in model.go are spelled
// exactly as the schema's Values(...) — TestEnumsMatchSchema is what keeps that
// true when someone edits one side.
func toIssue(e *ent.Issue) Issue {
	iss := Issue{
		ID:        e.ID,
		Title:     e.Title,
		Body:      e.Body,
		Status:    Status(e.Status),
		Priority:  Priority(e.Priority),
		Labels:    []string{},
		CreatedAt: e.CreatedAt,
		UpdatedAt: e.UpdatedAt,
	}
	if e.ClosedAt != nil {
		// Copied, not aliased: the ent row is discarded here and nothing above
		// should hold a pointer into it.
		closed := *e.ClosedAt
		iss.ClosedAt = &closed
	}
	if p := e.Edges.Project; p != nil {
		iss.Project = p.Slug
	}
	if m := e.Edges.Milestone; m != nil {
		iss.Milestone = m.Name
	}
	for _, l := range e.Edges.Labels {
		iss.Labels = append(iss.Labels, l.Name)
	}
	return iss
}

// toDetail assembles the full view. Every slice is initialised, because the
// --json contract says an empty list prints as [] and never as null.
func toDetail(e *ent.Issue) Detail {
	d := Detail{
		Issue:      toIssue(e),
		Blockers:   []Link{},
		Subtasks:   []Link{},
		Dependents: []Link{},
		Comments:   []Comment{},
	}

	// A ref reads "blocked is blocked by blocker". On a subtask ref the parent
	// is the blocked end and the child is the blocker, which is why a parent's
	// children arrive through the same edge as its dependencies and are split
	// apart by kind here.
	for _, r := range e.Edges.BlockedRefs {
		other := r.Edges.Blocker
		if other == nil {
			continue
		}
		link := toLink(r, other)
		if r.Kind == entref.KindSubtask {
			d.Subtasks = append(d.Subtasks, link)
		} else {
			d.Blockers = append(d.Blockers, link)
		}
	}
	// The reverse edge is not split: whatever waits on this issue waits on it,
	// and a parent that would disappear from a child's view is worse than a
	// list with two kinds in it.
	for _, r := range e.Edges.BlockerRefs {
		if other := r.Edges.Blocked; other != nil {
			d.Dependents = append(d.Dependents, toLink(r, other))
		}
	}

	// By id, so `tt show` renders the same list twice in a row. Ent gives no
	// order for an eager-loaded edge with none asked for.
	byID := func(a, b Link) int { return a.ID - b.ID }
	slices.SortFunc(d.Blockers, byID)
	slices.SortFunc(d.Subtasks, byID)
	slices.SortFunc(d.Dependents, byID)

	for _, c := range e.Edges.Comments {
		d.Comments = append(d.Comments, Comment{
			ID: c.ID, At: c.At, Author: c.Author, Body: c.Body,
		})
	}

	// Counted over the slice that was just built rather than by re-querying.
	// query.List computes the same number with a GROUP BY because it has a
	// page of issues and no loaded children; the two arithmetics differ, but
	// the Rollup type is shared so the renderers cannot.
	d.Rollup = Rollup{Total: len(d.Subtasks)}
	for _, s := range d.Subtasks {
		if s.Status == StatusDone {
			d.Rollup.Done++
		}
	}
	return d
}

func toLink(r *ent.Ref, other *ent.Issue) Link {
	return Link{
		ID:     other.ID,
		Title:  other.Title,
		Status: Status(other.Status),
		Kind:   Kind(r.Kind),
	}
}
