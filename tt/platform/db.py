"""The database, and the base every table is mapped on.

An engine, the way to open one, a read scope and a write transaction, and the one
``BaseDBModel`` that carries the columns every row has. A domain's ``queries``
names the tables; this file cannot.

Sync SQLAlchemy on purpose. The tracker is one local single-user SQLite file, so
there is no concurrency for async to buy — and a synchronous ``execute`` is a
plain function of the object and the session, which is what keeps the whole
action layer testable with one in-memory database and no event loop.

Sessions do not expire on commit, so an object a read returns still answers for
its columns and its eagerly-loaded relationships once the ``with`` block that
loaded it has closed.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from functools import wraps
from typing import Concatenate

from sqlalchemy import DateTime, Engine, create_engine, event, func
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from sqlalchemy.pool import StaticPool


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

    # Pragmas every connection this engine opens must set for itself.
    #
    # foreign_keys: a cascade is only enforced on a connection that turned it on.
    #
    # busy_timeout: a connection that finds the file locked waits and retries for
    # up to this long rather than erroring "database is locked" at once (the
    # default is 0). This is what lets two processes — an agent driving the MCP
    # server while the TUI holds the same tt.db open — coexist.
    #
    # journal_mode=WAL: readers and a writer proceed without blocking each other,
    # for file databases only. It is meaningless for the single-connection
    # in-memory database a test holds open, which is never contended.
    file_backed = url != "sqlite://"

    @event.listens_for(engine, "connect")
    def _configure_connection(dbapi_connection: object, _record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        if file_backed:
            cursor.execute("PRAGMA journal_mode=WAL")
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
    the whole thing back. A driver-level failure propagates as itself: it is the
    machine saying no, not the object, and dressing it up as a refusal would put a
    database error where a reason belongs."""
    session = Session(engine, expire_on_commit=False)
    try:
        with session.begin():
            yield session
    finally:
        session.close()


def with_transaction[**P, R](
    fn: Callable[Concatenate[Session, P], R],
) -> Callable[Concatenate[Engine, P], R]:
    """Turn a backend call that takes a live ``Session`` into one that takes an
    ``Engine`` and opens the transaction itself.

    This is what keeps session management out of the frontends: a decorated ``api``
    function is called with the engine and nothing else, and one call is one
    transaction — the same boundary the frontend used to draw by hand around every
    ``with db.transaction(...)``. A read commits nothing, so wrapping reads and
    writes alike is safe; a refusal raised inside rolls the whole call back.
    """

    @wraps(fn)
    def wrapper(engine: Engine, *args: P.args, **kwargs: P.kwargs) -> R:
        with transaction(engine) as tx:
            return fn(tx, *args, **kwargs)

    return wrapper


class BaseDBModel(DeclarativeBase):
    """id, timestamps and soft-delete stamp, shared by every mapped table.

    One base, so one ``metadata`` — which is what Alembic diffs and a test
    creates. A table adds its own columns and inherits these, so no model spells
    them out and no write stamps them by hand. Setting ``deleted_at`` is the soft
    delete; it is an ordinary column an action writes, not a method here.
    """

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None, index=True
    )
