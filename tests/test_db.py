"""The pragmas ``connect`` sets on every connection it opens.

A file-backed database is what the CLI, the TUI and the MCP server share, so it
runs in WAL with a busy timeout: two processes can hold one ``tt.db`` without one
erroring "database is locked" the instant the other is mid-write. The in-memory
database a test opens is a single connection held by a ``StaticPool`` and never
contended, so WAL is neither forced nor meaningful there.
"""

from pathlib import Path

from sqlalchemy import Engine, text

from tt.platform import db as platform_db


def _pragma(engine: Engine, name: str) -> object:
    with platform_db.reading(engine) as session:
        return session.execute(text(f"PRAGMA {name}")).scalar()


def test_a_file_database_runs_in_wal_with_a_busy_timeout(tmp_path: Path) -> None:
    engine = platform_db.connect(f"sqlite:///{tmp_path / 'tt.db'}")
    assert _pragma(engine, "journal_mode") == "wal"
    assert _pragma(engine, "busy_timeout") == 5000


def test_the_in_memory_database_is_not_forced_into_wal() -> None:
    engine = platform_db.connect("sqlite://")
    # An in-memory database journals in memory; WAL is never forced onto it.
    assert _pragma(engine, "journal_mode") == "memory"
    # The busy timeout is set unconditionally, so it holds here too.
    assert _pragma(engine, "busy_timeout") == 5000
