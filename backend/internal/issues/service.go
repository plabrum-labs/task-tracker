package issues

import (
	"context"
	"fmt"
	"strings"
	"time"

	"github.com/Plabrum/tt/backend/errs"
	"github.com/Plabrum/tt/backend/internal/comments"
	"github.com/Plabrum/tt/backend/internal/db"
	"github.com/Plabrum/tt/backend/internal/ent"
	entissue "github.com/Plabrum/tt/backend/internal/ent/issue"
	entlabel "github.com/Plabrum/tt/backend/internal/ent/label"
	entproject "github.com/Plabrum/tt/backend/internal/ent/project"
	entref "github.com/Plabrum/tt/backend/internal/ent/ref"
	"github.com/Plabrum/tt/backend/internal/labels"
	"github.com/Plabrum/tt/backend/internal/milestones"
	"github.com/Plabrum/tt/backend/internal/projects"
	"github.com/Plabrum/tt/backend/models"
)

// Create captures a new issue, creating its project on first use.
func Create(ctx context.Context, cl *ent.Client, p models.IssueAddParams) (models.Issue, error) {
	var out models.Issue
	err := db.WithTx(ctx, cl, func(tx *ent.Client) error {
		var err error
		out, err = CreateIn(ctx, tx, p)
		return err
	})
	return out, err
}

// CreateIn is Create inside a caller's transaction.
//
// The project auto-create runs in the same transaction as the insert, so a
// rejected issue takes its container with it. Capture should never leave behind
// an empty project for work that was never written.
func CreateIn(ctx context.Context, tx *ent.Client, p models.IssueAddParams) (models.Issue, error) {
	if err := p.Validate(); err != nil {
		return models.Issue{}, err
	}
	projectID, err := projects.EnsureIn(ctx, tx, p.Project)
	if err != nil {
		return models.Issue{}, err
	}
	if err := refuseArchived(ctx, tx, projectID); err != nil {
		return models.Issue{}, err
	}
	return insert(ctx, tx, projectID, nil, p)
}

// AddSubIssue captures a new issue as a child of an existing one.
func AddSubIssue(ctx context.Context, cl *ent.Client, id int, p models.IssueAddParams) (models.Issue, error) {
	var out models.Issue
	err := db.WithTx(ctx, cl, func(tx *ent.Client) error {
		var err error
		out, err = AddSubIssueIn(ctx, tx, id, p)
		return err
	})
	return out, err
}

// AddSubIssueIn is AddSubIssue inside a caller's transaction.
//
// The child lands in its parent's project whatever the params say: a sub-issue
// filed somewhere else would leave the parent's project showing a rollup over
// work it does not contain.
func AddSubIssueIn(ctx context.Context, tx *ent.Client, id int, p models.IssueAddParams) (models.Issue, error) {
	parent, err := row(ctx, tx, id)
	if err != nil {
		return models.Issue{}, err
	}
	if !offers(parent, models.KeyIssueAddSubIssue) {
		return models.Issue{}, errs.Conflictf("issue %d cannot take a sub-issue", id)
	}
	projectID, err := parent.QueryProject().OnlyID(ctx)
	if err != nil {
		return models.Issue{}, fmt.Errorf("resolving project of issue %d: %w", id, err)
	}
	if err := refuseArchived(ctx, tx, projectID); err != nil {
		return models.Issue{}, err
	}

	p.Project = "" // resolved from the parent, not from the caller
	if err := validateChild(p); err != nil {
		return models.Issue{}, err
	}
	return insert(ctx, tx, projectID, &parent.ID, p)
}

// insert writes a new issue and reloads it. Status and the timestamps come from
// schema defaults, so a second write path cannot set them differently.
func insert(
	ctx context.Context,
	tx *ent.Client,
	projectID int,
	parentID *int,
	p models.IssueAddParams,
) (models.Issue, error) {
	priority := p.Priority
	if priority == "" {
		priority = models.PriorityNormal
	}
	stored, err := priorityToEnt(priority)
	if err != nil {
		return models.Issue{}, err
	}

	create := tx.Issue.Create().
		SetTitle(strings.TrimSpace(p.Title)).
		SetBody(p.Body).
		SetPriority(stored).
		SetProjectID(projectID)
	if parentID != nil {
		create = create.SetParentID(*parentID)
	}
	if p.Milestone != "" {
		milestoneID, err := milestones.EnsureIn(ctx, tx, projectID, p.Milestone)
		if err != nil {
			return models.Issue{}, err
		}
		create = create.SetMilestoneID(milestoneID)
	}
	for _, name := range p.Labels {
		labelID, err := labels.EnsureIn(ctx, tx, name)
		if err != nil {
			return models.Issue{}, err
		}
		create = create.AddLabelIDs(labelID)
	}

	created, err := create.Save(ctx)
	if err != nil {
		return models.Issue{}, fmt.Errorf("creating issue: %w", err)
	}
	// Re-read through the same loader the reads use, rather than assembling an
	// issue from what was just written. It costs one query and buys the
	// guarantee that a write and a read can never disagree.
	return Load(ctx, tx, created.ID)
}

// Start moves an issue into doing.
func Start(ctx context.Context, cl *ent.Client, id int) (models.Issue, error) {
	return write(ctx, cl, id, StartIn)
}

// StartIn is Start inside a caller's transaction.
func StartIn(ctx context.Context, tx *ent.Client, id int) (models.Issue, error) {
	return transition(ctx, tx, id, models.KeyIssueStart, func(u *ent.IssueUpdateOne) *ent.IssueUpdateOne {
		return u.SetStatus(entissue.StatusDoing).ClearClosedAt()
	})
}

// Close marks an issue done.
func Close(ctx context.Context, cl *ent.Client, id int) (models.Issue, error) {
	return write(ctx, cl, id, CloseIn)
}

// CloseIn is Close inside a caller's transaction.
func CloseIn(ctx context.Context, tx *ent.Client, id int) (models.Issue, error) {
	return transition(ctx, tx, id, models.KeyIssueClose, func(u *ent.IssueUpdateOne) *ent.IssueUpdateOne {
		return u.SetStatus(entissue.StatusDone).SetClosedAt(time.Now())
	})
}

// Reopen brings a closed issue back to todo.
func Reopen(ctx context.Context, cl *ent.Client, id int) (models.Issue, error) {
	return write(ctx, cl, id, ReopenIn)
}

// ReopenIn is Reopen inside a caller's transaction.
func ReopenIn(ctx context.Context, tx *ent.Client, id int) (models.Issue, error) {
	return transition(ctx, tx, id, models.KeyIssueReopen, func(u *ent.IssueUpdateOne) *ent.IssueUpdateOne {
		return u.SetStatus(entissue.StatusTodo).ClearClosedAt()
	})
}

// transition applies a status change, having checked the same rule the menu
// checked. The menu is a snapshot; this is what enforces.
func transition(
	ctx context.Context,
	tx *ent.Client,
	id int,
	key models.IssueKey,
	apply func(*ent.IssueUpdateOne) *ent.IssueUpdateOne,
) (models.Issue, error) {
	e, err := row(ctx, tx, id)
	if err != nil {
		return models.Issue{}, err
	}
	if err := refuse(e, key); err != nil {
		return models.Issue{}, err
	}
	if _, err := apply(e.Update()).Save(ctx); err != nil {
		return models.Issue{}, fmt.Errorf("updating issue %d: %w", id, err)
	}
	return Load(ctx, tx, id)
}

// Edit changes an issue's title or body.
func Edit(ctx context.Context, cl *ent.Client, id int, p models.IssueEditParams) (models.Issue, error) {
	var out models.Issue
	err := db.WithTx(ctx, cl, func(tx *ent.Client) error {
		var err error
		out, err = EditIn(ctx, tx, id, p)
		return err
	})
	return out, err
}

// EditIn is Edit inside a caller's transaction.
func EditIn(ctx context.Context, tx *ent.Client, id int, p models.IssueEditParams) (models.Issue, error) {
	e, err := row(ctx, tx, id)
	if err != nil {
		return models.Issue{}, err
	}
	if err := refuse(e, models.KeyIssueEdit); err != nil {
		return models.Issue{}, err
	}
	if p.Title == nil && p.Body == nil {
		return models.Issue{}, errs.Invalidf("nothing to change")
	}

	u := e.Update()
	if p.Title != nil {
		title := strings.TrimSpace(*p.Title)
		if title == "" {
			return models.Issue{}, errs.Invalidf("title is required")
		}
		u = u.SetTitle(title)
	}
	if p.Body != nil {
		u = u.SetBody(*p.Body)
	}
	if _, err := u.Save(ctx); err != nil {
		return models.Issue{}, fmt.Errorf("editing issue %d: %w", id, err)
	}
	return Load(ctx, tx, id)
}

// Delete removes an issue.
func Delete(ctx context.Context, cl *ent.Client, id int) error {
	return db.WithTx(ctx, cl, func(tx *ent.Client) error {
		return DeleteIn(ctx, tx, id)
	})
}

// DeleteIn is Delete inside a caller's transaction.
//
// Its comments and refs go with it by cascade, and its sub-issues survive with
// their parent_id cleared: deleting a parent should not delete work nobody
// asked to lose.
func DeleteIn(ctx context.Context, tx *ent.Client, id int) error {
	e, err := row(ctx, tx, id)
	if err != nil {
		return err
	}
	if err := refuse(e, models.KeyIssueDelete); err != nil {
		return err
	}
	if err := tx.Issue.DeleteOneID(id).Exec(ctx); err != nil {
		return fmt.Errorf("deleting issue %d: %w", id, err)
	}
	return nil
}

// SetPriority changes an issue's pick-order bump.
func SetPriority(ctx context.Context, cl *ent.Client, id int, p models.Priority) (models.Issue, error) {
	var out models.Issue
	err := db.WithTx(ctx, cl, func(tx *ent.Client) error {
		var err error
		out, err = SetPriorityIn(ctx, tx, id, p)
		return err
	})
	return out, err
}

// SetPriorityIn is SetPriority inside a caller's transaction.
func SetPriorityIn(ctx context.Context, tx *ent.Client, id int, p models.Priority) (models.Issue, error) {
	stored, err := priorityToEnt(p)
	if err != nil {
		return models.Issue{}, err
	}
	e, err := row(ctx, tx, id)
	if err != nil {
		return models.Issue{}, err
	}
	if err := refuse(e, models.KeyIssueSetPriority); err != nil {
		return models.Issue{}, err
	}
	if _, err := e.Update().SetPriority(stored).Save(ctx); err != nil {
		return models.Issue{}, fmt.Errorf("setting priority of issue %d: %w", id, err)
	}
	return Load(ctx, tx, id)
}

// SetMilestone files an issue under a milestone, creating it on first use. An
// empty name clears it.
func SetMilestone(ctx context.Context, cl *ent.Client, id int, name string) (models.Issue, error) {
	var out models.Issue
	err := db.WithTx(ctx, cl, func(tx *ent.Client) error {
		var err error
		out, err = SetMilestoneIn(ctx, tx, id, name)
		return err
	})
	return out, err
}

// SetMilestoneIn is SetMilestone inside a caller's transaction.
func SetMilestoneIn(ctx context.Context, tx *ent.Client, id int, name string) (models.Issue, error) {
	e, err := row(ctx, tx, id)
	if err != nil {
		return models.Issue{}, err
	}
	if err := refuse(e, models.KeyIssueSetMilestone); err != nil {
		return models.Issue{}, err
	}

	u := e.Update()
	if strings.TrimSpace(name) == "" {
		u = u.ClearMilestone()
	} else {
		projectID, err := e.QueryProject().OnlyID(ctx)
		if err != nil {
			return models.Issue{}, fmt.Errorf("resolving project of issue %d: %w", id, err)
		}
		milestoneID, err := milestones.EnsureIn(ctx, tx, projectID, name)
		if err != nil {
			return models.Issue{}, err
		}
		u = u.SetMilestoneID(milestoneID)
	}
	if _, err := u.Save(ctx); err != nil {
		return models.Issue{}, fmt.Errorf("setting milestone of issue %d: %w", id, err)
	}
	return Load(ctx, tx, id)
}

// AddLabel puts a label on an issue, creating it on first use.
func AddLabel(ctx context.Context, cl *ent.Client, id int, name string) (models.Issue, error) {
	var out models.Issue
	err := db.WithTx(ctx, cl, func(tx *ent.Client) error {
		var err error
		out, err = AddLabelIn(ctx, tx, id, name)
		return err
	})
	return out, err
}

// AddLabelIn is AddLabel inside a caller's transaction.
func AddLabelIn(ctx context.Context, tx *ent.Client, id int, name string) (models.Issue, error) {
	e, err := row(ctx, tx, id)
	if err != nil {
		return models.Issue{}, err
	}
	if err := refuse(e, models.KeyIssueAddLabel); err != nil {
		return models.Issue{}, err
	}
	labelID, err := labels.EnsureIn(ctx, tx, name)
	if err != nil {
		return models.Issue{}, err
	}
	// Already-present is not an error: the label is on the issue either way,
	// and AddLabelIDs on an existing edge violates the join table's key.
	has, err := e.QueryLabels().Where(entlabel.IDEQ(labelID)).Exist(ctx)
	if err != nil {
		return models.Issue{}, fmt.Errorf("checking labels of issue %d: %w", id, err)
	}
	if !has {
		if _, err := e.Update().AddLabelIDs(labelID).Save(ctx); err != nil {
			return models.Issue{}, fmt.Errorf("labelling issue %d: %w", id, err)
		}
	}
	return Load(ctx, tx, id)
}

// RemoveLabel takes a label off an issue.
func RemoveLabel(ctx context.Context, cl *ent.Client, id int, name string) (models.Issue, error) {
	var out models.Issue
	err := db.WithTx(ctx, cl, func(tx *ent.Client) error {
		var err error
		out, err = RemoveLabelIn(ctx, tx, id, name)
		return err
	})
	return out, err
}

// RemoveLabelIn is RemoveLabel inside a caller's transaction.
func RemoveLabelIn(ctx context.Context, tx *ent.Client, id int, name string) (models.Issue, error) {
	e, err := row(ctx, tx, id)
	if err != nil {
		return models.Issue{}, err
	}
	if err := refuse(e, models.KeyIssueRemoveLabel); err != nil {
		return models.Issue{}, err
	}
	labelID, err := labels.Lookup(ctx, tx, name)
	if err != nil {
		return models.Issue{}, err
	}
	has, err := e.QueryLabels().Where(entlabel.IDEQ(labelID)).Exist(ctx)
	if err != nil {
		return models.Issue{}, fmt.Errorf("checking labels of issue %d: %w", id, err)
	}
	if !has {
		return models.Issue{}, errs.Conflictf("issue %d is not labelled %q", id, labels.Normalise(name))
	}
	if _, err := e.Update().RemoveLabelIDs(labelID).Save(ctx); err != nil {
		return models.Issue{}, fmt.Errorf("unlabelling issue %d: %w", id, err)
	}
	return Load(ctx, tx, id)
}

// AddDep records that an issue waits on another.
func AddDep(ctx context.Context, cl *ent.Client, id, blockerID int) (models.Issue, error) {
	var out models.Issue
	err := db.WithTx(ctx, cl, func(tx *ent.Client) error {
		var err error
		out, err = AddDepIn(ctx, tx, id, blockerID)
		return err
	})
	return out, err
}

// AddDepIn is AddDep inside a caller's transaction.
//
// The cycle check walks the dependency graph, so it is not a menu rule: a menu
// reads one level down and this needs however many the chain is deep.
func AddDepIn(ctx context.Context, tx *ent.Client, id, blockerID int) (models.Issue, error) {
	e, err := row(ctx, tx, id)
	if err != nil {
		return models.Issue{}, err
	}
	if err := refuse(e, models.KeyIssueAddDep); err != nil {
		return models.Issue{}, err
	}
	if id == blockerID {
		return models.Issue{}, errs.Conflictf("issue %d cannot block itself", id)
	}
	if _, err := row(ctx, tx, blockerID); err != nil {
		return models.Issue{}, err
	}

	exists, err := tx.Ref.Query().
		Where(entref.BlockedIDEQ(id), entref.BlockerIDEQ(blockerID)).
		Exist(ctx)
	if err != nil {
		return models.Issue{}, fmt.Errorf("checking dependencies of issue %d: %w", id, err)
	}
	if exists {
		return models.Issue{}, errs.Conflictf("issue %d already waits on issue %d", id, blockerID)
	}
	if err := refuseCycle(ctx, tx, id, blockerID); err != nil {
		return models.Issue{}, err
	}

	if _, err := tx.Ref.Create().SetBlockedID(id).SetBlockerID(blockerID).Save(ctx); err != nil {
		return models.Issue{}, fmt.Errorf("adding dependency %d -> %d: %w", id, blockerID, err)
	}
	return Load(ctx, tx, id)
}

// refuseCycle rejects a dependency that would close a loop, by walking from the
// proposed blocker to see whether the issue itself is already upstream of it.
func refuseCycle(ctx context.Context, tx *ent.Client, id, blockerID int) error {
	seen := map[int]bool{blockerID: true}
	frontier := []int{blockerID}
	for len(frontier) > 0 {
		next, err := tx.Ref.Query().
			Where(entref.BlockedIDIn(frontier...)).
			Select(entref.FieldBlockerID).
			Ints(ctx)
		if err != nil {
			return fmt.Errorf("walking dependencies of issue %d: %w", blockerID, err)
		}
		frontier = frontier[:0]
		for _, n := range next {
			if n == id {
				return errs.Conflictf("issue %d already waits on issue %d, directly or through others", blockerID, id)
			}
			if !seen[n] {
				seen[n] = true
				frontier = append(frontier, n)
			}
		}
	}
	return nil
}

// RemoveDep drops a dependency.
func RemoveDep(ctx context.Context, cl *ent.Client, id, blockerID int) (models.Issue, error) {
	var out models.Issue
	err := db.WithTx(ctx, cl, func(tx *ent.Client) error {
		var err error
		out, err = RemoveDepIn(ctx, tx, id, blockerID)
		return err
	})
	return out, err
}

// RemoveDepIn is RemoveDep inside a caller's transaction.
func RemoveDepIn(ctx context.Context, tx *ent.Client, id, blockerID int) (models.Issue, error) {
	e, err := row(ctx, tx, id)
	if err != nil {
		return models.Issue{}, err
	}
	if err := refuse(e, models.KeyIssueRemoveDep); err != nil {
		return models.Issue{}, err
	}
	deleted, err := tx.Ref.Delete().
		Where(entref.BlockedIDEQ(id), entref.BlockerIDEQ(blockerID)).
		Exec(ctx)
	if err != nil {
		return models.Issue{}, fmt.Errorf("removing dependency %d -> %d: %w", id, blockerID, err)
	}
	if deleted == 0 {
		return models.Issue{}, errs.Conflictf("issue %d does not wait on issue %d", id, blockerID)
	}
	return Load(ctx, tx, id)
}

// Comment appends a comment and returns the issue it landed on.
func Comment(ctx context.Context, cl *ent.Client, id int, body string) (models.Issue, error) {
	var out models.Issue
	err := db.WithTx(ctx, cl, func(tx *ent.Client) error {
		var err error
		out, err = CommentIn(ctx, tx, id, body)
		return err
	})
	return out, err
}

// CommentIn is Comment inside a caller's transaction.
func CommentIn(ctx context.Context, tx *ent.Client, id int, body string) (models.Issue, error) {
	e, err := row(ctx, tx, id)
	if err != nil {
		return models.Issue{}, err
	}
	if err := refuse(e, models.KeyIssueComment); err != nil {
		return models.Issue{}, err
	}
	if _, err := comments.AddIn(ctx, tx, id, body); err != nil {
		return models.Issue{}, err
	}
	return Load(ctx, tx, id)
}

// write is the plain/…In pairing for the writes that take only an id.
func write(
	ctx context.Context,
	cl *ent.Client,
	id int,
	in func(context.Context, *ent.Client, int) (models.Issue, error),
) (models.Issue, error) {
	var out models.Issue
	err := db.WithTx(ctx, cl, func(tx *ent.Client) error {
		var err error
		out, err = in(ctx, tx, id)
		return err
	})
	return out, err
}

// refuse turns the menu's verdict on key into the error a write returns. The
// same rule decides both, and Action.Reason is the message either way.
func refuse(e *ent.Issue, key models.IssueKey) error {
	for _, a := range Actions(e) {
		if a.Key != key {
			continue
		}
		if a.Runnable() {
			return nil
		}
		return errs.Conflictf("issue %d: %s", e.ID, a.Reason)
	}
	return errs.Conflictf("issue %d cannot %s", e.ID, key)
}

// offers reports whether the menu for this row carries key without a refusal.
func offers(e *ent.Issue, key models.IssueKey) bool {
	return refuse(e, key) == nil
}

// refuseArchived keeps new work out of a project that was archived to get it
// out of the way. Restoring the project is what makes capture work again.
func refuseArchived(ctx context.Context, tx *ent.Client, projectID int) error {
	archived, err := tx.Project.Query().
		Where(entproject.IDEQ(projectID), entproject.StatusEQ(entproject.StatusArchived)).
		Exist(ctx)
	if err != nil {
		return fmt.Errorf("checking project %d: %w", projectID, err)
	}
	if archived {
		return errs.Conflictf("project %d is archived", projectID)
	}
	return nil
}

// validateChild checks the params of a sub-issue, whose project comes from its
// parent rather than from the caller.
func validateChild(p models.IssueAddParams) error {
	if strings.TrimSpace(p.Title) == "" {
		return errs.Invalidf("title is required")
	}
	switch p.Priority {
	case "", models.PriorityNormal, models.PriorityHi:
		return nil
	default:
		return errs.Invalidf("unknown priority %q: want normal or hi", p.Priority)
	}
}

// row reads the ent row a write is about to change, with the edges the menu
// rules need. Going through withMenuEdges is what lets refuse() consult the
// same Actions a frontend rendered.
func row(ctx context.Context, tx *ent.Client, id int) (*ent.Issue, error) {
	e, err := withMenuEdges(tx.Issue.Query().Where(entissue.IDEQ(id))).Only(ctx)
	if err != nil {
		if ent.IsNotFound(err) {
			return nil, errs.NotFoundf("issue %d", id)
		}
		return nil, fmt.Errorf("loading issue %d: %w", id, err)
	}
	return e, nil
}
