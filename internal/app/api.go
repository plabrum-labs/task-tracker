// Package app is the API the frontends call. One method per verb, and one
// method is one unit of work: creating an issue with labels, a milestone and a
// parent link commits or rolls back as a whole, however many features it
// touches.
//
// Feature packages are leaves — they take a client and never open a
// transaction. Composing them is this package's job, which is why a rule that
// spans two features is enforced here and a rule that belongs to one is
// enforced inside it. Either way a frontend cannot route around it, because the
// API is all a frontend can see.
package app

import (
	"github.com/Plabrum/tt/ent"
)

// API is what cli/ and ui/ are handed.
//
// The ent client is unexported. A frontend that can reach the client can write
// a query, and the moment one does, the view types stop being the contract — a
// schema change starts breaking the CLI directly instead of breaking the one
// method that was supposed to absorb it.
type API struct {
	client *ent.Client
}

// New returns an API over one client.
func New(client *ent.Client) *API {
	return &API{client: client}
}
