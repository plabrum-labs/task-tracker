"""A tag's wire contract: the payloads its actions accept, and the shape its list
read returns.

``createTag`` and ``rename`` both carry a single name; ``CreateTagPayload`` mirrors
``CreateProjectPayload`` as the top-level create whose uniqueness is read in
``execute``. A tag has no detail view of its own — it is a label, listed and
attached — so there is only a list-item shape.
"""

from pydantic import BaseModel, ConfigDict, Field


class CreateTagPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="The tag's name, unique across live tags.")


class RenameTagPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="The tag's new name, unique across live tags.")


class TagListItem(BaseModel):
    id: int
    name: str
