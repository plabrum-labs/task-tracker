"""The number-columns migration, driven against seeded old-shape rows.

Unlike the domain tests, which build the schema with ``create_all`` on an in-memory
database, a backfill only means anything against rows that predate it — so this
steps a real temp-file database up to the revision *before* the numbers were added,
inserts issues, epics and milestones the old way, then runs the migration and reads
back what it wrote. It is the one place Alembic runs under pytest, because the
StaticPool in-memory database it cannot reach is exactly what has no history to
migrate.
"""

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

# The revision that adds the sequences table but no ``number`` columns, and the one
# that adds and backfills them. Seeding happens at ``_BEFORE`` so the columns are
# absent; the assertions read the state ``_AFTER`` leaves.
_BEFORE = "762216d69769"
_AFTER = "e88f1f25f3e8"

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _config(url: str) -> Config:
    config = Config(str(_REPO_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    config.set_main_option("script_location", str(_REPO_ROOT / "alembic"))
    return config


def _seed_old_rows(path: Path) -> None:
    """Two projects with old-shape rows: ENG has three issues (one soft-deleted),
    two epics and two milestones under the first epic; WEB has one issue."""
    con = sqlite3.connect(path)
    try:
        con.execute(
            "INSERT INTO projects (id, slug, title, body, status) VALUES (1,'ENG','','','ACTIVE')"
        )
        con.execute(
            "INSERT INTO projects (id, slug, title, body, status) VALUES (2,'WEB','','','ACTIVE')"
        )
        for iid, pid, deleted in [(1, 1, None), (2, 1, "2020-01-01"), (3, 1, None), (4, 2, None)]:
            con.execute(
                "INSERT INTO issues (id, project_id, title, body, status, priority, deleted_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (iid, pid, f"i{iid}", "", "TODO", 2, deleted),
            )
        for eid in (1, 2):
            con.execute(
                "INSERT INTO epics (id, project_id, title, body, status) VALUES (?,?,?,?,?)",
                (eid, 1, f"e{eid}", "", "ACTIVE"),
            )
        for mid in (1, 2):
            con.execute(
                "INSERT INTO milestones (id, epic_id, title) VALUES (?,?,?)", (mid, 1, f"m{mid}")
            )
        con.commit()
    finally:
        con.close()


def test_the_backfill_numbers_each_project_and_seeds_the_counters(tmp_path: Path) -> None:
    path = tmp_path / "t.db"
    config = _config(f"sqlite:///{path}")
    command.upgrade(config, _BEFORE)
    _seed_old_rows(path)
    command.upgrade(config, _AFTER)

    con = sqlite3.connect(path)
    try:
        issues = con.execute("SELECT id, project_id, number FROM issues ORDER BY id").fetchall()
        epics = con.execute("SELECT id, number FROM epics ORDER BY id").fetchall()
        milestones = con.execute(
            "SELECT id, project_id, number FROM milestones ORDER BY id"
        ).fetchall()
        sequences = con.execute(
            "SELECT project_id, type, current_value FROM sequences ORDER BY type, project_id"
        ).fetchall()
    finally:
        con.close()

    # Numbered in id order within the project, the soft-deleted issue included so the
    # run stays dense; WEB restarts at 1 rather than continuing ENG's run.
    assert issues == [(1, 1, 1), (2, 1, 2), (3, 1, 3), (4, 2, 1)]
    assert epics == [(1, 1), (2, 2)]
    # A milestone's project is denormalized from its epic, then numbered within it.
    assert milestones == [(1, 1, 1), (2, 1, 2)]
    # Each counter is seeded to where the backfill left off, so the next allocation
    # continues the run.
    assert sequences == [
        (1, "epic", 2),
        (1, "issue", 3),
        (2, "issue", 1),
        (1, "milestone", 2),
    ]


def test_the_migration_round_trips(tmp_path: Path) -> None:
    # Down and back up again leaves the migration applyable, so the downgrade is not
    # a dead branch.
    config = _config(f"sqlite:///{tmp_path / 't.db'}")
    command.upgrade(config, _AFTER)
    command.downgrade(config, _BEFORE)
    command.upgrade(config, _AFTER)
