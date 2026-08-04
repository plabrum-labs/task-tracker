"""The action system: what an action is, and the small vocabulary around it.

The domains and the frontends import from here and not from the modules beneath:
``base`` is what an action is and the refusals and offers around it, ``deps`` the
seam an ``execute`` is handed, ``group`` the dispatcher a key runs through,
``registry`` the flat map behind it, and ``form`` the fields a payload derives.
"""

from tt.platform.actions.base import (
    REFUSALS,
    ActionResponse,
    Availability,
    Conflict,
    Empty,
    Invalid,
    ObjectAction,
    Offer,
    Offered,
    Refused,
    Runnable,
    TopLevelAction,
)
from tt.platform.actions.deps import ActionDeps
from tt.platform.actions.form import (
    Enum,
    Field,
    Kind,
    OptionalText,
    Text,
    decode,
    fields_of,
    name_of,
    payload,
)
from tt.platform.actions.group import ActionGroup
from tt.platform.actions.registry import ActionRegistry

__all__ = [
    "REFUSALS",
    "ActionDeps",
    "ActionGroup",
    "ActionRegistry",
    "ActionResponse",
    "Availability",
    "Conflict",
    "Empty",
    "Enum",
    "Field",
    "Invalid",
    "Kind",
    "ObjectAction",
    "Offer",
    "Offered",
    "OptionalText",
    "Refused",
    "Runnable",
    "Text",
    "TopLevelAction",
    "decode",
    "fields_of",
    "name_of",
    "payload",
]
