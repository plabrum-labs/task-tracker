// Package statemachine is the transition topology of a status column.
//
// A machine is a declaration: which states exist, which moves each one offers,
// and what has to happen on the way in or out. It owns no queries and no
// transaction — a caller hands it a loaded row and the transaction to write
// through.
//
// One machine per object with a status. The topology lives here rather than in
// a switch inside a rule and a second switch inside a write, which is the shape
// that lets the two disagree.
package statemachine

import (
	"context"

	"github.com/Plabrum/tt/backend/errs"
	"github.com/Plabrum/tt/backend/internal/ent"
)

// State is one node: the states reachable from it, and the side effects of
// arriving at it or leaving it.
//
// OnEnter and OnExit are for what the move itself implies — a closed_at stamp,
// not a rule. A rule that decides whether the move is allowed is the action's,
// and lives in its Spec.
type State[S comparable, O any] struct {
	// To are the states this one can move to. A state with none is terminal.
	To []S
	// OnEnter runs after the new status is written, OnExit before the old one
	// is replaced. Either may be nil.
	OnEnter func(ctx context.Context, tx *ent.Client, o O, from S) error
	OnExit  func(ctx context.Context, tx *ent.Client, o O, to S) error
}

// Machine is the topology of one object's status, plus the two functions that
// tie it to a row: how to read the current status off one, and how to write a
// new one.
type Machine[S comparable, O any] struct {
	states map[S]State[S, O]
	read   func(O) S
	write  func(ctx context.Context, tx *ent.Client, o O, to S) error
}

// New builds a machine over states, reading the current status with read and
// persisting a new one with write.
func New[S comparable, O any](
	read func(O) S,
	write func(ctx context.Context, tx *ent.Client, o O, to S) error,
	states map[S]State[S, O],
) *Machine[S, O] {
	return &Machine[S, O]{states: states, read: read, write: write}
}

// Can reports whether o can move to the given state as things stand.
//
// This is the whole of a transition's topology check, so a rule asks it and the
// write asks it again rather than each restating the edges.
func (m *Machine[S, O]) Can(o O, to S) bool {
	current, ok := m.states[m.read(o)]
	if !ok {
		return false
	}
	for _, edge := range current.To {
		if edge == to {
			return true
		}
	}
	return false
}

// Transition moves o to the given state: OnExit, the write, then OnEnter.
//
// A move the topology does not offer wraps errs.ErrConflict. The order matters
// to the hooks — OnExit sees the row as it was, OnEnter sees it as it is.
func (m *Machine[S, O]) Transition(ctx context.Context, tx *ent.Client, o O, to S) error {
	from := m.read(o)
	current, ok := m.states[from]
	if !ok {
		return errs.Conflictf("stored status %v is not one this binary knows", from)
	}
	if !m.Can(o, to) {
		return errs.Conflictf("cannot move from %v to %v", from, to)
	}

	if current.OnExit != nil {
		if err := current.OnExit(ctx, tx, o, to); err != nil {
			return err
		}
	}
	if err := m.write(ctx, tx, o, to); err != nil {
		return err
	}
	if target := m.states[to]; target.OnEnter != nil {
		return target.OnEnter(ctx, tx, o, from)
	}
	return nil
}
