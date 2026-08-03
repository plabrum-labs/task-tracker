"""A project, and the issues under it counted.

The counts are part of the projection rather than something a caller fetches when
it needs them, because ``editStatus`` and ``delete`` both refuse on them. An
availability hook is a pure function of the object, so anything a hook reads has
to be in the object — computing the counts per row is what stops a list from
offering a menu it cannot justify.
"""

from dataclasses import dataclass
from enum import StrEnum

from tt.platform.deleted import Deleted


class Status(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


@dataclass(frozen=True)
class Project:
    id: int
    slug: str
    title: str
    body: str
    status: Status
    todo: int
    doing: int
    done: int
    created_at: str
    updated_at: str

    def subject(self) -> str:
        return f"project {self.slug}"

    def issue_count(self) -> int:
        return self.todo + self.doing + self.done


@dataclass(frozen=True)
class Draft:
    slug: str
    title: str
    body: str


@dataclass(frozen=True)
class Restorable:
    """What ``restore`` is offered against.

    The trash row alone is not enough: the partial unique index covers live rows
    only, so a slug freed by a delete can be taken again and bringing the old
    project back would then collide. A hook is a pure function of its object, so
    the live list it must read is carried on the object — the same reason
    ``Project`` carries its counts. Without this the refusal is a UNIQUE
    constraint violation with no sentence in it.
    """

    deleted: Deleted[Project]
    live: list[Project]
