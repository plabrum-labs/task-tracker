// Package actions is the write path over an object: what it offers, why an
// entry is refused, and what running one does.
//
// An action is a struct literal — what it is, its two rules, and its write, all
// in one place. Register puts it in its object's Group as it is declared, so
// there is no list to keep in step, and Priority rather than declaration order
// decides where it appears.
//
// One Group per object type, and it is the only place a rule is checked, so
// what a frontend renders and the write it then calls cannot disagree — they
// are the same action asked twice. Register keeps an action's payload type;
// Entry erases it so a Group can hold actions with different payloads side by
// side. Invoke is the typed way in and Dispatch is the key-driven one, and both
// go through Check first.
package actions

import (
	"context"

	"github.com/Plabrum/tt/backend/contract"
	"github.com/Plabrum/tt/backend/errs"
	"github.com/Plabrum/tt/backend/internal/ent"
)

// None is the payload of an action that takes no arguments.
type None struct{}

// Action is one action: what it is, its two rules, and its write.
//
// IsAvailable decides whether the object offers the action at all and
// IsDisabled whether it can run, which is the contract's absent/refused
// distinction. Both may be nil: always applies, never refuses. Execute is
// handed a loaded row inside the caller's transaction and only once both rules
// have cleared it, so a write restates no rule.
type Action[K ~string, O, P, R any] struct {
	Key         K
	Label       string
	Priority    int
	IsAvailable func(O) bool
	IsDisabled  func(O) string
	Execute     func(ctx context.Context, tx *ent.Client, o O, p P) (R, error)
}

// Bound is a registered action with its payload type kept, so a caller that
// names the action it wants has payload and result checked at compile time.
//
// Its fields are unreachable from outside this package, which is the point of
// registering through a function: nothing can swap a rule or a write out from
// under a Group that has already published it.
type Bound[K ~string, O, P, R any] struct {
	action Action[K, O, P, R]
}

// Entry is a payload-erased action inside a Group.
type Entry[K ~string, O, R any] struct {
	key         K
	label       string
	priority    int
	isAvailable func(O) bool
	isDisabled  func(O) string
	run         func(ctx context.Context, tx *ent.Client, o O, payload any) (R, error)
}

// Group is one object type's actions.
//
// subject names the object in an error — "issue 4" — so a refusal reads the
// same whichever way the action was reached.
type Group[K ~string, O, R any] struct {
	subject func(O) string
	entries []Entry[K, O, R]
}

// NewGroup returns an empty group. Register is what fills it.
func NewGroup[K ~string, O, R any](subject func(O) string) *Group[K, O, R] {
	return &Group[K, O, R]{subject: subject}
}

// Register adds an action to a group and returns its handle. Every type
// parameter is inferred from the action.
//
// Entries are kept in Priority order as they arrive, so a caller declaring
// actions across several files gets the same order however the compiler
// happens to sequence those files.
func Register[K ~string, O, P, R any](g *Group[K, O, R], a Action[K, O, P, R]) *Bound[K, O, P, R] {
	entry := Entry[K, O, R]{
		key:         a.Key,
		label:       a.Label,
		priority:    a.Priority,
		isAvailable: a.IsAvailable,
		isDisabled:  a.IsDisabled,
		run: func(ctx context.Context, tx *ent.Client, o O, payload any) (R, error) {
			p, ok := payload.(P)
			if !ok {
				var zero R
				return zero, errs.Invalidf("action %q got the wrong payload type", a.Key)
			}
			return a.Execute(ctx, tx, o, p)
		},
	}

	at := len(g.entries)
	for i, e := range g.entries {
		if e.priority > a.Priority {
			at = i
			break
		}
	}
	g.entries = append(g.entries, Entry[K, O, R]{})
	copy(g.entries[at+1:], g.entries[at:])
	g.entries[at] = entry

	return &Bound[K, O, P, R]{action: a}
}

// Available is what o offers, as the contract types a frontend renders. It is
// never nil: an object offering nothing gives an empty slice, which prints
// as [].
func (g *Group[K, O, R]) Available(o O) []contract.Action[K] {
	out := make([]contract.Action[K], 0, len(g.entries))
	for _, e := range g.entries {
		if !available(e, o) {
			continue
		}
		out = append(out, contract.Action[K]{
			Key:    e.key,
			Label:  e.label,
			Reason: disabled(e, o),
		})
	}
	return out
}

// Check is the group's verdict on key as an error: nil when the action can run,
// and errs.ErrConflict carrying the same Reason it would have shown when it
// cannot. An action the object does not offer at all is refused the same way.
//
// This is the enforcement point. Invoke and Dispatch both come through here,
// against the live row, so what a frontend rendered stays a snapshot and this
// decides.
func (g *Group[K, O, R]) Check(o O, key K) error {
	for _, e := range g.entries {
		if e.key != key {
			continue
		}
		if !available(e, o) {
			break
		}
		if reason := disabled(e, o); reason != "" {
			return errs.Conflictf("%s: %s", g.subject(o), reason)
		}
		return nil
	}
	return errs.Conflictf("%s cannot %s", g.subject(o), key)
}

// Allows reports whether o offers key without a refusal.
func (g *Group[K, O, R]) Allows(o O, key K) bool {
	return g.Check(o, key) == nil
}

// Dispatch runs the action key names against o, with the payload that action
// declared, as an any.
func (g *Group[K, O, R]) Dispatch(
	ctx context.Context,
	tx *ent.Client,
	o O,
	key K,
	payload any,
) (R, error) {
	var zero R
	for _, e := range g.entries {
		if e.key != key {
			continue
		}
		if err := g.Check(o, key); err != nil {
			return zero, err
		}
		return e.run(ctx, tx, o, payload)
	}
	return zero, errs.Conflictf("%s cannot %s", g.subject(o), key)
}

// Invoke runs a known action against o, its payload checked at compile time.
//
// It is a function rather than a method because a method cannot take the extra
// type parameter the payload needs. The action must be one of g's: g is what
// enforces, and Check refuses a key the group does not carry.
func Invoke[K ~string, O, P, R any](
	ctx context.Context,
	g *Group[K, O, R],
	tx *ent.Client,
	o O,
	b *Bound[K, O, P, R],
	p P,
) (R, error) {
	if err := g.Check(o, b.action.Key); err != nil {
		var zero R
		return zero, err
	}
	return b.action.Execute(ctx, tx, o, p)
}

// A nil rule is the permissive one: an action that declares no IsAvailable
// applies to every object, and one that declares no IsDisabled never refuses.

func available[K ~string, O, R any](e Entry[K, O, R], o O) bool {
	return e.isAvailable == nil || e.isAvailable(o)
}

func disabled[K ~string, O, R any](e Entry[K, O, R], o O) string {
	if e.isDisabled == nil {
		return ""
	}
	return e.isDisabled(o)
}
