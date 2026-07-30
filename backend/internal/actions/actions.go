// Package actions is the write path over an object: what its menu offers, why
// an entry is refused, and what running it does.
//
// One Group per object type. A Group holds its entries in menu order and is the
// only place a rule is checked, so the menu a frontend renders and the write it
// then calls cannot disagree — they are the same Spec consulted twice.
//
// An action is defined with Define, which keeps its payload type. Entry erases
// that type so a Group can hold actions with different payloads side by side;
// Invoke is the typed way in and Dispatch is the key-driven one. Both go
// through Check first.
package actions

import (
	"context"

	"github.com/Plabrum/tt/backend/contract"
	"github.com/Plabrum/tt/backend/errs"
	"github.com/Plabrum/tt/backend/internal/ent"
)

// None is the payload of an action that takes no arguments.
type None struct{}

// Spec is everything about an action except what it does.
//
// Applies decides whether the action is on the menu at all and Refuse whether
// it can run, which is the contract's absent/refused distinction. A nil Applies
// always applies and a nil Refuse never refuses.
type Spec[K ~string, O any] struct {
	Key     K
	Label   string
	Applies func(O) bool
	Refuse  func(O) string
}

// reason is why this action cannot run against o, "" when it can.
func (s Spec[K, O]) reason(o O) string {
	if s.Refuse == nil {
		return ""
	}
	return s.Refuse(o)
}

// applies reports whether the action belongs on o's menu at all.
func (s Spec[K, O]) applies(o O) bool {
	return s.Applies == nil || s.Applies(o)
}

// Action is one action: its Spec and the write it performs.
//
// Payload and result are type parameters rather than any, so a caller that
// names the action it wants has both checked at compile time.
type Action[K ~string, O, P, R any] struct {
	spec Spec[K, O]
	run  func(ctx context.Context, tx *ent.Client, o O, p P) (R, error)
}

// Define builds an action from its spec and its write.
//
// The write is handed a loaded row inside the caller's transaction, and only
// once the spec has cleared it, so a write restates no rule.
func Define[K ~string, O, P, R any](
	spec Spec[K, O],
	run func(ctx context.Context, tx *ent.Client, o O, p P) (R, error),
) *Action[K, O, P, R] {
	return &Action[K, O, P, R]{spec: spec, run: run}
}

// Entry is this action with its payload type erased, which is what lets a Group
// hold actions that take different payloads.
//
// The assertion inside can only fail on the Dispatch path, where a caller
// supplies a payload against a key chosen at runtime. Invoke cannot reach it:
// there the payload's type comes from the action itself.
func (a *Action[K, O, P, R]) Entry() Entry[K, O, R] {
	return Entry[K, O, R]{
		spec: a.spec,
		run: func(ctx context.Context, tx *ent.Client, o O, payload any) (R, error) {
			p, ok := payload.(P)
			if !ok {
				var zero R
				return zero, errs.Invalidf("action %q got the wrong payload type", a.spec.Key)
			}
			return a.run(ctx, tx, o, p)
		},
	}
}

// Entry is a payload-erased action inside a Group.
type Entry[K ~string, O, R any] struct {
	spec Spec[K, O]
	run  func(ctx context.Context, tx *ent.Client, o O, payload any) (R, error)
}

// Group is one object type's whole menu, in the order a frontend shows it.
//
// subject names the object in an error — "issue 4" — so a refusal reads the
// same whichever way the action was reached.
type Group[K ~string, O, R any] struct {
	subject func(O) string
	entries []Entry[K, O, R]
}

// NewGroup collects entries into a menu, in the order they are given.
func NewGroup[K ~string, O, R any](subject func(O) string, entries ...Entry[K, O, R]) *Group[K, O, R] {
	return &Group[K, O, R]{subject: subject, entries: entries}
}

// Menu is what o offers, as the contract types a frontend renders. It is never
// nil: an object with no actions has an empty menu, which prints as [].
func (g *Group[K, O, R]) Menu(o O) []contract.Action[K] {
	out := make([]contract.Action[K], 0, len(g.entries))
	for _, e := range g.entries {
		if !e.spec.applies(o) {
			continue
		}
		out = append(out, contract.Action[K]{
			Key:    e.spec.Key,
			Label:  e.spec.Label,
			Reason: e.spec.reason(o),
		})
	}
	return out
}

// Check is the menu's verdict on key as an error: nil when the action can run,
// and errs.ErrConflict carrying the same Reason the menu carries when it
// cannot. An action that does not apply to o at all is refused the same way.
//
// This is the enforcement point. Invoke and Dispatch both come through here,
// against the live row, so the menu stays a snapshot and this decides.
func (g *Group[K, O, R]) Check(o O, key K) error {
	for _, e := range g.entries {
		if e.spec.Key != key {
			continue
		}
		if !e.spec.applies(o) {
			break
		}
		if reason := e.spec.reason(o); reason != "" {
			return errs.Conflictf("%s: %s", g.subject(o), reason)
		}
		return nil
	}
	return errs.Conflictf("%s cannot %s", g.subject(o), key)
}

// Allows reports whether the menu for o carries key without a refusal.
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
		if e.spec.Key != key {
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
// type parameter the payload needs. The action must be one of g's entries: g is
// what enforces, and Check refuses a key the group does not carry.
func Invoke[K ~string, O, P, R any](
	ctx context.Context,
	g *Group[K, O, R],
	tx *ent.Client,
	o O,
	a *Action[K, O, P, R],
	p P,
) (R, error) {
	if err := g.Check(o, a.spec.Key); err != nil {
		var zero R
		return zero, err
	}
	return a.run(ctx, tx, o, p)
}
