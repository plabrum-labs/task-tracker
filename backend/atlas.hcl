// Paths are relative to backend/, and the db-* recipes cd here before running
// Atlas. They have to: Atlas's ent loader writes a temp package into .entc/ in
// the working directory, and from the repository root that package cannot
// import backend/internal/ent/schema.
env "local" {
  src = "ent://internal/ent/schema"
  dev = "sqlite://file?mode=memory&_fk=1"
  migration {
    dir = "file://../migrations"
  }
}
