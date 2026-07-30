package statemachine_test

import (
	"context"
	"errors"
	"testing"

	"github.com/Plabrum/tt/backend/errs"
	"github.com/Plabrum/tt/backend/internal/ent"
	"github.com/Plabrum/tt/backend/internal/statemachine"
)

// A machine is generic over its object, so these use a struct of their own: the
// topology and the hook order are what is under test, not any domain's states.
// Nothing here touches the client, so a nil one is what a transaction gets.

type state string

const (
	open    state = "open"
	shut    state = "shut"
	sealed  state = "sealed"
	unknown state = "unknown"
)

type door struct {
	state state
	log   []string
}

// machine is open ⇄ shut, and shut -> sealed one way, with every hook
// recording itself so a test can assert the order they run in.
func machine() *statemachine.Machine[state, *door] {
	note := func(what string) func(context.Context, *ent.Client, *door, state) error {
		return func(_ context.Context, _ *ent.Client, d *door, _ state) error {
			d.log = append(d.log, what)
			return nil
		}
	}
	return statemachine.New(
		func(d *door) state { return d.state },
		func(_ context.Context, _ *ent.Client, d *door, to state) error {
			d.log = append(d.log, "write")
			d.state = to
			return nil
		},
		map[state]statemachine.State[state, *door]{
			open: {
				To:     []state{shut},
				OnExit: note("exit open"),
			},
			shut: {
				To:      []state{open, sealed},
				OnEnter: note("enter shut"),
			},
			sealed: {},
		},
	)
}

func TestCan(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name string
		from state
		to   state
		want bool
	}{
		{name: "a declared edge", from: open, to: shut, want: true},
		{name: "one of two declared edges", from: shut, to: sealed, want: true},
		{name: "an edge that is only declared the other way", from: open, to: sealed, want: false},
		{name: "out of a terminal state", from: sealed, to: open, want: false},
		{name: "out of a state the machine does not know", from: unknown, to: open, want: false},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			if got := machine().Can(&door{state: tt.from}, tt.to); got != tt.want {
				t.Errorf("Can(%s -> %s) = %v, want %v", tt.from, tt.to, got, tt.want)
			}
		})
	}
}

// OnExit sees the row as it was and OnEnter sees it as it is, so the write sits
// between them.
func TestTransitionRunsExitWriteEnter(t *testing.T) {
	t.Parallel()

	d := &door{state: open}
	if err := machine().Transition(t.Context(), nil, d, shut); err != nil {
		t.Fatalf("Transition: %v", err)
	}
	if d.state != shut {
		t.Errorf("state = %s, want %s", d.state, shut)
	}

	want := []string{"exit open", "write", "enter shut"}
	if len(d.log) != len(want) {
		t.Fatalf("log = %v, want %v", d.log, want)
	}
	for i := range want {
		if d.log[i] != want[i] {
			t.Errorf("log[%d] = %q, want %q", i, d.log[i], want[i])
		}
	}
}

func TestTransitionRefusesUndeclaredMoves(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name string
		from state
		to   state
	}{
		{name: "an edge the topology does not declare", from: open, to: sealed},
		{name: "out of a terminal state", from: sealed, to: open},
		{name: "out of a state the machine does not know", from: unknown, to: open},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			d := &door{state: tt.from}
			if err := machine().Transition(t.Context(), nil, d, tt.to); !errors.Is(err, errs.ErrConflict) {
				t.Fatalf("Transition(%s -> %s) = %v, want ErrConflict", tt.from, tt.to, err)
			}
			if d.state != tt.from {
				t.Errorf("state = %s, want it left at %s", d.state, tt.from)
			}
			if len(d.log) != 0 {
				t.Errorf("log = %v, want a refused move to run nothing", d.log)
			}
		})
	}
}
