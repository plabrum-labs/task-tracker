//go:build ignore

package main

import (
	"log"

	"entgo.io/ent/entc"
	"entgo.io/ent/entc/gen"
)

func main() {
	err := entc.Generate("./schema", &gen.Config{
		Package: "github.com/Plabrum/tt/ent",
		Target:  ".",
	},
		// versioned-migration gives us ent/migrate's Diff/NamedDiff, which is how
		// migrations are generated here — no Atlas CLI in the loop.
		// upsert is for the project/label auto-create paths that land next.
		entc.FeatureNames("sql/versioned-migration", "sql/upsert"),
	)
	if err != nil {
		log.Fatalf("running ent codegen: %v", err)
	}
}
