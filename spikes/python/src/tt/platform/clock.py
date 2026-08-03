"""The one source of timestamps, so a write can stamp both columns alike.

RFC3339 UTC to the second, as text, because that is what SQLite sorts
lexicographically. A write computes ``now`` once and uses the result for
``created_at`` and ``updated_at`` both, which is what makes a freshly created
row carry one instant in both stamps.
"""

from datetime import UTC, datetime


def now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
