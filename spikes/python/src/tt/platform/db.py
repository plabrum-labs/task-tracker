"""The database, with no table in it.

An engine, the way to open one, a read scope and a write transaction — and the
narrowing of SQLAlchemy's error into the domain's ``Broken`` at the edge. A
domain's ``queries`` names the tables; this file cannot.

Sync SQLAlchemy on purpose. The tracker is one local single-user SQLite file, so
there is no concurrency for async to buy — and a synchronous ``execute`` is a
plain function of the object and the session, which is what keeps the whole
action layer testable with one in-memory database and no event loop.

Sessions do not expire on commit, so an object a read returns still answers for
its columns and its eagerly-loaded relationships once the ``with`` block that
loaded it has closed.
"""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from tt.platform.error import Broken


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

    # A foreign key's cascade is only enforced on a connection that turned the
    # pragma on, so every connection this engine opens does.
    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection: object, _record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


@contextmanager
def reading(engine: Engine) -> Iterator[Session]:
    """A session for reads. Nothing is written, so there is no commit to make."""
    with Session(engine, expire_on_commit=False) as session:
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
    session = Session(engine, expire_on_commit=False)
    try:
        with session.begin():
            yield session
    except SQLAlchemyError as e:
        raise Broken(str(e)) from e
    finally:
        session.close()
