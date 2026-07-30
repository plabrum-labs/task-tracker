package query

import (
	"context"
	"fmt"

	"entgo.io/ent/dialect/sql"

	"github.com/Plabrum/tt/ent"
	entissue "github.com/Plabrum/tt/ent/issue"
	entlabel "github.com/Plabrum/tt/ent/label"
	entmilestone "github.com/Plabrum/tt/ent/milestone"
	"github.com/Plabrum/tt/ent/predicate"
	entproject "github.com/Plabrum/tt/ent/project"
	entref "github.com/Plabrum/tt/ent/ref"
	"github.com/Plabrum/tt/internal/issue"
)

// List returns the rows matching p, in pick order.
//
// Four statements whether it returns one row or three hundred: the blocked flag
// and the rollup are batched over the ids the first query produced.
func List(ctx context.Context, cl *ent.Client, p ListParams) ([]Row, error) {
	preds, err := predicates(p)
	if err != nil {
		return nil, err
	}
	q := cl.Issue.Query().
		Where(preds...).
		WithProject().
		WithMilestone().
		WithLabels(func(lq *ent.LabelQuery) { lq.Order(entlabel.ByName()) }).
		Order(pickOrder()...)
	if p.Limit > 0 {
		q = q.Limit(p.Limit)
	}

	found, err := q.All(ctx)
	if err != nil {
		return nil, fmt.Errorf("listing issues: %w", err)
	}
	// Non-nil, so `tt ls --json` prints [] rather than null.
	if len(found) == 0 {
		return []Row{}, nil
	}

	ids := make([]int, len(found))
	for i, e := range found {
		ids[i] = e.ID
	}
	blocked, err := blockedSet(ctx, cl, ids)
	if err != nil {
		return nil, err
	}
	rollups, err := subtaskRollup(ctx, cl, ids)
	if err != nil {
		return nil, err
	}

	rows := make([]Row, 0, len(found))
	for _, e := range found {
		status, err := issue.StatusFromEnt(e.Status)
		if err != nil {
			return nil, fmt.Errorf("issue %d: %w", e.ID, err)
		}
		priority, err := issue.PriorityFromEnt(e.Priority)
		if err != nil {
			return nil, fmt.Errorf("issue %d: %w", e.ID, err)
		}
		row := Row{
			ID:       e.ID,
			Title:    e.Title,
			Status:   status,
			Priority: priority,
			Labels:   []string{},
			Blocked:  blocked[e.ID],
			// A miss is the zero Rollup, and Total == 0 renders no badge.
			Subtasks:  rollups[e.ID],
			CreatedAt: e.CreatedAt,
			UpdatedAt: e.UpdatedAt,
		}
		if pr := e.Edges.Project; pr != nil {
			row.Project = pr.Slug
		}
		if ms := e.Edges.Milestone; ms != nil {
			row.Milestone = ms.Name
		}
		for _, l := range e.Edges.Labels {
			row.Labels = append(row.Labels, l.Name)
		}
		rows = append(rows, row)
	}
	return rows, nil
}

// predicates turns the filters into a WHERE clause.
func predicates(p ListParams) ([]predicate.Issue, error) {
	var preds []predicate.Issue

	// "" is the absence of scoping, which is what -A passes.
	if p.Project != "" {
		preds = append(preds, entissue.HasProjectWith(entproject.SlugEQ(p.Project)))
	}

	// The default hides done work; -a passes all three explicitly.
	statuses := p.Statuses
	if len(statuses) == 0 {
		statuses = []issue.Status{issue.StatusTodo, issue.StatusDoing}
	}
	want := make([]entissue.Status, len(statuses))
	for i, st := range statuses {
		stored, err := issue.StatusToEnt(st)
		if err != nil {
			return nil, err
		}
		want[i] = stored
	}
	preds = append(preds, entissue.StatusIn(want...))

	// ANDed, one EXISTS each: `-l backend -l bug` means both, not either.
	for _, name := range p.Labels {
		preds = append(preds, entissue.HasLabelsWith(entlabel.NameEQ(name)))
	}
	if p.Milestone != "" {
		preds = append(preds, entissue.HasMilestoneWith(entmilestone.NameEQ(p.Milestone)))
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

// isBlocked is the one definition of "blocked", so --blocked and the Blocked
// column cannot disagree. It ignores kind: every ref blocks.
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

// blockedSet reports which of ids have at least one unfinished blocker, in one
// query.
func blockedSet(ctx context.Context, cl *ent.Client, ids []int) (map[int]bool, error) {
	found, err := cl.Issue.Query().
		Where(entissue.IDIn(ids...), isBlocked()).
		IDs(ctx)
	if err != nil {
		return nil, fmt.Errorf("resolving blocked issues: %w", err)
	}
	set := make(map[int]bool, len(found))
	for _, id := range found {
		set[id] = true
	}
	return set, nil
}

// refCount is one row of the grouped ref queries below. The json tags are what
// ent's scanner matches columns against.
type refCount struct {
	BlockedID int `json:"blocked_id"`
	N         int `json:"n"`
}

// subtaskRollup counts each issue's subtask children, and how many are done.
//
// Two queries rather than one: the done count needs the child's status, which
// lives on a joined table, and ent cannot express a filtered aggregate
// alongside a plain one in a single GROUP BY without a raw modifier.
//
// Both group by blocked_id, which is the parent — a ref reads "blocked is
// blocked by blocker", so on a subtask edge the child is the blocker.
func subtaskRollup(ctx context.Context, cl *ent.Client, ids []int) (map[int]issue.Rollup, error) {
	count := func(extra ...predicate.Ref) ([]refCount, error) {
		preds := append([]predicate.Ref{
			entref.KindEQ(entref.KindSubtask),
			entref.BlockedIDIn(ids...),
		}, extra...)

		var out []refCount
		err := cl.Ref.Query().
			Where(preds...).
			GroupBy(entref.FieldBlockedID).
			Aggregate(ent.As(ent.Count(), "n")).
			Scan(ctx, &out)
		return out, err
	}

	totals, err := count()
	if err != nil {
		return nil, fmt.Errorf("counting subtasks: %w", err)
	}
	done, err := count(entref.HasBlockerWith(entissue.StatusEQ(entissue.StatusDone)))
	if err != nil {
		return nil, fmt.Errorf("counting finished subtasks: %w", err)
	}

	rollups := make(map[int]issue.Rollup, len(totals))
	for _, r := range totals {
		rollups[r.BlockedID] = issue.Rollup{Total: r.N}
	}
	for _, r := range done {
		roll := rollups[r.BlockedID]
		roll.Done = r.N
		rollups[r.BlockedID] = roll
	}
	return rollups, nil
}
