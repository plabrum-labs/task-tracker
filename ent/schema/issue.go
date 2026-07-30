package schema

import (
	"entgo.io/ent"
	"entgo.io/ent/dialect/entsql"
	"entgo.io/ent/schema/edge"
	"entgo.io/ent/schema/field"
)

// Issue is the unit of work.
type Issue struct {
	ent.Schema
}

func (Issue) Mixin() []ent.Mixin {
	return []ent.Mixin{
		TimeMixin{},
	}
}

func (Issue) Fields() []ent.Field {
	return []ent.Field{
		field.String("title").
			NotEmpty(),
		field.Text("body").
			Default(""),
		field.Enum("status").
			Values("todo", "doing", "done").
			Default("todo"),
		field.Enum("priority").
			Values("normal", "hi").
			Default("normal"),
		field.Time("closed_at").
			Optional().
			Nillable(),
	}
}

func (Issue) Edges() []ent.Edge {
	return []ent.Edge{
		edge.From("project", Project.Type).
			Ref("issues").
			Unique().
			Required(),
		edge.From("milestone", Milestone.Type).
			Ref("issues").
			Unique(),
		edge.To("labels", Label.Type),
		edge.To("comments", Comment.Type).
			Annotations(entsql.OnDelete(entsql.Cascade)),

		// Self-referential M2M through the Ref edge schema, which carries `kind`.
		//
		// The explicit StorageKey is load-bearing: for a self-reference ent cannot
		// tell which of Ref's two edges backs which side of the relation (both point
		// at Issue), so it matches the relation columns against Ref's edge-field
		// names. Naming them here to match Ref's `blocker_id`/`blocked_id` is what
		// makes Through resolve. Column order is (assoc owner, assoc target): the
		// owner of `blocks` is the blocker.
		//
		// The two directions are declared separately rather than chained through
		// From(): ent's loader copies an assoc's StorageKey onto a chained inverse
		// and then rejects the pair as having two storage keys.
		edge.To("blocks", Issue.Type).
			StorageKey(edge.Columns("blocker_id", "blocked_id")).
			Through("blocker_refs", Ref.Type),
		edge.From("blocked_by", Issue.Type).
			Ref("blocks").
			Through("blocked_refs", Ref.Type),
	}
}
