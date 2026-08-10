"""What the client raises.

The domain layer distinguishes a *refusal* — an ``Invalid`` or ``Conflict``, the
tracker legitimately saying no — from a machine failure (a ``SQLAlchemyError``,
which stays a crash). The client surfaces that same distinction as typed
exceptions so a consumer can tell "the tracker said no" from "the call was
malformed" without matching on message strings, the way the CLI maps a refusal
to exit 123 and a real error to a traceback.
"""


class TtError(Exception):
    """Base for every error the client raises."""


class TtRefused(TtError):
    """The tracker declined a write: a bad payload, an unknown ref, or a row
    whose state says no. Carries the tracker's own reason."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class TtNotFound(TtError):
    """A read addressed a row that does not exist."""
