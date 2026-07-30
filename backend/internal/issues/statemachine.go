package issues

import (
	"context"
	"fmt"
	"time"

	"github.com/Plabrum/tt/backend/internal/ent"
	entissue "github.com/Plabrum/tt/backend/internal/ent/issue"
	"github.com/Plabrum/tt/backend/internal/statemachine"
)

// IssueStateMachine is the issue lifecycle:
//
//	todo -[start]-> doing -[close]-> done -[reopen]-> todo
//
// with a close straight from todo, for work finished without ever being picked
// up. closed_at is the state's business rather than each write's: arriving at
// done stamps it and arriving anywhere else clears it, so no write can move an
// issue and leave the stamp saying otherwise.
var IssueStateMachine = statemachine.New(
	func(e *ent.Issue) entissue.Status { return e.Status },
	func(ctx context.Context, tx *ent.Client, e *ent.Issue, to entissue.Status) error {
		if err := tx.Issue.UpdateOne(e).SetStatus(to).Exec(ctx); err != nil {
			return fmt.Errorf("setting status of issue %d to %s: %w", e.ID, to, err)
		}
		return nil
	},
	map[entissue.Status]statemachine.State[entissue.Status, *ent.Issue]{
		entissue.StatusTodo: {
			To:      []entissue.Status{entissue.StatusDoing, entissue.StatusDone},
			OnEnter: clearClosedAt,
		},
		entissue.StatusDoing: {
			To:      []entissue.Status{entissue.StatusDone},
			OnEnter: clearClosedAt,
		},
		entissue.StatusDone: {
			To:      []entissue.Status{entissue.StatusTodo},
			OnEnter: stampClosedAt,
		},
	},
)

// stampClosedAt records when an issue reached done.
func stampClosedAt(ctx context.Context, tx *ent.Client, e *ent.Issue, _ entissue.Status) error {
	if err := tx.Issue.UpdateOne(e).SetClosedAt(time.Now()).Exec(ctx); err != nil {
		return fmt.Errorf("stamping issue %d closed: %w", e.ID, err)
	}
	return nil
}

// clearClosedAt drops the stamp off an issue that is open again.
func clearClosedAt(ctx context.Context, tx *ent.Client, e *ent.Issue, _ entissue.Status) error {
	if err := tx.Issue.UpdateOne(e).ClearClosedAt().Exec(ctx); err != nil {
		return fmt.Errorf("clearing the closed stamp on issue %d: %w", e.ID, err)
	}
	return nil
}
