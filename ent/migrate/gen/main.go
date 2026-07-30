// Command gen generates a versioned migration into migrations/ by diffing the
// Ent schema against the current state of that directory. Dev-time only — the
// binary never runs this; it applies the committed output instead.
//
// Usage:
//
//	go run ./ent/migrate/gen -name init
package main

import (
	"context"
	"database/sql"
	"flag"
	"log"

	"ariga.io/atlas/sql/migrate"
	"entgo.io/ent/dialect"
	"entgo.io/ent/dialect/sql/schema"

	entmigrate "github.com/Plabrum/tt/ent/migrate"

	_ "modernc.org/sqlite"
)

// Atlas opens the dev URL with sql.Open("sqlite3", …) (its SQLite driver
// hardcodes that name), while modernc.org/sqlite registers itself as "sqlite".
// Alias the two without depending on modernc exporting its driver type.
func init() {
	probe, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		log.Fatalf("probing sqlite driver: %v", err)
	}
	sql.Register("sqlite3", probe.Driver())
	if err := probe.Close(); err != nil {
		log.Fatalf("closing probe: %v", err)
	}
}

// devURL is a throwaway in-memory database Atlas replays the existing
// migration directory onto to compute current state. Pragmas use modernc's
// form — it rejects parameters it doesn't recognise, so mattn's `_fk=1` fails.
const devURL = "sqlite://file?mode=memory&_pragma=foreign_keys(1)"

func main() {
	name := flag.String("name", "", "name of the migration file to generate")
	flag.Parse()
	if *name == "" {
		log.Fatal("-name is required")
	}

	dir, err := migrate.NewLocalDir("migrations")
	if err != nil {
		log.Fatalf("opening migrations dir: %v", err)
	}

	err = entmigrate.NamedDiff(context.Background(), devURL, *name,
		schema.WithDir(dir),
		// Ent defaults to golang-migrate's up/down pair even for a LocalDir. We
		// apply these with Atlas's own Executor, which wants one .sql per
		// version — a stray .down.sql would be read as another migration.
		schema.WithFormatter(migrate.DefaultFormatter),
		schema.WithMigrationMode(schema.ModeReplay),
		schema.WithDialect(dialect.SQLite),
		schema.WithDropColumn(true),
		schema.WithDropIndex(true),
	)
	if err != nil {
		log.Fatalf("generating migration: %v", err)
	}
}
