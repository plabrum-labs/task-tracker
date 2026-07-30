package schema

import (
	"entgo.io/ent"
	"entgo.io/ent/dialect/entsql"
	"entgo.io/ent/schema/edge"
	"entgo.io/ent/schema/field"
	"entgo.io/ent/schema/index"
)

// Ref is the edge schema behind Issue's blocks/blocked_by pair. A row reads
// "blocked is blocked by blocker". Decomposition is not a Ref — a subtask has
// one parent, so it is Issue's own parent_id column.
type Ref struct {
	ent.Schema
}

func (Ref) Mixin() []ent.Mixin {
	return []ent.Mixin{
		TimeMixin{},
	}
}

func (Ref) Fields() []ent.Field {
	return []ent.Field{
		field.Int("blocked_id"),
		field.Int("blocker_id"),
	}
}

func (Ref) Edges() []ent.Edge {
	return []ent.Edge{
		edge.To("blocked", Issue.Type).
			Field("blocked_id").
			Unique().
			Required().
			Annotations(entsql.OnDelete(entsql.Cascade)),
		edge.To("blocker", Issue.Type).
			Field("blocker_id").
			Unique().
			Required().
			Annotations(entsql.OnDelete(entsql.Cascade)),
	}
}

func (Ref) Indexes() []ent.Index {
	return []ent.Index{
		index.Fields("blocked_id", "blocker_id").
			Unique(),
	}
}
