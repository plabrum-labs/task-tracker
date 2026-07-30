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

// The platform is generic over the object, so these use a struct of their own
// rather than an ent row: the Group machinery is what is under test, not any
// domain's rules. No write here touches the client, so a nil one is what a
// transaction argument gets.

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

const reasonLocked = "locked"

func subject(w *widget) string { return fmt.Sprintf("widget %d", w.id) }

func lockedReason(w *widget) string {
	if w.locked {
		return reasonLocked
	}
	return ""
}

func notEmpty(w *widget) bool { return !w.empty }

// group builds the group under test, recording into ran which writes fired.
//
// poke takes a payload and refuses a locked widget; drain takes none and an
// empty widget does not offer it. Drain registers first but sorts second, so a
// group that kept arrival order rather than Priority would fail the assertions
// below.
func group(ran *[]key) (*actions.Group[key, *widget, string], *actions.Bound[key, *widget, string, string]) {
	g := actions.NewGroup[key, *widget, string](subject)

	actions.Register(g, actions.Action[key, *widget, actions.None, string]{
		Key:         keyDrain,
		Label:       "drain",
		Priority:    20,
		IsAvailable: notEmpty,
		Execute: func(_ context.Context, _ *ent.Client, _ *widget, _ actions.None) (string, error) {
			*ran = append(*ran, keyDrain)
			return "drained", nil
		},
	})

	poke := actions.Register(g, actions.Action[key, *widget, string, string]{
		Key:        keyPoke,
		Label:      "poke",
		Priority:   10,
		IsDisabled: lockedReason,
		Execute: func(_ context.Context, _ *ent.Client, _ *widget, p string) (string, error) {
			*ran = append(*ran, keyPoke)
			return p, nil
		},
	})

	return g, poke
}

func TestAvailable(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name string
		w    *widget
		want []contract.Action[key]
	}{
		{
			name: "everything applies and nothing refuses, in Priority order",
			w:    &widget{id: 1},
			want: []contract.Action[key]{
				{Key: keyPoke, Label: "poke"},
				{Key: keyDrain, Label: "drain"},
			},
		},
		{
			name: "a refused action is still offered, carrying its reason",
			w:    &widget{id: 2, locked: true},
			want: []contract.Action[key]{
				{Key: keyPoke, Label: "poke", Reason: reasonLocked},
				{Key: keyDrain, Label: "drain"},
			},
		},
		{
			name: "an action that does not apply is absent, not refused",
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
			g, _ := group(&ran)
			got := g.Available(tt.w)
			if len(got) != len(tt.want) {
				t.Fatalf("Available = %+v, want %+v", got, tt.want)
			}
			for i := range got {
				if got[i] != tt.want[i] {
					t.Errorf("Available[%d] = %+v, want %+v", i, got[i], tt.want[i])
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
			g, _ := group(&ran)
			err := g.Check(tt.w, tt.key)
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

// A refusal carries the reason the object would have shown, so a frontend never
// sees two wordings for one rule.
func TestCheckCarriesTheSameReason(t *testing.T) {
	t.Parallel()

	var ran []key
	g, _ := group(&ran)
	w := &widget{id: 4, locked: true}

	offered := g.Available(w)
	if len(offered) == 0 || offered[0].Reason != reasonLocked {
		t.Fatalf("Available = %+v, want poke carrying %q", offered, reasonLocked)
	}
	err := g.Check(w, keyPoke)
	if err == nil {
		t.Fatal("Check on a locked widget = nil, want a refusal")
	}
	if want := "widget 4: " + reasonLocked + ": conflict"; err.Error() != want {
		t.Errorf("Check = %q, want %q", err.Error(), want)
	}
}

func TestInvoke(t *testing.T) {
	t.Parallel()

	t.Run("runs the write when the rules clear it", func(t *testing.T) {
		t.Parallel()
		var ran []key
		g, poke := group(&ran)
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
		g, poke := group(&ran)
		_, err := actions.Invoke(t.Context(), g, nil, &widget{id: 1, locked: true}, poke, "hello")
		if !errors.Is(err, errs.ErrConflict) {
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
			g, _ := group(&ran)
			got, err := g.Dispatch(t.Context(), nil, tt.w, tt.key, tt.payload)
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
