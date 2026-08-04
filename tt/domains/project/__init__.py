"""The project domain: its vocabulary in ``enums``, its types in ``models``, its
reads in ``queries``, its actions in ``actions`` and its public surface in
``api``."""

from tt.domains.project.enums import Status
from tt.domains.project.models import Project

__all__ = ["Project", "Status"]
