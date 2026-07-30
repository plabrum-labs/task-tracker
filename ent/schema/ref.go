package schema

import (
	"entgo.io/ent"
	"entgo.io/ent/dialect/entsql"
	"entgo.io/ent/schema/edge"
	"entgo.io/ent/schema/field"
	"entgo.io/ent/schema/index"
)

// Ref is the edge schema behind Issue's blocks/blocked_by pair. A row reads
// "blocked is blocked by blocker". `kind` distinguishes a plain prerequisite
// from a decomposition; both block identically.
type Ref struct {
	ent.Schema
}

func (Ref) Fields() []ent.Field {
	return []ent.Field{
		field.Int("blocked_id"),
		field.Int("blocker_id"),
		field.Enum("kind").
			Values("dep", "subtask").
			Default("dep"),
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
