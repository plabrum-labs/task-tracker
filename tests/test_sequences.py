"""The per-project counter ``next_number`` draws from.

Numbers start at 1 and rise by one; each ``(project, type)`` keeps its own count,
so two projects both start at 1 and issues and epics in one project do not share a
run. The counter is exercised against a real in-memory SQLite engine, through the
same ``StaticPool`` connection the domain tests use, so the ``RETURNING`` bump is
the real SQL edge and not a stand-in.
"""

from sqlalchemy import Engine
from tests.conftest import a_project

from tt.platform import db as platform_db
from tt.platform.sequences import next_number


def test_a_sequence_starts_at_one_and_rises_by_one(db: Engine) -> None:
    project = a_project(db, "ENG")
    with platform_db.transaction(db) as tx:
        assert next_number(tx, project.id, "issue") == 1
        assert next_number(tx, project.id, "issue") == 2
        assert next_number(tx, project.id, "issue") == 3


def test_each_type_counts_independently(db: Engine) -> None:
    project = a_project(db, "ENG")
    with platform_db.transaction(db) as tx:
        assert next_number(tx, project.id, "issue") == 1
        assert next_number(tx, project.id, "issue") == 2
        # A different type in the same project is its own run, starting fresh.
        assert next_number(tx, project.id, "epic") == 1
        assert next_number(tx, project.id, "milestone") == 1


def test_each_project_counts_independently(db: Engine) -> None:
    eng = a_project(db, "ENG")
    web = a_project(db, "WEB")
    with platform_db.transaction(db) as tx:
        assert next_number(tx, eng.id, "issue") == 1
        assert next_number(tx, eng.id, "issue") == 2
        # A different project's run of the same type starts at 1, not 3.
        assert next_number(tx, web.id, "issue") == 1


def test_a_run_survives_across_transactions(db: Engine) -> None:
    project = a_project(db, "ENG")
    with platform_db.transaction(db) as tx:
        assert next_number(tx, project.id, "issue") == 1
    # A later call in a fresh transaction reads the committed counter, not a reset.
    with platform_db.transaction(db) as tx:
        assert next_number(tx, project.id, "issue") == 2
