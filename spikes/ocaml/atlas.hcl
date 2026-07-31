// The schema this diffs against is lib/schema.sql, which is also what dune
// embeds in the binary — so migrations/ and the database the app creates come
// from one text rather than from two that have to agree.
env "local" {
  src = "file://lib/schema.sql"
  dev = "sqlite://file?mode=memory&_fk=1"
  migration {
    dir = "file://migrations"
  }
}
