"""The refusals a caller can get back, as an exception hierarchy.

A domain action signals a refusal by raising one of these. They are separate
from the storage failures in ``db`` (``StoreError``): nothing a caller can do
about a database error is anything an action refused, so ``db`` converts its
failures into ``Broken`` at the edge rather than the domain ever raising one.
"""


class Error(Exception):
    """Base of the three refusals, so a frontend can catch the family at once."""


class Invalid(Error):
    """The request does not make sense — a bad payload, an unknown key or id."""


class Conflict(Error):
    """The request makes sense; the row says no."""


class Broken(Error):
    """Neither: the request was fine and the machine was not.

    A store failure has no object to blame, and dressing it up as one of the
    other two would put a database error where a reason belongs.
    """
