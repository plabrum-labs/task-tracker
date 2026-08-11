"""The pragmas ``connect`` sets, and the default location ``default_url`` resolves.

A file-backed database is what the CLI and the TUI share, so it
runs in WAL with a busy timeout: two processes can hold one ``tt.db`` without one
erroring "database is locked" the instant the other is mid-write. The in-memory
database a test opens is a single connection held by a ``StaticPool`` and never
contended, so WAL is neither forced nor meaningful there.

That shared file lives at one per-user path, not one relative to the directory a
command runs in — ``default_url`` is where that is decided.
"""

from pathlib import Path

import pytest
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


def test_tt_db_overrides_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TT_DB", "sqlite:///elsewhere.db")
    assert platform_db.default_url() == "sqlite:///elsewhere.db"


def test_the_default_is_the_per_user_xdg_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("TT_DB", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    url = platform_db.default_url()
    # An absolute per-user path, created on demand — not a file relative to the cwd.
    assert url == f"sqlite:///{tmp_path / 'tt' / 'tt.db'}"
    assert (tmp_path / "tt").is_dir()


def test_the_default_falls_back_to_local_share(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("TT_DB", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setattr(platform_db.Path, "home", lambda: tmp_path)
    url = platform_db.default_url()
    assert url == f"sqlite:///{tmp_path / '.local' / 'share' / 'tt' / 'tt.db'}"


def test_data_dir_is_the_xdg_directory_created_on_demand(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # sync rsyncs this directory, not the sqlite URL, so it resolves and creates
    # the tt directory itself — and TT_DB, which only overrides the URL, is ignored.
    monkeypatch.setenv("TT_DB", "sqlite:///elsewhere.db")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    directory = platform_db.data_dir()
    assert directory == tmp_path / "tt"
    assert directory.is_dir()
