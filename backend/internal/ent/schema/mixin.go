package schema

import (
	"context"
	"time"

	"entgo.io/ent"
	"entgo.io/ent/schema/field"
	"entgo.io/ent/schema/mixin"
)

// TimeMixin stamps created_at/updated_at at the schema level, so no handler can
// forget to bump updated_at.
type TimeMixin struct {
	mixin.Schema
}

func (TimeMixin) Fields() []ent.Field {
	return []ent.Field{
		field.Time("created_at").
			Default(time.Now).
			Immutable(),
		field.Time("updated_at").
			Default(time.Now).
			UpdateDefault(time.Now),
	}
}

// Hooks makes a newly created row carry one instant in both stamps.
//
// The two fields default to time.Now separately, so a create leaves updated_at
// a fraction after created_at — sometimes nanoseconds, sometimes zero. Anything
// reading "has this been touched since it was written" off the pair, which is
// what backend/contract's Comment.Edited does, would then be answering a race.
//
// Ent applies field defaults before it runs hooks, so by the time this sees the
// mutation created_at holds the value the row is about to be written with, and
// copying it across is what makes the two agree.
func (TimeMixin) Hooks() []ent.Hook {
	return []ent.Hook{
		func(next ent.Mutator) ent.Mutator {
			return ent.MutateFunc(func(ctx context.Context, m ent.Mutation) (ent.Value, error) {
				// The framework's own hook types rather than the generated
				// hook package: schema cannot import it, because the generated
				// client imports schema and the cycle would not resolve.
				if !m.Op().Is(ent.OpCreate) {
					return next.Mutate(ctx, m)
				}
				stamps, ok := m.(interface {
					CreatedAt() (time.Time, bool)
					SetUpdatedAt(time.Time)
				})
				if !ok {
					return next.Mutate(ctx, m)
				}
				if created, exists := stamps.CreatedAt(); exists {
					stamps.SetUpdatedAt(created)
				}
				return next.Mutate(ctx, m)
			})
		},
	}
}
