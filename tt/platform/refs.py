"""How a frontend addresses a row.

An issue carries a ``<slug>-<number>`` ref — a project's slug, a hyphen, and the
row's project-scoped number, ``ENG-12``. The number is split off the right, so a
slug that itself contains a hyphen is left whole. A string that is not a ref (no
hyphen, an empty half, or a non-numeric number) parses to ``None``, which a
resolver turns into "no such row" rather than a crash. Slugs are stored uppercase,
so the slug half is uppercased here — the canonical form a resolver matches on —
and a ref types the same in any case.

An epic or a milestone is not addressed by a ref: both are named, project-scoped
groupings, so a ``NamedRef`` places a title within a project the way a slug places
a number.
"""

from dataclasses import dataclass


def parse_ref(ref: str) -> tuple[str, int] | None:
    """The ``(slug, number)`` a ref names, or ``None`` when it is not a ref."""
    slug, sep, number = ref.rpartition("-")
    if not sep or not slug or not number.isdigit():
        return None
    return slug.upper(), int(number)


@dataclass(frozen=True)
class NamedRef:
    """How an epic or a milestone is addressed: a ``title`` within a project. The
    slug is stored uppercase, so it is canonicalised here and may be given in any
    case; the title is matched as typed."""

    project_slug: str
    title: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_slug", self.project_slug.upper())

    def __repr__(self) -> str:
        return f"{self.title!r} in {self.project_slug}"
