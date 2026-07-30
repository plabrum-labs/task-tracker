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
		// Backs the parent edge. Declared as a field so a loader can read the
		// FK off the row instead of walking the edge to learn it exists.
		field.Int("parent_id").
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

		// Decomposition. A subtask has at most one parent, which is why this is
		// a plain nullable FK rather than another Ref kind: the column can hold
		// one value, so the rule needs no index and no service guard.
		edge.To("subtasks", Issue.Type).
			Annotations(entsql.OnDelete(entsql.SetNull)).
			From("parent").
			Unique().
			Field("parent_id"),

		// Blocking, which is many-to-many in both directions, through the Ref
		// edge schema.
		//
		// The explicit StorageKey is load-bearing: for a self-reference Ent cannot
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
