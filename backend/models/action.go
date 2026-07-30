// Package models holds the contract types: what backend returns and what a
// frontend renders. It imports no ent, so a schema change cannot reshape
// --json without going through a conversion somewhere below.
//
// One package rather than one per domain because Issue holds a Project and a
// []Comment. Distributed models stay acyclic only while the nesting happens to
// point one way; one package makes a cycle structurally impossible however the
// graph grows.
package models

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
