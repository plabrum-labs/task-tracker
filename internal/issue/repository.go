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

// Create writes an issue into an existing project.
func Create(ctx context.Context, cl *ent.Client, projectID int, p AddParams) (Issue, error) {
	if err := p.validate(); err != nil {
		return Issue{}, err
	}

	created, err := insert(ctx, cl, projectID, p)
	if err != nil {
		return Issue{}, err
	}

	// Re-read through the same view Get uses, rather than assembling an Issue
	// from what was just written. It costs one query and buys the guarantee
	// that `tt add` and `tt show` can never disagree about what an issue is.
	d, err := view(ctx, cl, created.ID)
	if err != nil {
		return Issue{}, err
	}
	return d.Issue, nil
}

// Get returns one issue with its refs and comments. A missing id wraps
// errs.ErrNotFound.
func Get(ctx context.Context, cl *ent.Client, id int) (Detail, error) {
	return view(ctx, cl, id)
}

// insert writes a new issue. Status and the timestamps come from schema
// defaults, so a second write path cannot set them differently.
func insert(ctx context.Context, cl *ent.Client, projectID int, p AddParams) (*ent.Issue, error) {
	domainPriority := p.Priority
	if domainPriority == "" {
		domainPriority = PriorityNormal
	}
	priority, err := PriorityToEnt(domainPriority)
	if err != nil {
		return nil, err
	}
	created, err := cl.Issue.Create().
		SetTitle(strings.TrimSpace(p.Title)).
		SetBody(p.Body).
		SetPriority(priority).
		SetProjectID(projectID).
		Save(ctx)
	if err != nil {
		return nil, fmt.Errorf("creating issue: %w", err)
	}
	return created, nil
}

// view loads one issue with everything Detail needs. All four collections hang
// off Issue itself, so it stays one eager-loaded query.
func view(ctx context.Context, cl *ent.Client, id int) (Detail, error) {
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
	return toDetail(e)
}

// toIssue flattens an ent row and its eager-loaded edges. It fails on a row
// written by a newer binary, whose enum values this one has no constant for.
func toIssue(e *ent.Issue) (Issue, error) {
	status, err := StatusFromEnt(e.Status)
	if err != nil {
		return Issue{}, fmt.Errorf("issue %d: %w", e.ID, err)
	}
	priority, err := PriorityFromEnt(e.Priority)
	if err != nil {
		return Issue{}, fmt.Errorf("issue %d: %w", e.ID, err)
	}
	iss := Issue{
		ID:        e.ID,
		Title:     e.Title,
		Body:      e.Body,
		Status:    status,
		Priority:  priority,
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
	return iss, nil
}

// toDetail assembles the full view. Every slice is initialised, because an
// empty list prints as [] and never as null.
func toDetail(e *ent.Issue) (Detail, error) {
	iss, err := toIssue(e)
	if err != nil {
		return Detail{}, err
	}
	d := Detail{
		Issue:      iss,
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
		link, err := toLink(r, other)
		if err != nil {
			return Detail{}, err
		}
		if r.Kind == entref.KindSubtask {
			d.Subtasks = append(d.Subtasks, link)
		} else {
			d.Blockers = append(d.Blockers, link)
		}
	}
	// The reverse edge is not split by kind: a parent that disappeared from its
	// child's view would be worse than one list holding both kinds.
	for _, r := range e.Edges.BlockerRefs {
		other := r.Edges.Blocked
		if other == nil {
			continue
		}
		link, err := toLink(r, other)
		if err != nil {
			return Detail{}, err
		}
		d.Dependents = append(d.Dependents, link)
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

	// Counted over the slice just built. query.List reaches the same number
	// with a GROUP BY, having a page of issues and no loaded children.
	d.Rollup = Rollup{Total: len(d.Subtasks)}
	for _, s := range d.Subtasks {
		if s.Status == StatusDone {
			d.Rollup.Done++
		}
	}
	return d, nil
}

func toLink(r *ent.Ref, other *ent.Issue) (Link, error) {
	status, err := StatusFromEnt(other.Status)
	if err != nil {
		return Link{}, fmt.Errorf("issue %d: %w", other.ID, err)
	}
	kind, err := KindFromEnt(r.Kind)
	if err != nil {
		return Link{}, fmt.Errorf("ref %d: %w", r.ID, err)
	}
	return Link{
		ID:     other.ID,
		Title:  other.Title,
		Status: status,
		Kind:   kind,
	}, nil
}

// The conversions across the ent/domain boundary. Exhaustive switches rather
// than casts, so a value added to one side and not the other fails at the
// default arm instead of travelling as a constant nothing matches.
//
// Ent in an exported function signature is still unreachable from cli/ and ui/:
// neither can name entissue.Status without importing ent.

// StatusFromEnt converts a stored status to the domain value.
func StatusFromEnt(s entissue.Status) (Status, error) {
	switch s {
	case entissue.StatusTodo:
		return StatusTodo, nil
	case entissue.StatusDoing:
		return StatusDoing, nil
	case entissue.StatusDone:
		return StatusDone, nil
	default:
		return "", errs.Conflictf("stored status %q is not one this binary knows", s)
	}
}

// StatusToEnt converts a domain status to the stored value.
func StatusToEnt(s Status) (entissue.Status, error) {
	switch s {
	case StatusTodo:
		return entissue.StatusTodo, nil
	case StatusDoing:
		return entissue.StatusDoing, nil
	case StatusDone:
		return entissue.StatusDone, nil
	default:
		return "", errs.Invalidf("unknown status %q", s)
	}
}

// PriorityFromEnt converts a stored priority to the domain value.
func PriorityFromEnt(p entissue.Priority) (Priority, error) {
	switch p {
	case entissue.PriorityNormal:
		return PriorityNormal, nil
	case entissue.PriorityHi:
		return PriorityHi, nil
	default:
		return "", errs.Conflictf("stored priority %q is not one this binary knows", p)
	}
}

// PriorityToEnt converts a domain priority to the stored value.
func PriorityToEnt(p Priority) (entissue.Priority, error) {
	switch p {
	case PriorityNormal:
		return entissue.PriorityNormal, nil
	case PriorityHi:
		return entissue.PriorityHi, nil
	default:
		return "", errs.Invalidf("unknown priority %q", p)
	}
}

// KindFromEnt converts a stored ref kind to the domain value.
func KindFromEnt(k entref.Kind) (Kind, error) {
	switch k {
	case entref.KindDep:
		return KindDep, nil
	case entref.KindSubtask:
		return KindSubtask, nil
	default:
		return "", errs.Conflictf("stored ref kind %q is not one this binary knows", k)
	}
}

// KindToEnt converts a domain ref kind to the stored value.
func KindToEnt(k Kind) (entref.Kind, error) {
	switch k {
	case KindDep:
		return entref.KindDep, nil
	case KindSubtask:
		return entref.KindSubtask, nil
	default:
		return "", errs.Invalidf("unknown ref kind %q", k)
	}
}
