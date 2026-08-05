"""Parsing the ``<slug>-<number>`` ref a frontend addresses a row by.

A ref is a project's slug, a hyphen, and the row's project-scoped number —
``ENG-12``. The number is split off the right, so a slug that itself contains a
hyphen is left whole. A string that is not a ref (no hyphen, an empty half, or a
non-numeric number) parses to ``None``, which a resolver turns into "no such row"
rather than a crash.
"""


def parse_ref(ref: str) -> tuple[str, int] | None:
    """The ``(slug, number)`` a ref names, or ``None`` when it is not a ref."""
    slug, sep, number = ref.rpartition("-")
    if not sep or not slug or not number.isdigit():
        return None
    return slug, int(number)
