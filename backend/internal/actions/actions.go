// Package actions is the write path over an object: what its menu offers, why
// an entry is refused, and what running it does.
//
// An action is a type. Key and Label say what it is, IsAvailable and IsDisabled
// are its rules, and Execute is its write — so everything about one action is
// one block, and the compiler checks the shape rather than a convention doing
// it. Embed Default to take the two rules' defaults.
//
// One Group per object type. A Group holds its actions in menu order and is the
// only place a rule is checked, so the menu a frontend renders and the write it
// then calls cannot disagree — they are the same action asked twice.
//
// Bind keeps an action's payload type. Entry erases it so a Group can hold
// actions with different payloads side by side; Invoke is the typed way in and
// Dispatch is the key-driven one. Both go through Check first.
package actions

import (
	"context"

	"github.com/Plabrum/tt/backend/contract"
	"github.com/Plabrum/tt/backend/errs"
	"github.com/Plabrum/tt/backend/internal/ent"
)

// None is the payload of an action that takes no arguments.
type None struct{}

// Action is one action on an object: its identity, its rules and its write.
//
// IsAvailable decides whether the action is on the menu at all and IsDisabled
// whether it can run, which is the contract's absent/refused distinction.
// Execute is handed a loaded row inside the caller's transaction, and only once
// both rules have cleared it, so a write restates no rule.
type Action[K ~string, O, P, R any] interface {
	Key() K
	Label() string
	IsAvailable(O) bool
	IsDisabled(O) string
	Execute(ctx context.Context, tx *ent.Client, o O, p P) (R, error)
}

// Default is the two rules an action gets when it declares neither: it always
// applies and it never refuses. Embed it and override either one.
//
// Overriding is by method name and nothing checks the spelling, so an
// IsAvailible on an action that embeds this compiles and leaves the real rule
// unreachable. A rule that quietly stopped taking effect is the symptom.
type Default[O any] struct{}

// IsAvailable reports that the action applies to every object.
func (Default[O]) IsAvailable(O) bool { return true }

// IsDisabled reports that the action is never refused.
func (Default[O]) IsDisabled(O) string { return "" }

// Bound is an action with its payload type kept, so a caller that names the
// action it wants has payload and result checked at compile time.
type Bound[K ~string, O, P, R any] struct {
	action Action[K, O, P, R]
}

// Bind registers an action. Every type parameter is inferred from the action's
// own method set.
func Bind[K ~string, O, P, R any](a Action[K, O, P, R]) *Bound[K, O, P, R] {
	return &Bound[K, O, P, R]{action: a}
}

// Entry is this action with its payload type erased, which is what lets a Group
// hold actions that take different payloads.
//
// The assertion inside can only fail on the Dispatch path, where a caller
// supplies a payload against a key chosen at runtime. Invoke cannot reach it:
// there the payload's type comes from the action itself.
func (b *Bound[K, O, P, R]) Entry() Entry[K, O, R] {
	a := b.action
	return Entry[K, O, R]{
		key:       a.Key(),
		label:     a.Label(),
		available: a.IsAvailable,
		disabled:  a.IsDisabled,
		run: func(ctx context.Context, tx *ent.Client, o O, payload any) (R, error) {
			p, ok := payload.(P)
			if !ok {
				var zero R
				return zero, errs.Invalidf("action %q got the wrong payload type", a.Key())
			}
			return a.Execute(ctx, tx, o, p)
		},
	}
}

// Entry is a payload-erased action inside a Group.
type Entry[K ~string, O, R any] struct {
	key       K
	label     string
	available func(O) bool
	disabled  func(O) string
	run       func(ctx context.Context, tx *ent.Client, o O, payload any) (R, error)
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
		if !e.available(o) {
			continue
		}
		out = append(out, contract.Action[K]{
			Key:    e.key,
			Label:  e.label,
			Reason: e.disabled(o),
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
		if e.key != key {
			continue
		}
		if !e.available(o) {
			break
		}
		if reason := e.disabled(o); reason != "" {
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
// type parameter the payload needs. The action must be one of g's entries: g is
// what enforces, and Check refuses a key the group does not carry.
func Invoke[K ~string, O, P, R any](
	ctx context.Context,
	g *Group[K, O, R],
	tx *ent.Client,
	o O,
	b *Bound[K, O, P, R],
	p P,
) (R, error) {
	if err := g.Check(o, b.action.Key()); err != nil {
		var zero R
		return zero, err
	}
	return b.action.Execute(ctx, tx, o, p)
}
