"""The milestone domain: its type in ``models``, its reads in ``queries``, its
actions in ``actions`` and its public surface in ``api``. A milestone is a dated
checkpoint inside an epic — a thin exclusive container of name and optional due
date, with no status of its own: its progress is derived from its issues, never
stored, so it has no ``enums`` module."""

from tt.domains.milestone.models import Milestone

__all__ = ["Milestone"]
