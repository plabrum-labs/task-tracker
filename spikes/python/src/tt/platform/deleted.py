"""A row in the trash: the domain object it was, and when it went.

One generic type, so ``Group[Deleted[Issue]]`` and ``Group[Deleted[Project]]``
are distinct types — which is what makes "a deleted row cannot be edited" a fact
about the registration's type rather than a rule the lists happen to follow.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Deleted[T]:
    inner: T
    deleted_at: str
