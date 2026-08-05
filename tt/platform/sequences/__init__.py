"""Per-project business sequences: the counter table and the one allocation call.

Ported from snacks' ``sequences`` package. ``next_number`` is what a create action
calls to stamp a new row's project-scoped ``number``.
"""

from tt.platform.sequences.models import Sequence
from tt.platform.sequences.service import next_number

__all__ = ["Sequence", "next_number"]
