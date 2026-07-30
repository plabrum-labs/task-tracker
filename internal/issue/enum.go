package issue

import (
	entissue "github.com/Plabrum/tt/ent/issue"
	entref "github.com/Plabrum/tt/ent/ref"
	"github.com/Plabrum/tt/internal/errs"
)

// The conversions across the ent/domain boundary. Exhaustive switches rather
// than casts, so a value added to one side and not the other fails at the
// default arm instead of travelling as a constant nothing matches.
//
// Ent in an exported function signature is still unreachable from cli/ and ui/:
// neither can name entissue.Status without importing ent.

// StatusFromEnt converts a stored status to the domain value.
func StatusFromEnt(s entissue.Status) (Status, error) {
	switch s {
	case entissue.StatusTodo:
		return StatusTodo, nil
	case entissue.StatusDoing:
		return StatusDoing, nil
	case entissue.StatusDone:
		return StatusDone, nil
	default:
		return "", errs.Conflictf("stored status %q is not one this binary knows", s)
	}
}

// StatusToEnt converts a domain status to the stored value.
func StatusToEnt(s Status) (entissue.Status, error) {
	switch s {
	case StatusTodo:
		return entissue.StatusTodo, nil
	case StatusDoing:
		return entissue.StatusDoing, nil
	case StatusDone:
		return entissue.StatusDone, nil
	default:
		return "", errs.Invalidf("unknown status %q", s)
	}
}

// PriorityFromEnt converts a stored priority to the domain value.
func PriorityFromEnt(p entissue.Priority) (Priority, error) {
	switch p {
	case entissue.PriorityNormal:
		return PriorityNormal, nil
	case entissue.PriorityHi:
		return PriorityHi, nil
	default:
		return "", errs.Conflictf("stored priority %q is not one this binary knows", p)
	}
}

// PriorityToEnt converts a domain priority to the stored value.
func PriorityToEnt(p Priority) (entissue.Priority, error) {
	switch p {
	case PriorityNormal:
		return entissue.PriorityNormal, nil
	case PriorityHi:
		return entissue.PriorityHi, nil
	default:
		return "", errs.Invalidf("unknown priority %q", p)
	}
}

// KindFromEnt converts a stored ref kind to the domain value.
func KindFromEnt(k entref.Kind) (Kind, error) {
	switch k {
	case entref.KindDep:
		return KindDep, nil
	case entref.KindSubtask:
		return KindSubtask, nil
	default:
		return "", errs.Conflictf("stored ref kind %q is not one this binary knows", k)
	}
}

// KindToEnt converts a domain ref kind to the stored value.
func KindToEnt(k Kind) (entref.Kind, error) {
	switch k {
	case KindDep:
		return entref.KindDep, nil
	case KindSubtask:
		return entref.KindSubtask, nil
	default:
		return "", errs.Invalidf("unknown ref kind %q", k)
	}
}
