"""The epic domain: its vocabulary in ``enums``, its type in ``models``, its
reads in ``queries``, its actions in ``actions`` and its public surface in
``api``. An epic is the completable deliverable inside a permanent project — the
container that carries its own lifecycle status, which a project cannot."""

from tt.domains.epic.enums import Status
from tt.domains.epic.models import Epic

__all__ = ["Epic", "Status"]
