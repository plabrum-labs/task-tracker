package dbtest_test

import (
	"fmt"
	"testing"

	"github.com/Plabrum/tt/internal/dbtest"
)

// TestClientIsConcurrencySafe is the regression test for the race Client
// serialises against. It only fails under `just test-race`: the racing writes
// put back the value that was already there, so the damage is a torn string
// header rather than a wrong schema, and without the detector it passes either
// way.
//
// 32 callers because the two racing goroutines have to be in different phases
// of Schema.Create — one writing in setupTables, one reading in realm. Callers
// that start together tend to march in step and miss it, so the count buys
// spread rather than contention: 8 never trips it, 16 trips it 4 runs in 5, 32
// trips it every time.
func TestClientIsConcurrencySafe(t *testing.T) {
	t.Parallel()

	for i := range 32 {
		t.Run(fmt.Sprintf("client-%d", i), func(t *testing.T) {
			t.Parallel()
			_ = dbtest.Client(t)
		})
	}
}
