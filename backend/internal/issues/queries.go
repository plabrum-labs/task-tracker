// Package issues owns the unit of work: reading it, deciding what it offers,
// and every write that changes it.
//
// A domain owns every read of its own object however many tables it joins, so
// the list read that pulls in labels, milestones and refs lives here rather
// than in a query package of its own.
package issues

import (
	"context"
	"fmt"

	"entgo.io/ent/dialect/sql"

	"github.com/Plabrum/tt/backend/contract"
	"github.com/Plabrum/tt/backend/errs"
	"github.com/Plabrum/tt/backend/internal/comments"
	"github.com/Plabrum/tt/backend/internal/ent"
	entissue "github.com/Plabrum/tt/backend/internal/ent/issue"
	entlabel "github.com/Plabrum/tt/backend/internal/ent/label"
	entmilestone "github.com/Plabrum/tt/backend/internal/ent/milestone"
	"github.com/Plabrum/tt/backend/internal/ent/predicate"
	entproject "github.com/Plabrum/tt/backend/internal/ent/project"
	"github.com/Plabrum/tt/backend/internal/labels"
	"github.com/Plabrum/tt/backend/internal/milestones"
	"github.com/Plabrum/tt/backend/internal/projects"
)

// Load returns one issue with everything hanging off it. A missing id wraps
// errs.ErrNotFound.
//
// The graph is recursive, so it stops one level down: the issues in Parent,
// Subtasks, Blockers and Blocks come back at the depth List returns, with their
// own relations empty.
func Load(ctx context.Context, cl *ent.Client, id int) (contract.Issue, error) {
	e, err := withActionEdges(cl.Issue.Query().Where(entissue.IDEQ(id))).Only(ctx)
	if err != nil {
		if ent.IsNotFound(err) {
			return contract.Issue{}, errs.NotFoundf("issue %d", id)
		}
		return contract.Issue{}, fmt.Errorf("loading issue %d: %w", id, err)
	}

	out, err := convert(e)
	if err != nil {
		return contract.Issue{}, err
	}

	// One query per relation rather than a deeper eager-load: each returns
	// issues at list depth, which is its own load with its own action edges.
	if e.ParentID != nil {
		parent, err := one(ctx, cl, entissue.IDEQ(*e.ParentID))
		if err != nil {
			return contract.Issue{}, err
		}
		out.Parent = parent
	}
	if out.Subtasks, err = many(ctx, cl, entissue.ParentIDEQ(id)); err != nil {
		return contract.Issue{}, err
	}
	// A ref reads "blocked is blocked by blocker". This issue's blockers are
	// therefore the issues whose `blocks` edge names it.
	if out.Blockers, err = many(ctx, cl, entissue.HasBlocksWith(entissue.IDEQ(id))); err != nil {
		return contract.Issue{}, err
	}
	if out.Blocks, err = many(ctx, cl, entissue.HasBlockedByWith(entissue.IDEQ(id))); err != nil {
		return contract.Issue{}, err
	}
	if out.Comments, err = comments.ListForIssue(ctx, cl, id); err != nil {
		return contract.Issue{}, err
	}
	return out, nil
}

// List returns the issues matching p, in pick order.
func List(ctx context.Context, cl *ent.Client, p contract.IssueListParams) ([]contract.Issue, error) {
	preds, err := predicates(ctx, cl, p)
	if err != nil {
		return nil, err
	}
	q := withActionEdges(cl.Issue.Query().Where(preds...)).Order(pickOrder()...)
	if p.Limit > 0 {
		q = q.Limit(p.Limit)
	}

	found, err := q.All(ctx)
	if err != nil {
		return nil, fmt.Errorf("listing issues: %w", err)
	}
	return convertAll(found)
}

// withActionEdges eager-loads exactly what a converted issue needs: its project
// and milestone, its labels, and the two edges the action rules read.
//
// Every one is a batched load — one query per edge for the whole page, not one
// per row — which is what lets a list row carry actions as correct as a detail
// view's. Dropping an edge here does not fail a query; it silently changes what
// the rules decide, so the set is chosen by actions.go and not by this file.
func withActionEdges(q *ent.IssueQuery) *ent.IssueQuery {
	return q.
		WithProject().
		WithMilestone().
		WithLabels(func(lq *ent.LabelQuery) { lq.Order(entlabel.ByName()) }).
		WithSubtasks(func(iq *ent.IssueQuery) { iq.Order(entissue.ByID()) }).
		WithBlockedBy(func(iq *ent.IssueQuery) { iq.Order(entissue.ByID()) })
}

// one loads a single issue at list depth, or nil when the predicate matches
// nothing.
func one(ctx context.Context, cl *ent.Client, pred predicate.Issue) (*contract.Issue, error) {
	found, err := many(ctx, cl, pred)
	if err != nil {
		return nil, err
	}
	if len(found) == 0 {
		return nil, nil
	}
	return &found[0], nil
}

// many loads issues at list depth, by id.
func many(ctx context.Context, cl *ent.Client, pred predicate.Issue) ([]contract.Issue, error) {
	found, err := withActionEdges(cl.Issue.Query().Where(pred)).
		Order(entissue.ByID()).
		All(ctx)
	if err != nil {
		return nil, fmt.Errorf("loading related issues: %w", err)
	}
	return convertAll(found)
}

// convertAll flattens ent rows into contract types. The result is never nil, so
// --json prints [] rather than null.
func convertAll(rows []*ent.Issue) ([]contract.Issue, error) {
	out := make([]contract.Issue, 0, len(rows))
	for _, e := range rows {
		converted, err := convert(e)
		if err != nil {
			return nil, err
		}
		out = append(out, converted)
	}
	return out, nil
}

// convert flattens an ent row and its eager-loaded edges, actions included.
//
// Every slice is initialised, because an empty list prints as [] and never as
// null. The relation slices stay empty here: Load is what fills them.
func convert(e *ent.Issue) (contract.Issue, error) {
	status, err := statusFromEnt(e.Status)
	if err != nil {
		return contract.Issue{}, fmt.Errorf("issue %d: %w", e.ID, err)
	}
	priority, err := priorityFromEnt(e.Priority)
	if err != nil {
		return contract.Issue{}, fmt.Errorf("issue %d: %w", e.ID, err)
	}

	out := contract.Issue{
		ID:        e.ID,
		Title:     e.Title,
		Body:      e.Body,
		Status:    status,
		Priority:  priority,
		CreatedAt: e.CreatedAt,
		UpdatedAt: e.UpdatedAt,
		Labels:    []contract.Label{},
		Subtasks:  []contract.Issue{},
		Blockers:  []contract.Issue{},
		Blocks:    []contract.Issue{},
		Comments:  []contract.Comment{},
		Blocked:   blocked(e),
		Actions:   issueActions.Available(e),
	}
	if e.ClosedAt != nil {
		// Copied, not aliased: the ent row is discarded here and nothing above
		// should hold a pointer into it.
		closed := *e.ClosedAt
		out.ClosedAt = &closed
	}
	if p := e.Edges.Project; p != nil {
		project, err := projects.Convert(p)
		if err != nil {
			return contract.Issue{}, err
		}
		out.Project = project
	}
	if m := e.Edges.Milestone; m != nil {
		milestone := milestones.Convert(m)
		out.Milestone = &milestone
	}
	for _, l := range e.Edges.Labels {
		out.Labels = append(out.Labels, labels.Convert(l))
	}
	return out, nil
}

// predicates turns the filters into a WHERE clause.
func predicates(ctx context.Context, cl *ent.Client, p contract.IssueListParams) ([]predicate.Issue, error) {
	var preds []predicate.Issue

	if p.Project != "" {
		// Naming a project reaches into it whatever its status: asking for an
		// archived project's issues is the way to see them.
		preds = append(preds, entissue.HasProjectWith(entproject.SlugEQ(projects.Slugify(p.Project))))
	} else {
		// Archiving is what takes a project's work out of the way, so an
		// unscoped list is over the active ones.
		active, err := projects.ActiveIDs(ctx, cl)
		if err != nil {
			return nil, err
		}
		preds = append(preds, entissue.HasProjectWith(entproject.IDIn(active...)))
	}

	// The default hides done work; asking for it explicitly is what shows it.
	statuses := p.Statuses
	if len(statuses) == 0 {
		statuses = []contract.IssueStatus{contract.IssueTodo, contract.IssueDoing}
	}
	want := make([]entissue.Status, 0, len(statuses))
	for _, st := range statuses {
		stored, err := statusToEnt(st)
		if err != nil {
			return nil, err
		}
		want = append(want, stored)
	}
	preds = append(preds, entissue.StatusIn(want...))

	// ANDed, one EXISTS each: `-l backend -l bug` means both, not either.
	for _, name := range p.Labels {
		preds = append(preds, entissue.HasLabelsWith(entlabel.NameEQ(labels.Normalise(name))))
	}
	if p.Milestone != "" {
		preds = append(preds, entissue.HasMilestoneWith(entmilestone.NameEQ(milestones.Normalise(p.Milestone))))
	}
	// Title only: searching bodies would mean loading them.
	if p.Search != "" {
		preds = append(preds, entissue.TitleContainsFold(p.Search))
	}
	if p.BlockedOnly {
		preds = append(preds, isBlocked())
	}
	return preds, nil
}

// isBlocked is the one definition of "blocked" as a query, so --blocked and the
// Blocked field cannot disagree about which issues qualify.
func isBlocked() predicate.Issue {
	return entissue.HasBlockedByWith(entissue.StatusNEQ(entissue.StatusDone))
}

// pickOrder is high priority first, then oldest first, then by id.
//
// The priority term is a CASE rather than an ORDER BY on the column: the enum
// is stored as text, so the column sorts alphabetically. ASC happens to put
// "hi" above "normal" today and would invert the day a third level is added.
func pickOrder() []entissue.OrderOption {
	return []entissue.OrderOption{
		func(s *sql.Selector) {
			s.OrderExpr(sql.Expr(
				"CASE WHEN "+s.C(entissue.FieldPriority)+" = ? THEN 0 ELSE 1 END",
				string(entissue.PriorityHi),
			))
		},
		entissue.ByCreatedAt(sql.OrderAsc()),
		entissue.ByID(sql.OrderAsc()),
	}
}

// The conversions across the ent/domain boundary. Exhaustive switches rather
// than casts, so a value added to one side and not the other fails at the
// default arm instead of travelling as a constant nothing matches.

func statusFromEnt(s entissue.Status) (contract.IssueStatus, error) {
	switch s {
	case entissue.StatusTodo:
		return contract.IssueTodo, nil
	case entissue.StatusDoing:
		return contract.IssueDoing, nil
	case entissue.StatusDone:
		return contract.IssueDone, nil
	default:
		return "", errs.Conflictf("stored status %q is not one this binary knows", s)
	}
}

func statusToEnt(s contract.IssueStatus) (entissue.Status, error) {
	switch s {
	case contract.IssueTodo:
		return entissue.StatusTodo, nil
	case contract.IssueDoing:
		return entissue.StatusDoing, nil
	case contract.IssueDone:
		return entissue.StatusDone, nil
	default:
		return "", errs.Invalidf("unknown status %q", s)
	}
}

func priorityFromEnt(p entissue.Priority) (contract.Priority, error) {
	switch p {
	case entissue.PriorityNormal:
		return contract.PriorityNormal, nil
	case entissue.PriorityHi:
		return contract.PriorityHi, nil
	default:
		return "", errs.Conflictf("stored priority %q is not one this binary knows", p)
	}
}

func priorityToEnt(p contract.Priority) (entissue.Priority, error) {
	switch p {
	case contract.PriorityNormal:
		return entissue.PriorityNormal, nil
	case contract.PriorityHi:
		return entissue.PriorityHi, nil
	default:
		return "", errs.Invalidf("unknown priority %q", p)
	}
}
