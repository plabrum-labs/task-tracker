package schema

import (
	"entgo.io/ent"
	"entgo.io/ent/schema/edge"
	"entgo.io/ent/schema/field"
)

// Label is global rather than project-scoped: "bug" means "bug" everywhere.
type Label struct {
	ent.Schema
}

func (Label) Fields() []ent.Field {
	return []ent.Field{
		field.String("name").
			Unique().
			NotEmpty(),
		field.String("description").
			Default(""),
	}
}

func (Label) Edges() []ent.Edge {
	return []ent.Edge{
		edge.From("issues", Issue.Type).
			Ref("labels"),
	}
}
