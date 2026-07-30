package schema

import (
	"time"

	"entgo.io/ent"
	"entgo.io/ent/dialect/entsql"
	"entgo.io/ent/schema/edge"
	"entgo.io/ent/schema/field"
)

// Comment is the issue log. Append-only; author is free text.
type Comment struct {
	ent.Schema
}

func (Comment) Fields() []ent.Field {
	return []ent.Field{
		field.Time("at").
			Default(time.Now).
			Immutable(),
		field.String("author").
			NotEmpty(),
		field.Text("body").
			Default(""),
	}
}

func (Comment) Edges() []ent.Edge {
	return []ent.Edge{
		edge.From("issue", Issue.Type).
			Ref("comments").
			Unique().
			Required().
			Annotations(entsql.OnDelete(entsql.Cascade)),
	}
}
