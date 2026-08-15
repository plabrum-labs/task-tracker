"""One-shot loader: copy a SQLite snapshot's rows into the configured Postgres.

The tracker moved from a local SQLite file to a Postgres server, and the raw
SQLite dump cannot be replayed into Postgres (SQLite-flavoured SQL, a different
column order). This reads the snapshot through the shared table metadata instead,
so every value is parsed by its column's type and written back by name — ids and
all — in foreign-key-safe order. It then advances each table's identity sequence
past the largest id it inserted, so the next insert Postgres makes does not
collide with a copied row.

Usage:
    uv run python scripts/import_from_sqlite.py backups/tt_sqlite_snapshot_*.db

The destination is ``db.default_url()`` (the ``[database]`` config / ``TT_DB``).
It refuses to run against a destination that already holds projects unless
``--force`` is given, so a stray second run cannot double-load.
"""

from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import Engine, create_engine, func, insert, inspect, select, text

from tt.platform import db
from tt.platform.db import BaseDBModel


def _copy_table(source: Engine, dest: Engine, table_name: str) -> int:
    """Copy every row of one table from source to dest, by column name, and return
    the count. Reads through the mapped ``Table`` so SQLite's text timestamps and
    dates arrive as typed Python values the Postgres driver can bind."""
    table = BaseDBModel.metadata.tables[table_name]
    with source.connect() as src:
        rows = [dict(row) for row in src.execute(select(table)).mappings()]
    if rows:
        with dest.begin() as out:
            out.execute(insert(table), rows)
    return len(rows)


def _resync_identity(dest: Engine, table_name: str) -> None:
    """Advance a table's ``id`` sequence past the largest copied id, so the next
    Postgres insert does not reuse one. Tables with a composite key and no ``id``
    (the link tables) own no such sequence and are skipped."""
    table = BaseDBModel.metadata.tables[table_name]
    if "id" not in table.c:
        return
    with dest.begin() as out:
        max_id = out.execute(select(func.max(table.c.id))).scalar()
        if max_id is None:
            return
        # pg_get_serial_sequence names the sequence backing the column; setval with
        # is_called=true leaves the next nextval at max_id + 1.
        out.execute(
            text("SELECT setval(pg_get_serial_sequence(:t, 'id'), :v, true)"),
            {"t": table_name, "v": max_id},
        )


def main(argv: list[str]) -> int:
    args = [a for a in argv if a != "--force"]
    force = "--force" in argv
    if len(args) != 1:
        print(__doc__)
        return 2
    snapshot = Path(args[0])
    if not snapshot.is_file():
        print(f"no such snapshot: {snapshot}", file=sys.stderr)
        return 1

    source = create_engine(f"sqlite:///{snapshot.resolve()}")
    dest = db.connect(db.default_url())

    if not force and "projects" in inspect(dest).get_table_names():
        projects = BaseDBModel.metadata.tables["projects"]
        with dest.connect() as conn:
            existing = conn.execute(select(func.count()).select_from(projects)).scalar()
        if existing:
            print(
                f"destination already holds {existing} projects; refusing without --force",
                file=sys.stderr,
            )
            return 1

    # Foreign-key-safe order: parents before children, which is the order the
    # tables were created in.
    total = 0
    for table in BaseDBModel.metadata.sorted_tables:
        copied = _copy_table(source, dest, table.name)
        total += copied
        print(f"  {table.name}: {copied}")
    for table in BaseDBModel.metadata.sorted_tables:
        _resync_identity(dest, table.name)

    print(f"copied {total} rows into {db.default_url().rsplit('@', 1)[-1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
