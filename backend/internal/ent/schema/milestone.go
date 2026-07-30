package schema

import (
	"entgo.io/ent"
	"entgo.io/ent/schema/edge"
	"entgo.io/ent/schema/field"
	"entgo.io/ent/schema/index"
)

// Milestone is project-scoped and may carry a deadline. Unique on (project, name).
type Milestone struct {
	ent.Schema
}

func (Milestone) Mixin() []ent.Mixin {
	return []ent.Mixin{
		TimeMixin{},
	}
}

func (Milestone) Fields() []ent.Field {
	return []ent.Field{
		field.String("name").
			NotEmpty(),
		field.Time("due").
			Optional().
			Nillable(),
		field.String("description").
			Default(""),
	}
}

func (Milestone) Edges() []ent.Edge {
	return []ent.Edge{
		edge.From("project", Project.Type).
			Ref("milestones").
			Unique().
			Required(),
		edge.To("issues", Issue.Type),
	}
}

func (Milestone) Indexes() []ent.Index {
	return []ent.Index{
		index.Fields("name").
			Edges("project").
			Unique(),
	}
}
