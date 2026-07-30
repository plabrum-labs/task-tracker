// Package contract holds the types backend returns and a frontend renders.
// These are not database rows: ent lives under backend/internal and never
// crosses this line, so a schema change cannot reshape --json without going
// through a conversion somewhere below. Each object carries its own Actions,
// which a persisted row cannot.
//
// One package rather than one per domain because Issue holds a Project and a
// []Comment. Split per domain, the types stay acyclic only while the nesting
// happens to point one way; one package makes a cycle structurally impossible
// however the graph grows.
package contract

// Action is one entry in an object's menu.
//
// Absent from the slice means the action does not apply here at all. Present
// with Reason == "" means it can run now. Present with a Reason means it
// applies but is refused, and Reason says why.
type Action[K ~string] struct {
	Key    K      `json:"key"`
	Label  string `json:"label"`
	Reason string `json:"reason"` // "" when runnable
}

// Runnable reports whether the action can run as things stand.
func (a Action[K]) Runnable() bool {
	return a.Reason == ""
}
