env "local" {
  src = "ent://ent/schema"
  dev = "sqlite://file?mode=memory&_fk=1"
  migration {
    dir = "file://migrations"
  }
}
