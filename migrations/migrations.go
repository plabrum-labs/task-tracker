// Package migrations embeds the committed Atlas migration files.
//
// It is its own package only because //go:embed cannot reach a parent
// directory — the embed has to live in or below migrations/.
package migrations

import "embed"

// FS holds every migration plus atlas.sum, so the integrity of the directory
// can be checked at runtime rather than trusted.
//
//go:embed *.sql atlas.sum
var FS embed.FS
