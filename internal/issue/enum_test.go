package issue_test

import (
	"testing"

	"entgo.io/ent/dialect/sql/schema"

	"github.com/Plabrum/tt/ent/migrate"
	"github.com/Plabrum/tt/internal/issue"
)

// TestEnumsMatchSchema drives itself from the generated column descriptors, so
// the set it checks is the set the database actually accepts. A hand-written
// list of constants cannot do that: whoever forgets to add a new enum value to
// the domain also forgets to add it to the list, and the test stays green.
//
// Only the schema→domain direction is enumerable. An orphan domain constant
// with no schema value is invisible here and harmless: it can never come out of
// the database, and ToEnt refuses to write it.
func TestEnumsMatchSchema(t *testing.T) {
	t.Parallel()

	t.Run("status", func(t *testing.T) {
		t.Parallel()
		checkEnum(t, migrate.IssuesColumns, "status",
			issue.StatusFromEnt, issue.StatusToEnt, issue.ParseStatus)
	})
	t.Run("priority", func(t *testing.T) {
		t.Parallel()
		checkEnum(t, migrate.IssuesColumns, "priority",
			issue.PriorityFromEnt, issue.PriorityToEnt, issue.ParsePriority)
	})
	t.Run("kind", func(t *testing.T) {
		t.Parallel()
		checkEnum(t, migrate.RefsColumns, "kind",
			issue.KindFromEnt, issue.KindToEnt, nil)
	})
}

// checkEnum asserts that every value the schema declares for one column has a
// domain constant that round-trips back to the same string, and that the domain
// has no more of them than the schema does.
func checkEnum[Stored ~string, Domain ~string](
	t *testing.T,
	columns []*schema.Column,
	name string,
	from func(Stored) (Domain, error),
	to func(Domain) (Stored, error),
	parse func(string) (Domain, error),
) {
	t.Helper()

	values := enumValues(t, columns, name)
	seen := make(map[Domain]bool, len(values))
	for _, v := range values {
		domain, err := from(Stored(v))
		if err != nil {
			t.Errorf("schema %s %q has no domain constant: %v", name, v, err)
			continue
		}
		seen[domain] = true

		stored, err := to(domain)
		if err != nil {
			t.Errorf("domain %s %q does not convert back: %v", name, domain, err)
			continue
		}
		if string(stored) != v {
			t.Errorf("%s %q round-tripped to %q", name, v, stored)
		}
		if parse != nil {
			if _, err := parse(v); err != nil {
				t.Errorf("schema %s %q is not accepted by the parser: %v", name, v, err)
			}
		}
	}

	// Fewer would mean two schema values collapsing onto one constant, which
	// makes the conversion lossy in a way the round-trip above cannot see.
	if len(seen) != len(values) {
		t.Errorf("%s maps %d schema values onto %d domain values",
			name, len(values), len(seen))
	}
}

// enumValues reads one column's declared enum set out of the generated schema.
func enumValues(t *testing.T, columns []*schema.Column, name string) []string {
	t.Helper()
	for _, c := range columns {
		if c.Name != name {
			continue
		}
		if len(c.Enums) == 0 {
			t.Fatalf("column %q declares no enum values", name)
		}
		return c.Enums
	}
	t.Fatalf("no column named %q in the generated schema", name)
	return nil
}
