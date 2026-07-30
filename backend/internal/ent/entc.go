//go:build ignore

package main

import (
	"log"

	"entgo.io/ent/entc"
	"entgo.io/ent/entc/gen"
)

func main() {
	err := entc.Generate("./schema", &gen.Config{
		Package: "github.com/Plabrum/tt/backend/internal/ent",
		Target:  ".",
	},
		// upsert backs the project and label auto-create paths, which have to be
		// idempotent: capture creates a container on first use and reuses it after.
		entc.FeatureNames("sql/upsert"),
	)
	if err != nil {
		log.Fatalf("running ent codegen: %v", err)
	}
}
