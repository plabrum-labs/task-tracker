package actions_test

import (
	"context"
	"errors"
	"fmt"
	"testing"

	"github.com/Plabrum/tt/backend/contract"
	"github.com/Plabrum/tt/backend/errs"
	"github.com/Plabrum/tt/backend/internal/actions"
	"github.com/Plabrum/tt/backend/internal/ent"
)

// The platform is generic over the object, so the tests use a struct of their
// own rather than an ent row: what is under test is the Spec/Group machinery,
// not any domain's rules. No write here touches the client, so a nil one is
// what a transaction argument gets.

type widget struct {
	id     int
	locked bool
	empty  bool
}

type key string

const (
	keyPoke   key = "poke"
	keyDrain  key = "drain"
	keyAbsent key = "absent"
)

func subject(w *widget) string { return fmt.Sprintf("widget %d", w.id) }

const reasonLocked = "locked"

// poke takes a payload and refuses a locked widget; drain takes none and
// withholds itself from an empty one. Both record that they ran.

type poke struct {
	actions.Default[*widget]
	ran *[]key
}

func (poke) Key() key      { return keyPoke }
func (poke) Label() string { return "poke" }

func (poke) IsDisabled(w *widget) string {
	if w.locked {
		return reasonLocked
	}
	return ""
}

func (p poke) Execute(_ context.Context, _ *ent.Client, _ *widget, s string) (string, error) {
	*p.ran = append(*p.ran, keyPoke)
	return s, nil
}

type drain struct {
	actions.Default[*widget]
	ran *[]key
}

func (drain) Key() key      { return keyDrain }
func (drain) Label() string { return "drain" }

func (drain) IsAvailable(w *widget) bool { return !w.empty }

func (d drain) Execute(_ context.Context, _ *ent.Client, _ *widget, _ actions.None) (string, error) {
	*d.ran = append(*d.ran, keyDrain)
	return "drained", nil
}

// group builds the menu under test, recording into ran which writes fired.
func group(ran *[]key) *actions.Group[key, *widget, string] {
	return actions.NewGroup(subject, pokeAction(ran).Entry(), actions.Bind(drain{ran: ran}).Entry())
}

// pokeAction is the poke action on its own, for the typed Invoke path.
func pokeAction(ran *[]key) *actions.Bound[key, *widget, string, string] {
	return actions.Bind(poke{ran: ran})
}

func TestMenu(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name string
		w    *widget
		want []contract.Action[key]
	}{
		{
			name: "everything applies and nothing refuses",
			w:    &widget{id: 1},
			want: []contract.Action[key]{
				{Key: keyPoke, Label: "poke"},
				{Key: keyDrain, Label: "drain"},
			},
		},
		{
			name: "a refused entry stays on the menu, carrying its reason",
			w:    &widget{id: 2, locked: true},
			want: []contract.Action[key]{
				{Key: keyPoke, Label: "poke", Reason: reasonLocked},
				{Key: keyDrain, Label: "drain"},
			},
		},
		{
			name: "an entry that does not apply is absent, not refused",
			w:    &widget{id: 3, empty: true},
			want: []contract.Action[key]{
				{Key: keyPoke, Label: "poke"},
			},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			var ran []key
			got := group(&ran).Menu(tt.w)
			if len(got) != len(tt.want) {
				t.Fatalf("menu = %+v, want %+v", got, tt.want)
			}
			for i := range got {
				if got[i] != tt.want[i] {
					t.Errorf("menu[%d] = %+v, want %+v", i, got[i], tt.want[i])
				}
			}
		})
	}
}

func TestCheck(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name    string
		w       *widget
		key     key
		wantErr error
	}{
		{name: "runnable", w: &widget{id: 1}, key: keyPoke},
		{name: "refused", w: &widget{id: 1, locked: true}, key: keyPoke, wantErr: errs.ErrConflict},
		{name: "does not apply", w: &widget{id: 1, empty: true}, key: keyDrain, wantErr: errs.ErrConflict},
		{name: "not in the group at all", w: &widget{id: 1}, key: keyAbsent, wantErr: errs.ErrConflict},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			var ran []key
			err := group(&ran).Check(tt.w, tt.key)
			if tt.wantErr == nil {
				if err != nil {
					t.Fatalf("Check = %v, want nil", err)
				}
				return
			}
			if !errors.Is(err, tt.wantErr) {
				t.Fatalf("Check = %v, want %v", err, tt.wantErr)
			}
		})
	}
}

// A refusal carries the reason the menu carries, so a frontend never sees two
// wordings for one rule.
func TestCheckCarriesTheMenuReason(t *testing.T) {
	t.Parallel()

	var ran []key
	err := group(&ran).Check(&widget{id: 4, locked: true}, keyPoke)
	if err == nil {
		t.Fatal("Check on a locked widget = nil, want a refusal")
	}
	if want := "widget 4: " + reasonLocked; err.Error() != want+": conflict" {
		t.Errorf("Check = %q, want it to start %q", err.Error(), want)
	}
}

func TestInvoke(t *testing.T) {
	t.Parallel()

	t.Run("runs the write when the spec clears it", func(t *testing.T) {
		t.Parallel()
		var ran []key
		g := group(&ran)
		poke := pokeAction(&ran)
		got, err := actions.Invoke(t.Context(), g, nil, &widget{id: 1}, poke, "hello")
		if err != nil {
			t.Fatalf("Invoke: %v", err)
		}
		if got != "hello" {
			t.Errorf("result = %q, want %q", got, "hello")
		}
		if len(ran) != 1 || ran[0] != keyPoke {
			t.Errorf("writes ran = %v, want just poke", ran)
		}
	})

	t.Run("a refusal keeps the write from running", func(t *testing.T) {
		t.Parallel()
		var ran []key
		g := group(&ran)
		poke := pokeAction(&ran)
		if _, err := actions.Invoke(t.Context(), g, nil, &widget{id: 1, locked: true}, poke, "hello"); !errors.Is(err, errs.ErrConflict) {
			t.Fatalf("Invoke on a locked widget = %v, want ErrConflict", err)
		}
		if len(ran) != 0 {
			t.Errorf("writes ran = %v, want none", ran)
		}
	})
}

func TestDispatch(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name    string
		w       *widget
		key     key
		payload any
		want    string
		wantErr error
		wantRan int
	}{
		{name: "the declared payload runs the write", w: &widget{id: 1}, key: keyPoke, payload: "hi", want: "hi", wantRan: 1},
		{name: "no payload runs a None write", w: &widget{id: 1}, key: keyDrain, payload: actions.None{}, want: "drained", wantRan: 1},
		{name: "another payload type is invalid", w: &widget{id: 1}, key: keyPoke, payload: 42, wantErr: errs.ErrInvalid},
		{name: "a refusal keeps the write from running", w: &widget{id: 1, locked: true}, key: keyPoke, payload: "hi", wantErr: errs.ErrConflict},
		{name: "an unknown key is a conflict", w: &widget{id: 1}, key: keyAbsent, payload: "hi", wantErr: errs.ErrConflict},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			var ran []key
			got, err := group(&ran).Dispatch(t.Context(), nil, tt.w, tt.key, tt.payload)
			if tt.wantErr != nil {
				if !errors.Is(err, tt.wantErr) {
					t.Fatalf("Dispatch = %v, want %v", err, tt.wantErr)
				}
			} else if err != nil {
				t.Fatalf("Dispatch: %v", err)
			}
			if got != tt.want {
				t.Errorf("result = %q, want %q", got, tt.want)
			}
			if len(ran) != tt.wantRan {
				t.Errorf("writes ran = %v, want %d of them", ran, tt.wantRan)
			}
		})
	}
}
