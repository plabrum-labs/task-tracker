"""How ``default_url`` resolves the database a frontend opens, and that ``connect``
hands back a usable engine.

``default_url`` layers three sources: the ``TT_DB`` environment variable, the
``[database] url`` in the per-user config, and the local dev server as the last
resort — this pins that precedence. ``connect`` is a thin engine opener, so the
one thing worth asserting is that the engine it returns actually talks to the
server.
"""

from pathlib import Path

import pytest
from sqlalchemy import Engine, text
from tests import conftest

from tt.platform import config
from tt.platform import db as platform_db


def test_tt_db_overrides_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TT_DB", "postgresql+psycopg://someone@elsewhere/other")
    assert platform_db.default_url() == "postgresql+psycopg://someone@elsewhere/other"


def test_the_config_url_is_used_when_tt_db_is_unset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config_file = tmp_path / "config.toml"
    config_file.write_text('[database]\nurl = "postgresql+psycopg://cfg@host/db"\n')
    monkeypatch.delenv("TT_DB", raising=False)
    monkeypatch.setenv("TT_CONFIG", str(config_file))
    assert config.load_database_url() == "postgresql+psycopg://cfg@host/db"
    assert platform_db.default_url() == "postgresql+psycopg://cfg@host/db"


def test_the_default_is_the_local_dev_server(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A config path that names no file, so the config supplies no url either.
    monkeypatch.delenv("TT_DB", raising=False)
    monkeypatch.setenv("TT_CONFIG", str(tmp_path / "absent.toml"))
    assert config.load_database_url() is None
    assert platform_db.default_url() == "postgresql+psycopg://tt:tt@localhost:54329/tt"


def test_connect_returns_a_working_engine(db: Engine) -> None:
    # Depend on the ``db`` fixture only to guarantee the test database exists;
    # ``connect`` opens its own engine against the same url.
    engine = platform_db.connect(conftest.test_url())
    with engine.connect() as conn:
        assert conn.execute(text("SELECT 1")).scalar() == 1
    engine.dispose()
