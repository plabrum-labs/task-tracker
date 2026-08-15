"""The per-project counter ``next_number`` draws from.

Numbers start at 1 and rise by one; each ``(project, type)`` keeps its own count,
so two projects both start at 1 and two type strings in one project do not share a
run. Only issues draw a number today, but the counter keys on an arbitrary type, so
the independence case exercises that keying with a second type string directly. The
counter is exercised against a real Postgres engine, the same one the domain tests
use, so the ``RETURNING`` bump is the real SQL edge and not a stand-in.
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
    # The domains only ever draw the "issue" type, but the counter keys on
    # ``(project, type)``, so a second type string in the same project is its own run.
    project = a_project(db, "ENG")
    with platform_db.transaction(db) as tx:
        assert next_number(tx, project.id, "issue") == 1
        assert next_number(tx, project.id, "issue") == 2
        # A different type in the same project starts fresh at 1.
        assert next_number(tx, project.id, "other") == 1
        assert next_number(tx, project.id, "other") == 2


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
