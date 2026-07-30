// Package errs holds the sentinel errors every layer below the frontends
// returns.
//
// The sentinels are the whole vocabulary: a frontend decides what to do with a
// failure by matching one of four values, not by reading a message. The CLI maps
// them to exit codes and the TUI maps them to messages, which is why the mapping
// itself lives in each frontend rather than here.
package errs

import (
	"errors"
	"fmt"
)

var (
	// ErrInvalid is bad user input.
	ErrInvalid = errors.New("invalid")
	// ErrNotFound is an id or name that does not exist.
	ErrNotFound = errors.New("not found")
	// ErrConflict is a rule refusing an otherwise well-formed request.
	ErrConflict = errors.New("conflict")
	// ErrEmpty is a request that found nothing eligible to act on.
	ErrEmpty = errors.New("nothing eligible")
)

// Invalidf returns a formatted error wrapping ErrInvalid.
func Invalidf(format string, a ...any) error {
	return fmt.Errorf(format+": %w", append(a, ErrInvalid)...)
}

// NotFoundf returns a formatted error wrapping ErrNotFound.
func NotFoundf(format string, a ...any) error {
	return fmt.Errorf(format+": %w", append(a, ErrNotFound)...)
}

// Conflictf returns a formatted error wrapping ErrConflict.
func Conflictf(format string, a ...any) error {
	return fmt.Errorf(format+": %w", append(a, ErrConflict)...)
}

// Emptyf returns a formatted error wrapping ErrEmpty.
func Emptyf(format string, a ...any) error {
	return fmt.Errorf(format+": %w", append(a, ErrEmpty)...)
}
