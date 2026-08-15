"""The database, and the base every table is mapped on.

An engine, the way to open one, a read scope and a write transaction, and the one
``BaseDBModel`` that carries the columns every row has. A domain's ``queries``
names the tables; this file cannot.

Sync SQLAlchemy on purpose. The tracker is a single-user client of a Postgres
server, so its concurrency is a person at one machine at a time, not a load async
would relieve — and a synchronous ``execute`` is a plain function of the object
and the session, which is what keeps the whole action layer testable without an
event loop.

Sessions do not expire on commit, so an object a read returns still answers for
its columns and its eagerly-loaded relationships once the ``with`` block that
loaded it has closed.
"""

import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from functools import wraps
from typing import Concatenate

from sqlalchemy import DateTime, Engine, create_engine, func
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from tt.platform import config

# The local development/throwaway server (the `compose.yaml` Postgres on an
# esoteric port). A real deployment names its own server in the ``[database]``
# table (or ``TT_DB``); this is only the fallback so a checkout runs against the
# container without ceremony.
_DEV_URL = "postgresql+psycopg://tt:tt@localhost:54329/tt"


def default_url() -> str:
    """The database a frontend opens when given no explicit ``--db``.

    ``TT_DB`` overrides everything — the tests and any throwaway database go
    through it. Otherwise the ``[database] url`` in the per-user config names the
    server a device talks to, and without that the local ``compose.yaml`` Postgres
    is used, so a fresh checkout runs against the container."""
    return os.environ.get("TT_DB") or config.load_database_url() or _DEV_URL


def connect(url: str) -> Engine:
    """Open the database. Postgres enforces foreign keys and lets readers and a
    writer proceed without blocking on its own, so there is no per-connection
    configuration to attach here the way a SQLite file needed."""
    return create_engine(url)


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
