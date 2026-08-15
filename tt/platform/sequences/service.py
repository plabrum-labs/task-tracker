"""Issuing the next number in a per-project sequence.

A port of snacks' ``generate_next_identifier`` onto tt's stack: sync SQLAlchemy
instead of async, over the same Postgres. The mechanism is unchanged — upsert the
counter row so it exists, then bump it and read the new value back in one
``UPDATE ... RETURNING``, which is atomic within the call's single write
transaction. What differs is only the scope (a project, not an organization) and
that the caller keeps the returned integer as a ``number`` rather than formatting a
padded identifier string here — the ref a frontend shows is computed from the
project's slug and this number, not stored.
"""

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from tt.platform.sequences.models import Sequence


def next_number(tx: Session, project_id: int, seq_type: str) -> int:
    """Atomically issue the next number for ``(project_id, seq_type)``, starting at 1.

    The upsert makes the counter row exist without disturbing one that already
    does; the bump-and-return is the atomic step that hands two concurrent writers
    distinct numbers."""
    tx.execute(
        pg_insert(Sequence)
        .values(project_id=project_id, type=seq_type, current_value=0)
        .on_conflict_do_nothing(index_elements=["project_id", "type"])
    )
    return tx.execute(
        update(Sequence)
        .where(Sequence.project_id == project_id, Sequence.type == seq_type)
        .values(current_value=Sequence.current_value + 1)
        .returning(Sequence.current_value)
    ).scalar_one()
