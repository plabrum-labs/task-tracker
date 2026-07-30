package schema

import (
	"entgo.io/ent"
	"entgo.io/ent/schema/edge"
	"entgo.io/ent/schema/field"
)

// Project is the auto-created container inferred from the working directory.
type Project struct {
	ent.Schema
}

func (Project) Mixin() []ent.Mixin {
	return []ent.Mixin{
		TimeMixin{},
	}
}

func (Project) Fields() []ent.Field {
	return []ent.Field{
		field.String("slug").
			Unique().
			NotEmpty(),
		field.String("title").
			Default(""),
		field.String("description").
			Default(""),
		field.Enum("status").
			Values("active", "archived").
			Default("active"),
	}
}

func (Project) Edges() []ent.Edge {
	return []ent.Edge{
		edge.To("issues", Issue.Type),
		edge.To("milestones", Milestone.Type),
	}
}
