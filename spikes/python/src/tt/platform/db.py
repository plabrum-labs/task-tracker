"""The database, with no table in it.

Everything a query needs and nothing about what is being queried: an engine, the
way to open one, a read scope, a write transaction, and the narrowing of
SQLAlchemy's error into the domain's ``Broken``. A domain's ``services`` names
the tables; this file cannot.

Sync SQLAlchemy on purpose. The tracker is one local single-user SQLite file, so
there is no concurrency for async to buy — and a synchronous ``execute`` is a
plain function of the object and the session, which is what keeps the whole
action layer testable with one in-memory database and no event loop.
"""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from tt.platform.error import Broken


class StoreError(Exception):
    """A storage failure, kept separate from the domain's refusals. ``transaction``
    converts it into ``Broken`` at the edge; the domain never raises one."""


class MissingAfterInsert(StoreError):
    """The case with no row to blame: a create writes, then loads the row back
    through the same projection every read uses, and that load can only come up
    empty if something removed the row in between."""

    def __init__(self, what: str) -> None:
        super().__init__(f"{what} vanished between the insert and the read")


def connect(url: str) -> Engine:
    """Open the database. ``sqlite://`` is the shared in-memory one a test fixture
    uses: SQLite gives each connection its own ``:memory:``, so a ``StaticPool``
    holds the single connection that keeps the schema and the rows alive for the
    life of the engine."""
    if url == "sqlite://":
        engine = create_engine(
            url,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    else:
        engine = create_engine(url)

    # The DDL asks for it too, but a PRAGMA is per-connection, so the cascade an
    # issues row leans on is only enforced if every connection turns it on.
    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection: object, _record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


@contextmanager
def reading(engine: Engine) -> Iterator[Session]:
    """A session for reads. Nothing is written, so there is no commit to make."""
    with Session(engine) as session:
        yield session


@contextmanager
def transaction(engine: Engine) -> Iterator[Session]:
    """One public call is one transaction. The body runs inside ``BEGIN``…``COMMIT``;
    any exception it raises — including a refusal after rows are written — rolls
    the whole thing back. The frontends open this at their edge and hand the
    session down to an action's ``execute``.

    A driver-level failure is the machine saying no rather than the row, so it
    reaches a caller as ``Broken`` rather than as one of the two refusals.
    """
    session = Session(engine)
    try:
        with session.begin():
            yield session
    except SQLAlchemyError as e:
        raise Broken(str(e)) from e
    finally:
        session.close()


def apply_ddl(engine: Engine, statements: list[str]) -> None:
    """Run a DDL text, safe on a database that already has the tables. The
    statements are the caller's: this knows how to run them and not what is in
    them — see ``domains.schema``."""
    with engine.begin() as connection:
        for sql in statements:
            connection.execute(text(sql))


def emitted_ddl(engine: Engine) -> list[str]:
    """What the database actually holds, so a test can hold the emitted schema
    against the columns the tables declare."""
    with engine.connect() as connection:
        rows = connection.execute(
            text("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name")
        ).all()
    return [row[0] for row in rows]
