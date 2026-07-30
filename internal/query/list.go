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

// Service answers the list reads.
type Service struct {
	client *ent.Client
}

func NewService(client *ent.Client) *Service {
	return &Service{client: client}
}

// List returns the rows matching p, in pick order.
func (s *Service) List(ctx context.Context, p ListParams) ([]Row, error) {
	// A read needs no transaction. The three follow-up queries are keyed to
	// the ids the first one returned, so a concurrent write can at worst make
	// a flag stale by one refresh — never make a row incoherent.
	return s.ListIn(ctx, s.client, p)
}

// ListIn is List against a caller's client.
//
// It is four statements, and stays four statements whether it returns one row
// or three hundred: labels and the milestone name are eager-loaded, and the
// blocked flag and subtask rollup are batched over the ids the first query
// produced. Nothing here scales with the row count.
func (s *Service) ListIn(ctx context.Context, cl *ent.Client, p ListParams) ([]Row, error) {
	q := cl.Issue.Query().
		Where(predicates(p)...).
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
	// Non-nil, so `tt ls --json` prints [] rather than null on a fresh
	// database. The empty case also skips three pointless round trips.
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
		row := Row{
			ID:       e.ID,
			Title:    e.Title,
			Status:   issue.Status(e.Status),
			Priority: issue.Priority(e.Priority),
			Labels:   []string{},
			Blocked:  blocked[e.ID],
			// A miss is the zero Rollup, which is exactly right: Total == 0
			// renders no badge, and an issue with no subtasks is absent from
			// the map rather than present with a zero.
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
func predicates(p ListParams) []predicate.Issue {
	var preds []predicate.Issue

	// "" is not a project, it is the absence of scoping — what -A passes.
	if p.Project != "" {
		preds = append(preds, entissue.HasProjectWith(entproject.SlugEQ(p.Project)))
	}

	// The default hides done work: a list you have to filter every time is a
	// list you stop reading. -a passes all three explicitly.
	statuses := p.Statuses
	if len(statuses) == 0 {
		statuses = []issue.Status{issue.StatusTodo, issue.StatusDoing}
	}
	want := make([]entissue.Status, len(statuses))
	for i, st := range statuses {
		want[i] = entissue.Status(st)
	}
	preds = append(preds, entissue.StatusIn(want...))

	// ANDed, one EXISTS each: `-l backend -l bug` means both, not either.
	for _, name := range p.Labels {
		preds = append(preds, entissue.HasLabelsWith(entlabel.NameEQ(name)))
	}
	if p.Milestone != "" {
		preds = append(preds, entissue.HasMilestoneWith(entmilestone.NameEQ(p.Milestone)))
	}
	// Title only. Searching bodies would need the bodies loaded, and a
	// substring hit inside a paragraph is not what someone typing two words at
	// a prompt is looking for.
	if p.Search != "" {
		preds = append(preds, entissue.TitleContainsFold(p.Search))
	}
	if p.BlockedOnly {
		preds = append(preds, isBlocked())
	}
	return preds
}

// isBlocked is the single definition of "blocked", used by both --blocked and
// the Blocked column. One expression means the flag and the column cannot
// disagree about which issues they are talking about.
//
// It ignores kind deliberately: a subtask blocks its parent exactly as a
// dependency does, which is the whole reason subtask is a subset of ref rather
// than a parallel relation.
func isBlocked() predicate.Issue {
	return entissue.HasBlockedByWith(entissue.StatusNEQ(entissue.StatusDone))
}

// pickOrder is what the top of the list means: high priority first, then
// oldest first, then by id.
//
// The priority term is a CASE rather than an ORDER BY on the column because
// the enum is stored as text. `DESC` would sort "normal" above "hi", the exact
// inverse of the intent; `ASC` happens to be right today only because
// "hi" < "normal" alphabetically, and would silently break the day a third
// level is added. Spelling the order out means the sort follows the enum's
// meaning rather than its spelling. TestListPickOrder is the regression test.
//
// The id tiebreak is not cosmetic: the TUI degrades its selection by id across
// refreshes, which needs the list to be stable when two issues share a
// created_at — and at SQLite's timestamp resolution, two issues added in one
// script often do.
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
// Two queries rather than one because the done count needs the *child's*
// status, which lives on a joined table, and ent cannot express a filtered
// aggregate alongside a plain one in a single GROUP BY without dropping to a
// raw modifier.
//
// Both group by blocked_id. A ref reads "blocked is blocked by blocker", so on
// a subtask edge the parent is the blocked end and the child is the blocker —
// grouping by blocked_id groups by parent. A child under two parents therefore
// counts toward both, which is the DAG behaviour a tree could not express and
// the reason subtasks are refs at all.
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
